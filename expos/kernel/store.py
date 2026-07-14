"""RunStore：运行目录即运行时检查点（docs/ARCHITECTURE.md §4.4）。

- events.jsonl 追加式事件日志：状态迁移 / 裁决 / 改判 / 决策，永不覆盖历史；
  改判/翻案 = 追加新事件引用旧事件（OpenLineage facet 版本化模式）。
- checkpoint.json 原子写（tmp + rename），支持断点续跑。
- truth/ 为仿真真值 sidecar：本模块只做不透明落盘（公理 6），
  qc/models/planner/agent 一律禁读，ReadOnlyRunView 不暴露它。
- ReadOnlyRunView 在此定义并导出（依赖方向 agent→kernel，永不反向）：
  agent 层只拿它，没有任何写入口。
- 并发模型：**单写者**。一个运行目录同一时刻只允许一个 RunStore 写句柄
  （loop.py 持有）；seq 计数与文件写不做跨进程锁。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from expos.errors import ExposError
from expos.kernel.objects import (
    Actor,
    DecisionKind,
    DecisionRecord,
    ExperimentObject,
    ObservationObject,
    PROPOSAL_KINDS,
    TrustLevel,
    utc_now,
)

_EVENTS = "events.jsonl"
_CHECKPOINT = "checkpoint.json"
_CONFIG = "config.json"

_log = logging.getLogger("expos.kernel.store")


#: DECISION_FACE_KINDS.v1 — the versioned whitelist the "decision face bitwise equal"
#: comparison is defined over (docs/M18_LLM_LIVE_SMOKE.md §2, letter 094 §2). Promoted
#: from tests/test_agent_backend_switch.py to an importable kernel constant so producers
#: and consumers share ONE authority (Phase 4 item #5). ``agent_shadow_proposal`` is
#: CONSTRUCTIVELY EXCLUDED (its usage/latency/response-id are non-deterministic —
#: including it would make bitwise equality impossible). VERSION DISCIPLINE: changing the
#: MEMBERSHIP is a new version constant (DECISION_FACE_KINDS_V2, …), NEVER mutate v1 in
#: place — a frozen set pinned to a run's provenance must stay reproducible.
DECISION_FACE_KINDS_V1: frozenset[str] = frozenset(
    {"knowledge_updated", "decision", "promotion_decision", "run_stop"}
)

#: EMISSION-dedup scope (Phase 4 item #1, blueprint §Convergence b) — distinct from the
#: surface-COMPARISON whitelist above. A resumed/redone round re-emits these decision-face
#: kinds; ``append_decision_face_event`` guards them for exactly-once (same dedup key + same
#: content fingerprint => idempotent skip; same key + DIFFERENT fingerprint => loud
#: NondeterminismError). Covers ``claim_decision`` (not in the comparison whitelist, which is
#: intentionally free of non-deterministic fields and needs no dedup of it). Version-frozen
#: with the same discipline as DECISION_FACE_KINDS_V1.
DEDUP_GUARDED_KINDS_V1: frozenset[str] = frozenset(
    {"knowledge_updated", "promotion_decision", "claim_decision"}
)


class StoreError(ExposError):
    """事件日志物理损坏（中间行坏 / seq 回退或跳跃）——非崩溃尾，响亮拒读。

    user_facing=False：这类损坏意味着并发写、手工篡改或磁盘损伤，属不变量破坏，
    CLI 不得静默吞掉，须保留 traceback（对齐 errors.py 的"bug 不许静默"红线）。
    torn-tail（仅物理最后一行半写）不走本异常——那是单写者崩溃的正常残留，容忍丢弃。"""

    user_facing = False


class NondeterminismError(StoreError):
    """A decision-face event re-emission carries the SAME dedup key but a DIFFERENT
    content fingerprint than an already-logged event (Phase 4 item #1). A resumed/redone
    round did NOT reproduce its decision bitwise — a real nondeterminism/forking bug, never
    silently resolved by picking one (Temporal NON_DETERMINISTIC_ERROR precedent, INDEX_REF_X
    §T-2). Loud by construction (user_facing=False: a bug, keep the traceback)."""

    user_facing = False


class ReadOnlyRunView(BaseModel):
    """交给 Agent Orchestrator 的只读快照。

    结构性保证：frozen（属性赋值即抛错）、无任何 save/append/write/delete API、
    不含 truth sidecar。内容是从磁盘加载的副本，改动副本不影响存储。
    """

    model_config = ConfigDict(frozen=True)

    run_root: str
    exported_at: str
    experiments: tuple[ExperimentObject, ...] = ()
    observations: tuple[ObservationObject, ...] = ()
    decisions: tuple[DecisionRecord, ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    checkpoint: dict[str, Any] | None = None

    def observations_by_trust(self, trust: TrustLevel) -> tuple[ObservationObject, ...]:
        return tuple(o for o in self.observations if o.trust == trust)

    def decisions_of(self, kind: DecisionKind) -> tuple[DecisionRecord, ...]:
        return tuple(d for d in self.decisions if d.kind == kind)


class RunStore:
    # Bounded retry for transient per-file OSError (EIO/ESTALE on flaky NFS mounts) while
    # reading a single observation file. Class attributes (not module constants) so tests
    # can monkeypatch them per-instance/per-class without touching production defaults.
    # Only single-file reads are retried here — directory-level OSError (glob/listdir on an
    # unreadable observations/) is NOT covered and must keep failing loudly (env-level fault).
    _OBS_READ_MAX_ATTEMPTS = 3
    _OBS_READ_RETRY_BACKOFF_S = 0.05

    def __init__(
        self,
        root: str | Path,
        create: bool = True,
        lock: bool = False,
        cache_observations: bool = False,
    ):
        self.root = Path(root)
        if create:
            for sub in ("experiments", "observations", "truth", "models", "report"):
                (self.root / sub).mkdir(parents=True, exist_ok=True)
        elif not self.root.is_dir():
            raise FileNotFoundError(f"运行目录不存在: {self.root}")
        self._seq = self._recover_next_seq()
        self._tail_healed = False  # 首次 append 前惰性愈合 torn tail（写路径，单写者）
        self._decision_ids: set[str] | None = None  # append_decision 幂等去重集（惰性建，Q-8）
        # ---- decision-face emission dedup index (Phase 4 item #1): {(kind, *dedup_key) ->
        # content_fingerprint} for the DEDUP_GUARDED_KINDS_V1. Lazily rebuilt from the event
        # log on the first guarded emit (so a resume that inherits a crashed round's
        # decision-face events dedups against them); None = not yet built.
        self._decision_face_index: dict[tuple[Any, ...], str | None] | None = None
        # ---- loop 内存态观测缓存（M-2 热径修复）：os 臂每轮 9 次 list_observations
        # 全量磁盘重扫（planner 扇出 6 + qc 1 + loop 2），48 孔板每 run 累计 1.9-4.4s、
        # 384 孔板末轮单轮 3-8s。cache_observations=True 时 list_observations 走内存缓存
        # （O(N) glob+反序列化 → O(1) 命中），save_observation 落盘同步维护缓存条目。
        # **一致性红线**：缓存 = 落盘的严格镜像。所有观测写路径都汇入 save_observation
        # （route_observation / reclassify / NaivePolicy / QCPolicy 归因回存 / save_observations
        # 批量），故写路径全覆盖；唯一绕过 save_observation 的直改盘是 reconcile_redo_rounds
        # 的直接 unlink，其后**强制失效重建**。缓存条目由落盘同一份 JSON 反序列化而来
        # （逐字段等同磁盘读回值）。默认关闭：非 loop 写者（cli/eval 只读句柄）行为零变化。
        # list_observations() never returns the cached instances themselves — each hit
        # re-deserializes fresh copies from the cached JSON payload (exactly what the
        # cache-off path does with the on-disk bytes), so an unsaved in-place mutation by
        # a reader cannot corrupt the cache (G1, see list_observations for detail).
        self._cache_observations = cache_observations
        # Cache entry: obs_id -> (parsed object used for filtering only, canonical JSON
        # payload used to rehydrate the returned copies). 惰性建；None=未建/已失效。
        self._obs_cache: dict[str, tuple[ObservationObject, str]] | None = None
        # ---- 物化视图故障隔离（OS3 §一）：单个坏 obs 文件（非 UTF-8/坏 JSON/校验失败）
        # 不得使 status/verdicts/UI/export_view/--resume 裸 traceback DoS 全 run。
        # list_observations/缓存重建遇坏文件 → 隔离该文件（登记于此 + logging.error 响亮列名）
        # 并继续返回其余。写者路径（resume 重建）同样隔离不炸，但由 loop.py 首轮落
        # view_quarantine 事件 + warning 防静默。{path: "ErrClass: msg"}；每次全量扫描刷新。
        # Transient per-file OSError (EIO/ESTALE) is also quarantined this way, but only
        # after bounded retry (G2, see _read_file_text_with_retry) — a directory-level
        # OSError (observations/ itself unreadable) still propagates loudly, unquarantined.
        self.quarantined_files: dict[str, str] = {}
        # 跨进程单写者护栏（M-4b）：lock=True 时对 writer.lock 取非阻塞排他 flock。
        # 两个 `--resume` 同目录并发时，后到者取锁失败→响亮 StoreError，绝不交错事件日志/
        # checkpoint（单写者是 seq/torn-tail/原子写全部前置假设）。默认 False 使只读/多句柄
        # 读路径不受影响；仅 loop.py 的写句柄开锁。OS 在进程退出/fd 关闭时自动释放。
        self._lock_fd: int | None = None
        if lock:
            self._acquire_writer_lock()

    # ---------------------------------------------------------- 跨进程写锁（M-4b）

    def _acquire_writer_lock(self) -> None:
        lock_path = self.root / "writer.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise StoreError(
                f"运行目录 {self.root} 的 writer.lock 已被另一写者持有——拒绝并发写"
                "（两个 --resume 同目录会交错事件日志/checkpoint，破坏单写者不变量）"
            ) from e
        self._lock_fd = fd

    def release_writer_lock(self) -> None:
        """释放写锁（幂等）。loop.py 正常收口/续跑早返回前调用，令同进程后续写句柄可再取。
        进程退出或 fd 关闭时 OS 亦自动释放，故崩溃路径无需显式调用。"""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    # ---------------------------------------------------------- 事件日志

    def _events_path(self) -> Path:
        return self.root / _EVENTS

    @staticmethod
    def _parse_line(line: str) -> Any | None:
        """行有效性谓词——torn-tail 三方（_recover_next_seq / _heal_torn_tail /
        read_events）共用的**单一判据源**：JSON 可解析 → 返回解析结果（有效记录）；
        不可解析 → None（仅当其为物理末行时才算崩溃半写残留 torn tail）。

        关键：**缺尾换行但可解析的末行是有效记录，不是 torn tail**——崩溃可以恰好
        落在"记录字节已全部落盘、结尾 \\n 未落"之间。此前 heal 用"不以 \\n 结尾"
        判 torn 而 seq 恢复用"可解析"判有效，两判据互斥：末行被 heal 截掉、seq 却
        已计入 → 追加后 seq 空洞 → read_events 恒抛（R1-5(a) 崩溃一致性缺口）。"""
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _recover_next_seq(self) -> int:
        """恢复下一条事件的 seq（item 3：从现文件末条有效记录 +1 恢复）。

        对 torn tail 容错（末行半写不计入，判据=_parse_line）；对中间坏行宽容跳过
        （真正的响亮拒读留给 read_events 在实际读取时做）。有显式 seq 者以"末条有效
        seq + 1"续接；旧 run 无 seq 字段者退化为"有效记录条数"（向后兼容）。"""
        p = self._events_path()
        if not p.exists():
            return 0
        # Split on the LF byte ONLY (matching the writer's "\n" delimiter and
        # scan_events_tail's byte-level find(b"\n")). str.splitlines() would also
        # break on U+0085/U+2028/U+2029, which json.dumps(ensure_ascii=False)
        # emits RAW inside payload strings -- over-splitting one logical event
        # into several physical "lines" (P1 property-batch counterexample).
        lines = [line for line in p.read_text(encoding="utf-8").split("\n") if line.strip()]
        last_seq: int | None = None
        count = 0
        for i, line in enumerate(lines):
            rec = self._parse_line(line)
            if rec is None:
                if i == len(lines) - 1:
                    break  # torn tail：末行半写，不计入
                continue   # 中间坏行：恢复期宽容跳过，read_events 会响亮拒读
            count += 1
            s = rec.get("seq")
            if s is not None:
                last_seq = s
        return (last_seq + 1) if last_seq is not None else count

    def _heal_torn_tail(self) -> None:
        """写入前愈合 torn tail，判据与 _recover_next_seq / read_events 同一谓词
        （_parse_line）：

        - 末行 **JSON 可解析但缺尾换行**（崩溃恰在记录字节落盘后、"\\n" 前）→
          它是 seq 恢复已计入的**有效记录**，只补一个换行、绝不截断——截断会制造
          seq 空洞，使 read_events 单调校验恒抛（R1-5(a)）；
        - 末行 **JSON 不可解析** → 真半写残留，截掉并告警。与 PG-WAL "截断残缺
          尾部再重放"同构。只在写路径（单写者持有）调用，读路径不改盘。"""
        p = self._events_path()
        if not p.exists():
            return
        data = p.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        idx = data.rfind(b"\n")
        tail = data[idx + 1:]
        if self._parse_line(tail.decode("utf-8", errors="replace")) is not None:
            with p.open("ab") as f:
                f.write(b"\n")  # 完整记录只缺换行：补齐即愈合，记录不丢、seq 不空洞
            _log.warning("torn_tail: %s 末行完整但缺尾换行（崩溃点），已补换行、未截断", p)
            return
        with p.open("wb") as f:
            f.write(data[: idx + 1] if idx >= 0 else b"")
        _log.warning("torn_tail: append 前截断 %s 末行半写残留（崩溃点）", p)

    def append_event(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """追加一条事件并返回它。事件日志只增不改。

        **单行原子写**：整条记录先序列化为一个字符串（含结尾换行），再**一次**
        `write` + `flush`。单写者崩溃只可能截在这一次 write 中途 → 至多留一行半写的
        torn tail（末行），绝不产生跨行交错；read_events 侧对该 torn tail 容错。"""
        if not self._tail_healed:
            self._heal_torn_tail()  # 恢复后首写前，去掉上次崩溃的半写尾，保证追加后仍连续可读
            self._tail_healed = True
        record = {"seq": self._seq, "ts": utc_now(), "kind": kind, "payload": payload}
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._events_path().open("a", encoding="utf-8") as f:
            f.write(line)  # 一次 write：整行原子落盘
            f.flush()
        self._seq += 1
        return record

    @staticmethod
    def _event_line_sha256(record: dict[str, Any]) -> str:
        """Content hash of one event record, stable across a write/read round-trip
        (Phase 4 item #1 forked-resume detection). Canonicalized (sort_keys + default=str)
        so it does NOT depend on dict key order — the value computed at write time (over the
        just-appended record) equals the value recomputed on resume over the parsed record,
        while a single tampered byte in the logged payload moves it."""
        return hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def _decision_face_key_fp(
        kind: str, payload: dict[str, Any]
    ) -> tuple[tuple[Any, ...], str | None] | None:
        """Map a decision-face event to its ``(dedup_key, content_fingerprint)`` (blueprint
        §Convergence b), or None if the kind is not dedup-guarded. Keys:
          * knowledge_updated  -> (round_id,)            + knowledge fingerprint;
          * promotion_decision -> (round_id,)            + canonical-content fingerprint;
          * claim_decision     -> (round_id, claim_id)   + provenance fingerprint.
        Read from the ALREADY-LOGGED payload so the index rebuilds from the log alone."""
        if kind == "knowledge_updated":
            return ((payload.get("round_id"),), payload.get("fingerprint"))
        if kind == "promotion_decision":
            return ((payload.get("round_id"),), payload.get("content_fingerprint"))
        if kind == "claim_decision":
            return (
                (payload.get("round_id"), payload.get("claim_id")),
                payload.get("provenance_fingerprint"),
            )
        return None

    def append_decision_face_event(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedup_key: tuple[Any, ...],
        content_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Append a DEDUP_GUARDED_KINDS_V1 decision-face event with resume-idempotent
        exactly-once semantics (Phase 4 item #1). Consults a per-``(kind, *dedup_key)`` index
        of already-logged content fingerprints (lazily rebuilt from the log on first use):

          * key absent             -> append normally, index it, return the record;
          * key present, fp EQUAL  -> idempotent skip (a resumed/redone round reproduced the
            SAME decision — the I5 double-emission bullseye); debug-note it, append nothing,
            return None;
          * key present, fp DIFFER -> raise :class:`NondeterminismError` (the redo diverged;
            never silently pick one — Temporal T-2 non-determinism-is-first-class discipline).

        The emit helpers (``emit_knowledge_updated`` / ``emit_promotion_decision`` /
        ``emit_claim_decision``) route here; direct ``append_event`` of a guarded kind would
        bypass the index and is a wiring bug."""
        if kind not in DEDUP_GUARDED_KINDS_V1:
            raise StoreError(
                f"append_decision_face_event called for non-guarded kind {kind!r} "
                f"(expected one of {sorted(DEDUP_GUARDED_KINDS_V1)})"
            )
        if self._decision_face_index is None:
            index: dict[tuple[Any, ...], str | None] = {}
            for ev in self.read_events():
                entry = self._decision_face_key_fp(
                    ev.get("kind", ""), ev.get("payload") or {}
                )
                if entry is not None:
                    index[(ev["kind"], *entry[0])] = entry[1]
            self._decision_face_index = index
        full_key = (kind, *dedup_key)
        existing = self._decision_face_index.get(full_key)
        if full_key in self._decision_face_index:
            if existing == content_fingerprint:
                _log.debug(
                    "decision_face_dedup: idempotent skip %s key=%s "
                    "(resume/redo reproduced the decision bitwise)",
                    kind, full_key,
                )
                return None
            raise NondeterminismError(
                f"decision-face re-emission diverged: {kind} key={full_key} — logged "
                f"content fingerprint {existing!r} != re-derived {content_fingerprint!r}. "
                "A resumed/redone round did NOT reproduce this decision bitwise "
                "(forked/nondeterministic history); refusing to silently pick one."
            )
        record = self.append_event(kind, payload)
        self._decision_face_index[full_key] = content_fingerprint
        return record

    #: R5 REF-1 P1-2: per-kind required payload keys (transport-intact but
    #: payload-corrupt streams previously hit consumers as random KeyErrors or,
    #: worse, folded silently -- e.g. a typo'd grade read as inactive). Registry
    #: covers the kinds whose payloads are indexed by downstream readers; kinds
    #: absent from the registry are not checked (additive, zero regression).
    EVENT_PAYLOAD_REQUIRED: dict[str, set[str]] = {
        "routing": {"obs_id"},
        "action_consumed": {"item_uid", "round_id"},
        "redo_reconciliation": {"from_round"},
        "run_stop": {"exit_status"},
        # grade intentionally NOT required here: a missing grade is the legal
        # old format (Gen-2 pre-observation-surface events); its VALUE legality
        # is judged by the budget layer (grade_stream), one event one layer one
        # verdict (letter 040 P3 tension fix).
        "risk_map_applied": {"round_id"},
        "aggregation_alpha": {"round_id"},
        "reclassification": {"obs_id", "to_trust"},
        "learning_weight_assigned": {"round_id", "entries"},
        "knowledge_updated": {"round_id", "fingerprint", "n_hypotheses", "n_claims"},
        "promotion_decision": {"round_id", "knowledge_fingerprint", "promoted",
                               "denied"},
        "claim_decision": {"round_id", "claim_id", "claim_version",
                           "decision_status", "decision_fn_id",
                           "input_observation_ids", "statistic", "power",
                           "consumed_knowledge_fingerprint"},
        # Phase 4 item #1: the wet-leg non-replay marker (write-strict from birth — a NEW
        # kind, so no legacy tolerance is needed; docs/EVENT_SCHEMA.md §1+§4).
        "wet_leg_issued": {"round_id", "exp_id", "n_wells"},
        # M18 agent-backend switch (letters 086 §2 + 094/095 amendment): the shadow-mode
        # parallel-LLM audit event and the llm-mode generation-failure event.
        "agent_shadow_proposal": {"round_id", "schema_valid", "fingerprint_match",
                                  "basis_subset", "order_diff", "usage",
                                  "prompt_sha256", "validator_versions"},
        "agent_generation_failed": {"round_id", "failure_kind", "attempts",
                                    "usage", "prompt_sha256"},
        # M23 Phase 1 (letter 128): the physical-action transaction facet's
        # transition event. Write-strict from birth -- a NEW kind, deliberately
        # NOT in ADDITIVE_SINCE (no legacy logs can carry it; ruling red 122).
        "physical_action_transition": {"action_id", "round_id", "to"},
    }

    #: WRITE-STRICT / READ-TOLERANT additive-key registry (Phase 4 item #5). Keys added to a
    #: kind's required set AFTER that kind first shipped: new emissions must carry them (the
    #: emit helper's mandatory kwarg is the write gate), but historical run logs written before
    #: the addition are IMMUTABLE EVIDENCE and must still validate on READ — schema additions
    #: never retroactively invalidate signed-off logs (append-only-evidence discipline). The
    #: default (read-tolerant) validate path subtracts these from the missing-key set; a
    #: strict caller (legacy_tolerant=False, the new-run/write-era gate) enforces the full set.
    #: NEVER add a PRE-EXISTING required key here (that would weaken a real contract).
    ADDITIVE_SINCE: dict[str, set[str]] = {
        # knowledge_updated gained round_id in Phase 4; pre-Phase-4 logs (runs/corun_*,
        # runs/llm_smoke_stage3, …) legitimately lack it and must read-validate clean.
        "knowledge_updated": {"round_id"},
    }

    def validate_event_payloads(
        self, events: list[dict[str, Any]], *, legacy_tolerant: bool = True
    ) -> list[dict[str, Any]]:
        """Collect payload-structure violations (never raises -- REF-1 opt-out
        form): for each event whose kind is in EVENT_PAYLOAD_REQUIRED, report
        missing required keys and non-dict payloads. Consumed by `expos check`
        (default ON there); regular readers stay opt-in with zero regression.

        ``legacy_tolerant`` (default True, the READ era) tolerates keys in ``ADDITIVE_SINCE``
        being absent from an event: historical logs written before an additive key shipped are
        immutable evidence and still validate. ``legacy_tolerant=False`` is the WRITE/new-run
        era — the full required set is enforced so a fresh emission missing an additive key
        fails loudly (write-strict). PRE-EXISTING required keys are enforced in BOTH eras."""
        violations: list[dict[str, Any]] = []
        for e in events:
            kind = e.get("kind", "")
            req = self.EVENT_PAYLOAD_REQUIRED.get(kind)
            if req is None:
                continue
            if legacy_tolerant:
                # Read era: an event missing an ADDITIVE_SINCE key is legacy-tolerated (it
                # predates the addition); every pre-existing required key still enforced.
                req = req - self.ADDITIVE_SINCE.get(kind, set())
            payload = e.get("payload")
            if not isinstance(payload, dict):
                violations.append({"seq": e.get("seq"), "kind": e.get("kind"),
                                   "problem": "payload_not_a_dict"})
                continue
            missing = sorted(req - payload.keys())
            if missing:
                violations.append({"seq": e.get("seq"), "kind": e.get("kind"),
                                   "problem": "missing_keys", "keys": missing})
        return violations

    def read_events(
        self, kind: str | None = None, validate: bool = False
    ) -> list[dict[str, Any]]:
        """读事件，带两条恢复期鲁棒性（EVENT_SCHEMA §0.1）：

        1. **torn-tail 容错**：单写者崩溃可能在 events.jsonl **物理最后一行**留半行 JSON。
           仅当解析失败的是最后一条非空行时，按"崩溃点残留"跳过并 logging.warning 告警
           （含文件与行号，不静默）；**中间行**解析失败是真损坏（非崩溃尾），响亮抛
           StoreError。与 PG-WAL"重放到最后一条完整记录、截断残缺尾部"同构。
        2. **seq 显式单调校验**：present 的 seq 必须 0..N 连续递增；回退/重复/跳跃一律
           抛 StoreError（指明疑似并发写或手工篡改）。校验跨全部行（先于 kind 过滤）。
           **向后兼容**：旧 run 无 seq 字段的行按行序补虚拟 seq（0 起），不参与跳跃校验、
           不抛（PG pg_upgrade "reuse old data files"精神）。"""
        p = self._events_path()
        if not p.exists():
            return []
        # 收集非空行 + 其 1-based 物理行号；torn tail 只可能是最后一条非空行。
        # Line semantics: split on the LF byte ONLY -- the writer delimits with
        # "\n" and scan_events_tail scans find(b"\n"); str.splitlines() would
        # additionally break on U+0085/U+2028/U+2029 which appear RAW in
        # json.dumps(ensure_ascii=False) payload text, over-splitting a logical
        # event (P1 property-batch counterexample; keeps the two readers and the
        # health surface agreeing on identical bytes).
        content: list[tuple[int, str]] = [
            (lineno, line)
            for lineno, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1)
            if line.strip()
        ]
        if not content:
            return []
        last_lineno = content[-1][0]
        out: list[dict[str, Any]] = []
        last_seq: int | None = None
        for pos, (lineno, line) in enumerate(content):
            rec = self._parse_line(line)  # 与 _recover_next_seq/_heal_torn_tail 同一谓词
            if rec is None:
                if lineno == last_lineno:
                    _log.warning(
                        "torn_tail: 跳过 %s 末行半写记录（行 %d，单写者崩溃残留）",
                        p, lineno,
                    )
                    continue
                raise StoreError(
                    f"events.jsonl 中间行损坏（非崩溃尾，疑似磁盘损伤或篡改）: {p} 行 {lineno}"
                )
            seq = rec.get("seq")
            if seq is None:
                rec["seq"] = pos  # 旧 run 兼容：按行序补虚拟 seq，不参与跳跃校验
            else:
                if last_seq is not None:
                    if seq <= last_seq:
                        raise StoreError(
                            f"事件 seq 回退/重复: 前 {last_seq} → 现 {seq}"
                            f"（{p} 行 {lineno}）——疑似并发写或手工篡改"
                        )
                    if seq != last_seq + 1:
                        raise StoreError(
                            f"事件 seq 跳跃: 前 {last_seq} → 现 {seq}，缺 {seq - last_seq - 1} 条"
                            f"（{p} 行 {lineno}）——疑似并发写或手工篡改"
                        )
                last_seq = seq
            if kind is None or rec["kind"] == kind:
                out.append(rec)
        # REF-1 P1-2 opt-in gate (default OFF => zero behavior change for all
        # existing readers). When ON, violations are collected -- never raised --
        # and exposed on last_payload_violations for tooling (`expos check`).
        self.last_payload_violations = (
            self.validate_event_payloads(out) if validate else []
        )
        return out

    # ---------------------------------------------------------- 尾损诊断与自愈（expos check）

    def scan_events_tail(self) -> dict[str, Any]:
        """只读诊断 events.jsonl 尾部损伤（redis-check-aof 骨架 + SQLite walIndexRecover
        "遇第一个无效帧即 break、不跳帧打捞"纪律）。判据复用 _parse_line（与 seq 恢复 /
        torn-tail 愈合 / read_events 同一谓词，勿重复实现）。返回诊断报告（双坐标：字节偏移
        `valid_up_to_byte` + 行号 `valid_up_to_line`）：

        - status="clean"：全部非空行有效 → 无损伤；
        - status="truncated"：第一个坏行**恰是最后一条非空行**（其后直达 EOF）→ 干净尾截断，
          可自愈（末行半写 / 末行坏 JSON 两类都在此）；
        - status="corrupt"：第一个坏行之后仍有非空行 → 中段损坏，**结构性拒修**（水位后未直达
          EOF；绝不把中段损坏伪装成尾损打捞）。

        纯只读、不改盘（自愈须显式走 truncate_events_tail）。"""
        p = self._events_path()
        base = {"exists": p.exists(), "size": 0, "status": "clean",
                "valid_up_to_byte": 0, "valid_up_to_line": 0,
                "first_bad_line": None, "n_lines": 0}
        if not p.exists():
            return base
        data = p.read_bytes()
        base["size"] = len(data)
        # 逐物理行切分并留字节偏移（含结尾换行的下一行起点 nxt）
        nonblank: list[tuple[int, int, int, bytes]] = []  # (line_no, start, nxt, seg)
        cur = line_no = 0
        n = len(data)
        while cur < n:
            nl = data.find(b"\n", cur)
            end = n if nl == -1 else nl
            nxt = n if nl == -1 else nl + 1
            line_no += 1
            seg = data[cur:end]
            if seg.strip():
                nonblank.append((line_no, cur, nxt, seg))
            cur = nxt
        base["n_lines"] = line_no
        first_bad_idx: int | None = None
        for i, (ln, start, nxt, seg) in enumerate(nonblank):
            if self._parse_line(seg.decode("utf-8", errors="replace")) is None:
                first_bad_idx = i
                break  # 遇第一个坏行即停，不跳行打捞（walIndexRecover 纪律）
            base["valid_up_to_byte"] = nxt
            base["valid_up_to_line"] = ln
        if first_bad_idx is None:
            base["status"] = "clean"
            base["valid_up_to_byte"] = len(data)
        else:
            base["first_bad_line"] = nonblank[first_bad_idx][0]
            # 水位后必须直达 EOF 才许截（结构约束）：第一个坏行须是最后一条非空行
            base["status"] = ("truncated" if first_bad_idx == len(nonblank) - 1
                              else "corrupt")
        return base

    def truncate_events_tail(self, scan: dict[str, Any]) -> Path:
        """按 scan 报告把 events.jsonl 截到最后有效记录水位（备份原文件到 `.pre_fix`）。
        **仅 status=="truncated" 才执行**；clean 无需修、corrupt 结构性拒修——传入非
        truncated 报告一律响亮抛 StoreError（防误截中段损坏）。写路径，调用方（cli check
        --fix）已做交互确认/--yes 旁路。"""
        if scan.get("status") != "truncated":
            raise StoreError(
                f"truncate_events_tail 仅修 truncated 尾损，收到 status={scan.get('status')!r}"
                "——中段损坏(corrupt)结构性拒修，clean 无需修"
            )
        p = self._events_path()
        data = p.read_bytes()
        backup = p.with_suffix(p.suffix + ".pre_fix")
        backup.write_bytes(data)
        watermark = scan["valid_up_to_byte"]
        with p.open("wb") as f:
            f.write(data[:watermark])
        _log.warning(
            "expos_check_fix: 截断 %s 尾损残尾（水位 byte=%d/line=%d，坏行=%s），原文件备份至 %s",
            p, watermark, scan["valid_up_to_line"], scan.get("first_bad_line"), backup,
        )
        return backup

    # ---------------------------------------------------------- 物化视图诊断（expos check）

    def scan_view_files(self) -> dict[str, Any]:
        """只读扫描物化视图（observations/ + experiments/）逐文件可解析性（OS3 §一(b)）：
        坏文件（非 UTF-8 / 坏 JSON / 校验失败）列入诊断（路径 → 错误类别），不炸不改盘。

        与事件日志尾损诊断（scan_events_tail）互补：现对坏 obs 文件 check 报 clean exit 0
        全盲，本方法补齐——供 cli check 消费，有坏视图文件即"可诊断问题非 clean"。"""
        bad: dict[str, str] = {}
        for sub, model in (
            ("observations", ObservationObject),
            ("experiments", ExperimentObject),
        ):
            d = self.root / sub
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    model.model_validate_json(p.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, ValidationError, json.JSONDecodeError) as e:
                    bad[str(p)] = type(e).__name__
        return {"n_bad": len(bad), "bad_files": bad}

    def scan_view_health(self) -> dict[str, Any]:
        """视图健康分区（OS3/用户架构裁决 P0，mailbox 020）：六项物化视图各报一个状态
        healthy | stale | quarantined | missing，纯只读、不改盘。复用已有 quarantine 机制。

        - events：复用 scan_events_tail（clean→healthy / truncated→stale / corrupt→quarantined）；
        - observations / experiments：坏文件→quarantined（已被 list 隔离不 DoS），否则 healthy；
        - score：report/score.json 缺→missing（incomplete）、旧于 events.jsonl→stale（重跑未重评）、坏→quarantined；
        - lineage：report/training_members.json 缺→missing（lineage incomplete）、坏→quarantined；
        - snapshot：models/snapshot_r*.json 坏→quarantined（model unavailable 降级，**不影响 raw event replay**）、无→missing。

        overall：任一项 stale/quarantined → degraded（"stale/incomplete 不得装正常"）；missing 为
        可缺的前向/评测产物（advisory，不单独置 degraded）。runs_index 是 rebuildable cache 非真相源，
        坏则直接重建，不在本健康分区内评判（见 docs/RUNS_INDEX_DESIGN.md §7）。"""
        def _json_ok(p: Path) -> bool:
            json.loads(p.read_text(encoding="utf-8"))
            return True

        sections: dict[str, dict[str, Any]] = {}

        es = self.scan_events_tail()
        ev_map = {"clean": "healthy", "truncated": "stale", "corrupt": "quarantined"}
        sections["events"] = {
            "status": "missing" if not es["exists"] else ev_map.get(es["status"], "quarantined"),
            "detail": f"{es['n_lines']} lines, tail={es['status']}",
        }

        view = self.scan_view_files()
        for sub in ("observations", "experiments"):
            d = self.root / sub
            bad = sorted(p for p in view["bad_files"] if Path(p).parent.name == sub)
            n_files = len(list(d.glob("*.json"))) if d.is_dir() else 0
            st = "missing" if not d.is_dir() else ("quarantined" if bad else "healthy")
            sections[sub] = {"status": st, "detail": f"{n_files} files, {len(bad)} quarantined",
                             "bad_files": bad}

        score_p, ev_p = self.root / "report" / "score.json", self._events_path()
        if not score_p.exists():
            sections["score"] = {"status": "missing", "detail": "report/score.json 不存在（评测未跑/未完成）"}
        else:
            try:
                _json_ok(score_p)
                stale = (ev_p.exists()
                         and score_p.stat().st_mtime_ns < ev_p.stat().st_mtime_ns)
                sections["score"] = ({"status": "stale", "detail": "score.json 旧于 events.jsonl（重跑后未重评）"}
                                     if stale else {"status": "healthy", "detail": "present"})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
                sections["score"] = {"status": "quarantined", "detail": type(e).__name__}

        lin_p = self.root / "report" / "training_members.json"
        if not lin_p.exists():
            sections["lineage"] = {"status": "missing",
                                   "detail": "training_members.json 不存在（lineage incomplete）"}
        else:
            try:
                _json_ok(lin_p)
                sections["lineage"] = {"status": "healthy", "detail": "present"}
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
                sections["lineage"] = {"status": "quarantined", "detail": type(e).__name__}

        snap_d = self.root / "models"
        snaps = sorted(snap_d.glob("snapshot_r*.json")) if snap_d.is_dir() else []
        if not snaps:
            sections["snapshot"] = {"status": "missing", "detail": "无模型快照（未训练/未完成轮次）"}
        else:
            bad_snap = []
            for p in snaps:
                try:
                    _json_ok(p)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    bad_snap.append(str(p))
            sections["snapshot"] = {
                "status": "quarantined" if bad_snap else "healthy",
                "detail": f"{len(snaps)} snapshots, {len(bad_snap)} corrupt"
                          "（model 降级但 raw event replay 不受影响）",
                "bad_files": sorted(bad_snap),
            }

        overall = ("degraded"
                   if any(s["status"] in ("stale", "quarantined") for s in sections.values())
                   else "healthy")
        return {"overall": overall, "sections": sections}

    # ---------------------------------------------------------- 决策载荷

    def append_decision(self, record: DecisionRecord) -> DecisionRecord:
        """追加一条决策载荷。**同 decision_id 二次落盘一律响亮拒读**（Q-8 重复提交双计）：

        decision_id 是决策的审计身份；同 id 重复落盘会使 unresolved_proposals /
        _resolutions 按 list_decisions 遍历时双计（提案计两次、裁定覆盖两次）。选"拒绝"而非
        "幂等吞掉"——重复 append 是调用方 bug（提案本应每次新铸 id），静默吞会掩盖它。
        单写者内存去重集，resume 时从盘惰性重建（首次调用扫 read_events("decision")）。"""
        if self._decision_ids is None:
            self._decision_ids = {
                did
                for ev in self.read_events("decision")
                if (did := ev["payload"].get("decision_id")) is not None
            }
        if record.decision_id in self._decision_ids:
            raise StoreError(
                f"重复 decision_id={record.decision_id} 二次落盘——决策 id 是审计身份，"
                "重复会使提案/裁定双计（Q-8）；提案应每次新铸 id"
            )
        # usage-必键 方案 A (letters 080/086 §3): a KNOWLEDGE-GATED agent proposal (MCL
        # template proposal + M18 LLM proposal — the ones carrying a knowledge_fingerprint)
        # must carry a ``usage`` accounting block. Key PRESENCE is the contract (a provider
        # not honouring usage is a legal degradation, empty dict allowed); its absence is a
        # wiring bug and is refused at the write point ("出生即治理"). Scoped to knowledge-
        # gated proposals so the single-leg loop's non-gated proposals are unaffected.
        content = record.content if isinstance(record.content, dict) else {}
        if (record.actor == Actor.AGENT and record.kind in PROPOSAL_KINDS
                and "knowledge_fingerprint" in content and "usage" not in content):
            raise StoreError(
                f"agent proposal {record.decision_id} carries knowledge_fingerprint but no "
                "'usage' block — knowledge-gated proposals must account usage (方案 A, "
                "letter 080/086); a provider not honouring usage is a legal degradation "
                "(empty dict), but the key must be present"
            )
        self.append_event("decision", record.model_dump(mode="json"))
        self._decision_ids.add(record.decision_id)
        return record

    def list_decisions(
        self,
        kind: DecisionKind | None = None,
        actor: Actor | None = None,
    ) -> list[DecisionRecord]:
        out = []
        for ev in self.read_events("decision"):
            rec = DecisionRecord.model_validate(ev["payload"])
            if kind is not None and rec.kind != kind:
                continue
            if actor is not None and rec.actor != actor:
                continue
            out.append(rec)
        return out

    # ---------------------------------------------------------- 两个内核对象

    def save_experiment(self, exp: ExperimentObject) -> Path:
        path = self.root / "experiments" / f"{exp.exp_id}.json"
        self._atomic_write_text(path, exp.model_dump_json(indent=2))
        return path

    def load_experiment(self, exp_id: str) -> ExperimentObject:
        path = self.root / "experiments" / f"{exp_id}.json"
        return ExperimentObject.model_validate_json(path.read_text(encoding="utf-8"))

    def list_experiments(self) -> list[ExperimentObject]:
        exps = [
            ExperimentObject.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted((self.root / "experiments").glob("*.json"))
        ]
        return sorted(exps, key=lambda e: (e.round_id, e.exp_id))

    def _read_file_text_with_retry(self, p: Path) -> str:
        """Read one file's text with bounded retry on transient OSError (e.g. EIO/ESTALE
        from a flaky NFS mount). This is scoped to a single file's read call — it does not
        wrap directory listing/glob, so an unreadable observations/ directory still raises
        immediately (environment-level fault, must stay loud). After exhausting
        `_OBS_READ_MAX_ATTEMPTS`, the last OSError is re-raised for the caller to quarantine."""
        last_exc: OSError | None = None
        for attempt in range(self._OBS_READ_MAX_ATTEMPTS):
            try:
                return p.read_text(encoding="utf-8")
            except OSError as e:
                last_exc = e
                if attempt < self._OBS_READ_MAX_ATTEMPTS - 1:
                    _log.warning(
                        "transient_read_error: retrying %s after %s (attempt %d/%d)",
                        p, type(e).__name__, attempt + 1, self._OBS_READ_MAX_ATTEMPTS,
                    )
                    time.sleep(self._OBS_READ_RETRY_BACKOFF_S)
        assert last_exc is not None
        raise last_exc

    def _read_obs_dir(self) -> list[ObservationObject]:
        """glob observations/ 逐文件解析，**故障隔离**（OS3 §一）：单文件非 UTF-8/坏 JSON/
        校验失败 → 隔离该文件（记入 self.quarantined_files + logging.error 响亮列名与原因）
        并跳过继续，绝不让单坏文件 DoS 全 run。刷新 self.quarantined_files（本次全量扫描口径）。
        缓存重建与磁盘直读路径共用本谓词——隔离行为一致。

        Transient per-file OSError (EIO/ESTALE) also goes through quarantine, but only after
        `_read_file_text_with_retry` exhausts its bounded retries — a single flaky read must
        not crash the whole run (the exact DoS shape this mechanism guards against). Directory-
        level OSError (glob() itself failing) is NOT caught here and propagates loudly."""
        out: list[ObservationObject] = []
        quarantined: dict[str, str] = {}
        for p in (self.root / "observations").glob("*.json"):
            try:
                text = self._read_file_text_with_retry(p)
                obs = ObservationObject.model_validate_json(text)
            except (UnicodeDecodeError, ValidationError, json.JSONDecodeError, OSError) as e:
                quarantined[str(p)] = f"{type(e).__name__}: {e}"
                _log.error(
                    "view_quarantine: 隔离无法解析的观测文件 %s（%s）——跳过并继续，"
                    "单坏文件不 DoS 全 run（诊断走 expos check，写者路径由 loop 落 view_quarantine 事件）",
                    p, type(e).__name__,
                )
                continue
            out.append(obs)
        self.quarantined_files = quarantined
        return out

    def _build_obs_cache(self) -> None:
        """从磁盘全量重建观测缓存（唯一 glob 观测目录之处，缓存开启后仅 miss/失效时命中）。
        条目由磁盘 JSON 反序列化——与 list_observations 磁盘路径逐字段同源，坏文件同路径隔离。

        Each entry also stores the canonical JSON payload (same format save_observation
        writes to disk) so cache hits can hand back freshly deserialized copies (G1)."""
        self._obs_cache = {
            o.obs_id: (o, o.model_dump_json(indent=2)) for o in self._read_obs_dir()
        }

    def save_observation(self, obs: ObservationObject) -> Path:
        path = self.root / "observations" / f"{obs.obs_id}.json"
        payload = obs.model_dump_json(indent=2)
        self._atomic_write_text(path, payload)
        # 缓存同步维护（M-2）：仅在缓存已建时更新（未建则下次 list 惰性重建自会纳入本文件）。
        # 用**落盘同一份 JSON** 反序列化回构条目——保证缓存条目 == 磁盘读回值（逐字段一致），
        # 且与调用方后续可能的原地改动解耦（QCPolicy route 后再挂 failure_attr 二次落盘会再进本路）。
        if self._cache_observations and self._obs_cache is not None:
            # Entry = (object parsed back from the exact bytes written, those bytes) —
            # the payload lets cache hits rehydrate fresh copies (G1).
            self._obs_cache[obs.obs_id] = (
                ObservationObject.model_validate_json(payload), payload,
            )
        return path

    def save_observations(self, obs_list: list[ObservationObject]) -> None:
        for obs in obs_list:
            self.save_observation(obs)

    def load_observation(self, obs_id: str) -> ObservationObject:
        path = self.root / "observations" / f"{obs_id}.json"
        return ObservationObject.model_validate_json(path.read_text(encoding="utf-8"))

    def list_observations(
        self,
        round_id: int | None = None,
        trust: TrustLevel | None = None,
    ) -> list[ObservationObject]:
        """按**内容确定序**（round_id, well_id, obs_id）返回，不按 uuid 文件名序。

        obs_id 是随机 uuid：按文件名排序会使同一 run 配置每次重跑得到不同的观测
        顺序，浮点求和顺序抖动（~1e-12）经 GP 超参优化放大成 UCB 近平局翻转 →
        同 seed 轨迹 run-to-run 分叉（R1-5(c) 根因）。(round_id, well_id) 在一个
        run 内唯一且由种子确定，obs_id 仅作病理性重复的稳定 tiebreak。"""
        if self._cache_observations:
            # 内存态命中（M-2）：O(1) 免 glob+全量反序列化。惰性首建；此后 save_observation
            # 同步维护、reconcile_redo_rounds 失效重建。过滤/排序判据与磁盘路径逐字一致。
            if self._obs_cache is None:
                self._build_obs_cache()
            # Never return the cached objects themselves (G1): the cache-off path hands
            # back freshly deserialized objects, so an in-place mutation by a caller that
            # forgets to save() is a silent no-op there; returning the shared cached
            # instance would let that same mutation corrupt the cache for every later
            # call. Copies are made by re-deserializing the cached JSON payload — the
            # very operation the cache-off path performs on the on-disk bytes, so both
            # paths return identically-constructed objects. Chosen over
            # model_copy(deep=True) on measurement: at 384 obs, deep-copying all entries
            # costs ~77ms/call (2.3x the local-FS disk path — would negate M-2), while
            # filter-then-rehydrate stays ~4x cheaper and only pays for matching entries.
            out = [
                ObservationObject.model_validate_json(payload)
                for entry, payload in self._obs_cache.values()
                if (round_id is None or entry.round_id == round_id)
                and (trust is None or entry.trust == trust)
            ]
        else:
            out = []
            for obs in self._read_obs_dir():  # 磁盘直读，同款故障隔离
                if round_id is not None and obs.round_id != round_id:
                    continue
                if trust is not None and obs.trust != trust:
                    continue
                out.append(obs)
        return sorted(out, key=lambda o: (o.round_id, o.layout_meta.well_id, o.obs_id))

    # ---------------------------------------------------------- 崩溃窗口对账

    def reconcile_redo_rounds(self, from_round: int) -> dict[str, Any] | None:
        """level-triggered 对账（CONTROLLER_MODEL 不变量精神）：清掉物化视图里
        round_id ≥ from_round（=checkpoint.completed_rounds）的**崩溃窗口孤儿**。

        崩溃可落在"观测/实验已落盘、write_checkpoint 未落"之间：resume 会重做该轮
        并生成全新 uuid 的 exp/obs——孤儿不清则同轮双份观测喂进响应模型、n_train 虚胖
        （truth/ 那一路按轮幂等覆盖，observations/experiments 此前没有对应幂等，
        R1-5(b)）。处置响亮留痕：删除孤儿观测+实验文件并落 `redo_reconciliation`
        事件（EVENT_SCHEMA §1，testing）。events.jsonl append-only 不动——事件日志
        里旧轮痕迹保留是审计特性，这里只清物化视图。零孤儿时不删文件但仍落标记（R4-A）。
        返回事件 payload（恒非 None；R4-A 修复后零孤儿也落标记）。"""
        orphan_obs = [o for o in self.list_observations() if o.round_id >= from_round]
        orphan_exps = [e for e in self.list_experiments() if e.round_id >= from_round]
        # R4-A [P1] fix: the marker must land even with zero orphans. A crash in the
        # window "action_consumed event written -> save_experiment not yet written"
        # leaves no orphan files, yet stale consumed-events exist; without the marker
        # the planner-side seq/from_round filter degrades to pre-fix behavior and
        # silently skips remedial actions on the redo round. The marker records the
        # fact "rounds >= from_round are being redone", true regardless of orphan count.
        for o in orphan_obs:
            (self.root / "observations" / f"{o.obs_id}.json").unlink()
        for e in orphan_exps:
            (self.root / "experiments" / f"{e.exp_id}.json").unlink()
        # 直接 unlink 绕过 save_observation → 强制失效观测缓存（M-2 一致性红线）。
        # 下次 list_observations 从磁盘重建，反映清账后的真相；缓存关闭时此赋值为无害 no-op。
        self._obs_cache = None
        payload = {
            "from_round": from_round,
            "n_observations_removed": len(orphan_obs),
            "n_experiments_removed": len(orphan_exps),
            "exp_ids": sorted(e.exp_id for e in orphan_exps),
        }
        self.append_event("redo_reconciliation", payload)
        _log.warning(
            "redo_reconciliation: 清除崩溃窗口孤儿（round_id ≥ %d）：观测 %d 条、实验 %d 个"
            "——resume 将重做该轮（事件日志保留旧轮痕迹，仅物化视图被清）",
            from_round, len(orphan_obs), len(orphan_exps),
        )
        return payload

    # ---------------------------------------------------------- 检查点与配置

    def _atomic_write_text(self, path: Path, text: str) -> None:
        # tmp 名带 pid（M-4b）：确定性 `.tmp` 名会让两进程同写一个目标文件时撞同一
        # 临时文件，A 写盘 / B 覆盖 / A rename 交错产出损坏内容。带 pid 后每进程独占其
        # tmp，rename 各自原子。writer.lock 已在正常路径拦并发写，这是纵深防御第二层。
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _atomic_write_json(self, path: Path, data: dict[str, Any]) -> None:
        self._atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))

    def write_checkpoint(self, state: dict[str, Any]) -> None:
        """先记事件、后原子写文件：两步间崩溃时 checkpoint.json 落后于日志，
        续跑会保守地重做该轮（安全方向的偏斜）。"""
        state = dict(state)
        state["written_at"] = utc_now()
        # round_id 回退（红队信 001 实锤真 bug）：loop 传的是 completed_rounds，
        # 旧版只读 round_id 键 → 事件 round_id 恒 null、resume 索引恒空。
        # 语义：本恢复点覆盖到的最后完成轮 = completed_rounds - 1。
        rid = state.get("round_id")
        if rid is None and state.get("completed_rounds") is not None:
            rid = int(state["completed_rounds"]) - 1
        ckpt_ev = self.append_event("checkpoint", {
            "round_id": rid, "completed_rounds": state.get("completed_rounds"),
        })
        # Forked-resume detection anchor (Phase 4 item #1, INDEX_REF_X §Convergence c /
        # litestream L-2): pin the (seq, sha256) of the LAST event as of this checkpoint —
        # the checkpoint event just appended. On resume the event at ``last_event_seq`` must
        # still hash to ``last_event_sha256``; a mismatch = the log was rewritten/truncated
        # underneath the checkpoint (forked history) => loud refusal to resume. ADDITIVE keys:
        # a pre-Phase-4 checkpoint lacking them resumes exactly as before (compat note).
        state["last_event_seq"] = ckpt_ev["seq"]
        state["last_event_sha256"] = self._event_line_sha256(ckpt_ev)
        self._atomic_write_json(self.root / _CHECKPOINT, state)

    def read_checkpoint(self) -> dict[str, Any] | None:
        p = self.root / _CHECKPOINT
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def save_config(self, cfg: dict[str, Any]) -> None:
        self._atomic_write_json(self.root / _CONFIG, cfg)

    def read_config(self) -> dict[str, Any] | None:
        p = self.root / _CONFIG
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ---------------------------------------------------------- 真值 sidecar（公理 6）

    def save_truth(self, round_id: int, records: list[dict[str, Any]]) -> Path:
        """不透明落盘，**按轮幂等（覆盖写）**——断点续跑重做某轮时不得重复追加
        （Bluesky 走读发现的坑：append 语义会让阶段重做产生双份真值行）。
        本方法只由 loop.py 在执行后调用；qc/models/planner/agent 禁止调用或读取
        truth/（守门测试与验收红线）。"""
        path = self.root / "truth" / f"round_{round_id}.jsonl"
        text = "".join(
            json.dumps(rec, ensure_ascii=False, default=str) + "\n" for rec in records
        )
        self._atomic_write_text(path, text)
        return path

    # ---------------------------------------------------------- 只读视图

    def export_view(self) -> ReadOnlyRunView:
        """导出给 agent 层的只读快照（不含 truth）。"""
        return ReadOnlyRunView(
            run_root=str(self.root),
            exported_at=utc_now(),
            experiments=tuple(self.list_experiments()),
            observations=tuple(self.list_observations()),
            decisions=tuple(self.list_decisions()),
            events=tuple(self.read_events()),
            checkpoint=self.read_checkpoint(),
        )
