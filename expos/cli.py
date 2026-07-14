"""expos CLI v2（权威规格：docs/CLI_DESIGN.md）。

定位：CLI 是 runs/ 的第四个读者（loop 写者、UI 读者、测试之外），遵守同一契约——
只经 RunStore 读方法访问；**唯一带写副作用的命令是 override**，且它连 RunStore 都不碰，
只向 `<run_dir>/overrides/pending/` 原子投递一个提案文件（REFERENCE_MAP §13.13 通道）。

退出码（对齐 expos/errors.py 的 ExposError.user_facing）：
- 0 成功（含"查询结果为空"）；
- 2 领域/用法错误（ExposError(user_facing=True) + argparse 用法错）；
- 1 内部 bug（user_facing=False 或任何未捕获异常——**不吞 traceback**）。

快启动纪律：重依赖（numpy/streamlit/loop）一律延迟到子命令函数体内 import，
`python3 -m expos.cli --help` 不触发它们（本模块顶层只 import stdlib + errors）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from expos.errors import ExposError

ROOT = Path(__file__).resolve().parent.parent

_TRUST_CHOICES = {"trusted": "TRUSTED", "suspect": "SUSPECT",
                  "failed": "FAILED", "pending": "PENDING"}
_ROUTING_CHOICES = [
    "TO_RESPONSE_MODEL", "TO_FAILURE_MODEL",
    "QUARANTINE", "REMEASURE", "REPEAT_CANDIDATE",
]


class CliError(ExposError):
    """CLI 领域/用法错误——干净 exit 2（user_facing 继承 True）。"""


# ---------------------------------------------------------------- 通用工具

def _emit(payload: dict[str, Any] | list[Any], use_json: bool, human: str) -> int:
    """--json 时打机器 JSON 到 stdout；否则打人读文本。返回 0。"""
    if use_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(human)
    return 0


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    """stdlib 手写定宽列（无 tabulate 依赖），列宽由数据计算。"""
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    widths = [max(len(str(c)) for c in col) for col in cols]
    def fmt(cells: list[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))
    lines = [fmt(headers)]
    for r in rows:
        lines.append(fmt(r))
    return "\n".join(lines)


def _resolve_run_dir(run_dir: str) -> Path:
    p = Path(run_dir)
    if not p.is_dir():
        raise CliError(f"运行目录不存在: {p}")
    return p


def _open_store(run_dir: str):
    """以只读方式（create=False）打开 RunStore；目录不存在→干净 exit 2。"""
    p = _resolve_run_dir(run_dir)
    from expos.kernel.store import RunStore  # 延迟：pydantic 依赖
    try:
        return RunStore(p, create=False)
    except FileNotFoundError as e:  # RunStore create=False 的领域错误
        raise CliError(str(e)) from e


# ---------------------------------------------------------------- run

def cmd_run(args: argparse.Namespace) -> int:
    domain_path = Path(args.domain)
    if not domain_path.exists():
        domain_path = ROOT / "domains" / f"{args.domain}.yaml"

    # --loop mcl (M16): the dual-leg minimum complete loop (dry PySCF + Dry->Wet
    # promotion + wet plate-reader) is a SEPARATE driver from the single-leg
    # run_loop — it never goes through build_adapter (dimw5_handoff §1).
    if getattr(args, "loop", "single") == "mcl":
        return _cmd_run_mcl(args, domain_path)

    from expos.loop import run_loop  # 延迟：numpy/sklearn

    # compare 三臂编排走 eval 侧（scripts/run_loop.py 的 compare 分支）；本命令只跑单臂。
    summary = run_loop(
        domain_path, mode=args.mode, rounds=args.rounds,
        seed=args.seed, out_dir=args.out, resume=args.resume,
        allow_config_drift=getattr(args, "allow_config_drift", False),
    )
    # Rounds 分母与 status 统一：都用 campaign 视界 budget.rounds_total（跨 resume 恒定），
    # 而非本次调用的 rounds_target（随 --rounds/resume 漂移，与 status 口径不一）。
    _ckpt = _open_store(str(args.out)).read_checkpoint() or {}
    rounds_total = _ckpt.get("budget", {}).get("rounds_total") or summary.get("rounds_target")
    summary["rounds_total"] = rounds_total  # 让 --json 也带上与 status 一致的 campaign 分母
    human = (
        f"Run {args.out}  domain={summary.get('domain')}  mode={args.mode}\n"
        f"Rounds  {summary.get('rounds_completed')}/{rounds_total} completed\n"
        f"Obs     {summary.get('n_observations')}  "
        f"TRUSTED {summary.get('n_trusted')} "
        f"SUSPECT {summary.get('n_suspect')} FAILED {summary.get('n_failed')}\n"
        f"Best    {summary.get('best_trusted')}"
    )
    return _emit(summary, args.json, human)


def _cmd_run_mcl(args: argparse.Namespace, domain_path: Path) -> int:
    """`run --loop mcl`: two-round dual-leg MCL (M16). resume is not supported
    (the MCL runs its fixed two-round campaign fresh; --resume is single-leg)."""
    from expos.mcl import run_mcl_loop  # 延迟：numpy/pyscf/scheduler

    if getattr(args, "resume", False):
        raise CliError("--loop mcl 不支持 --resume（两轮一次跑完，换目录重跑）")
    # M18 agent-backend switch (docs/M18_LLM_LIVE_SMOKE.md §1): template (default) is the
    # byte-identical production path (pass None); shadow/llm carry a provider route string.
    agent_mode = getattr(args, "agent_backend", "template")
    agent_backend = None
    if agent_mode != "template":
        agent_backend = {"mode": agent_mode,
                         "provider": getattr(args, "agent_provider", None)}
    summary = run_mcl_loop(
        domain_path, rounds=args.rounds, seed=args.seed,
        out_dir=args.out, mode=args.mode,
        truth_profile=getattr(args, "truth_profile", None),
        agent_backend=agent_backend,
    )
    # Eval-harness provenance record (case 2, B): write an INDEPENDENT peer file
    # alongside the OS config.json capturing the evaluation-device knobs
    # (truth_profile / noise_sd / interleave / derived reader sub-seed / agent
    # backend) that NEVER enter the OS run record (truth-blind red line). Written
    # HERE at the evaluation entry (the CLI seam) so mcl.py's OS path stays free of
    # harness knowledge. The record layer is OFF the critical path: a write failure
    # is logged to stderr and never aborts the (already-completed) run -- but it is
    # never silently swallowed (no-silent-degradation red line).
    #
    # HARDENING NOTE (single-object anti-drift, INDEX_M22_EVALPROV §2, filed for the
    # hardening batch -- NOT done here because it would touch mcl.py's OS path):
    # noise_sd and interleave are mcl-PINNED constants (mcl.py:850 noise_sd=0.0,
    # mcl.py:~1106 compile_wet(interleave=True)); the CLI mirrors them. The
    # constructive fix is to freeze the eval knobs into ONE EvalHarnessSpec that the
    # serve()/compile_wet dispatch (mcl.py:849-852 / ~1106) CONSUMES as the same
    # instance this record serializes, so "dispatched == recorded" becomes
    # structurally impossible to violate rather than diligently mirrored.
    try:
        from expos.adapters.wet.sim_reader import DEFAULT_TRUTH_PROFILE
        from expos.eval.harness_record import EvalHarnessSpec, write_harness_record
        from expos.loop import derive_seed

        tp = getattr(args, "truth_profile", None) or DEFAULT_TRUTH_PROFILE
        spec = EvalHarnessSpec(
            truth_profile=tp,
            noise_sd=0.0,       # mcl.py pins the in-process reader to noise_sd=0.0
            interleave=True,    # compile_wet(..., interleave=True) -- mcl-pinned
            root_seed=args.seed,
            reader_seed=derive_seed(args.seed, "reader"),
            agent_backend=agent_mode,
            mode=args.mode,
        )
        rec_path = write_harness_record(args.out, spec.as_knobs())
        print(f"eval-harness record written: {rec_path}", file=sys.stderr)
    except Exception as exc:  # off critical path: log loudly, never abort the run
        print(f"[eval-harness] provenance record write failed (non-fatal): "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
    human = (
        f"Run {args.out}  domain={summary.get('domain')}  loop=mcl mode={args.mode}\n"
        f"Rounds  {summary.get('rounds_completed')}/{summary.get('rounds_target')} completed\n"
        f"Obs     {summary.get('n_observations')} "
        f"(dry {summary.get('n_dry')} / wet {summary.get('n_wet')})  "
        f"TRUSTED {summary.get('n_trusted')}\n"
        f"Knowledge updates {summary.get('n_knowledge_updates')}  "
        f"promotion decisions {summary.get('n_promotion_decisions')}"
    )
    return _emit(summary, args.json, human)


# ---------------------------------------------------------------- status

def cmd_status(args: argparse.Namespace) -> int:
    from expos.kernel.objects import TrustLevel

    store = _open_store(args.run_dir)
    ckpt = store.read_checkpoint()
    if ckpt is None:
        raise CliError(f"运行目录缺 checkpoint.json（未完成任何轮次）: {args.run_dir}")
    cfg = store.read_config() or {}
    dcfg = cfg.get("domain_config", {})
    direction = (dcfg.get("objective") or {}).get("direction", "maximize")

    counts = {t.value: 0 for t in TrustLevel}
    best = None
    sign = 1.0 if direction == "maximize" else -1.0
    for obs in store.list_observations():
        counts[obs.trust.value] += 1
        if (obs.trust == TrustLevel.TRUSTED and not obs.is_control
                and obs.result.value is not None):
            if best is None or sign * obs.result.value > sign * best["value"]:
                best = {"value": obs.result.value, "obs_id": obs.obs_id,
                        "cand_id": obs.cand_id, "round_id": obs.round_id}

    budget = ckpt.get("budget", {})
    wt, wu = budget.get("wells_total", 0), budget.get("wells_used", 0)
    def _n_override_files(sub: str) -> int:
        d = store.root / "overrides" / sub
        return len(list(d.glob("*.json"))) if d.is_dir() else 0

    n_pending = _n_override_files("pending")
    n_rejected = _n_override_files("rejected")
    from expos.kernel.objects import DecisionKind
    n_applied = len(store.list_decisions(kind=DecisionKind.OVERRIDE))

    payload = {
        "run_dir": str(store.root),
        "domain": cfg.get("domain"), "mode": cfg.get("mode"), "seed": cfg.get("seed"),
        "rounds_completed": ckpt.get("completed_rounds"),
        "rounds_total": budget.get("rounds_total"),
        "last_checkpoint": ckpt.get("written_at"),
        "budget": {"wells_used": wu, "wells_total": wt,
                   "pct": round(100.0 * wu / wt, 1) if wt else 0.0},
        "trust": counts,
        "best": best,
        "overrides": {"pending": n_pending, "applied": n_applied,
                      "rejected": n_rejected},
    }
    pct = payload["budget"]["pct"]
    human = "\n".join([
        f"Run {store.root}        domain={cfg.get('domain')}  "
        f"mode={cfg.get('mode')}  seed={cfg.get('seed')}",
        f"Rounds  {ckpt.get('completed_rounds')}/{budget.get('rounds_total')} completed"
        f"    last checkpoint {ckpt.get('written_at')}",
        f"Budget  wells {wu}/{wt} ({pct}%)",
        f"Trust   TRUSTED {counts['TRUSTED']}  SUSPECT {counts['SUSPECT']}  "
        f"FAILED {counts['FAILED']}  PENDING {counts['PENDING']}",
        f"Best    {'y=%.4f  (%s, round %s)' % (best['value'], best['obs_id'], best['round_id']) if best else 'n/a'}",
        f"Overrides  pending {n_pending} / applied {n_applied} / rejected {n_rejected}",
    ])
    return _emit(payload, args.json, human)


# ---------------------------------------------------------------- verdicts

def cmd_verdicts(args: argparse.Namespace) -> int:
    from expos.kernel.objects import TrustLevel

    store = _open_store(args.run_dir)
    trust_filter = None
    if args.trust:
        trust_filter = TrustLevel(_TRUST_CHOICES[args.trust.lower()])

    rows: list[list[str]] = []
    records: list[dict[str, Any]] = []
    total = 0
    for obs in store.list_observations():
        total += 1
        if trust_filter is not None and obs.trust != trust_filter:
            continue
        suspicion = obs.qc.suspicion if obs.qc else 0.0
        top_cause = obs.failure_attr.top_cause if obs.failure_attr else None
        next_action = obs.next_action.action.value if obs.next_action else None
        routing = obs.routing.value if obs.routing else None
        records.append({
            "obs_id": obs.obs_id, "round": obs.round_id,
            "well": obs.layout_meta.well_id, "trust": obs.trust.value,
            "suspicion": round(suspicion, 3), "routing": routing,
            "top_cause": top_cause, "next_action": next_action,
        })
        rows.append([
            obs.obs_id, str(obs.round_id), obs.layout_meta.well_id,
            obs.trust.value, f"{suspicion:.3f}", routing or "-",
            top_cause or "-", next_action or "-",
        ])

    payload = {"run_dir": str(store.root), "n_shown": len(records),
               "n_total": total, "verdicts": records}
    if not rows:
        print(f"no observations matched (total {total})", file=sys.stderr)
        return _emit(payload, args.json, "")
    headers = ["obs_id", "round", "well", "trust", "suspicion",
               "routing", "top_cause", "next_action"]
    human = _render_table(headers, rows) + f"\n\n{len(records)} of {total} observations"
    return _emit(payload, args.json, human)


# ---------------------------------------------------------------- inspect

def _event_summary(rec: dict[str, Any]) -> str:
    kind = rec.get("kind")
    p = rec.get("payload", {})
    if kind == "round_designed":
        return (f"round={p.get('round_id')} {p.get('exp_id')} "
                f"{p.get('generator')} n={p.get('n_candidates')} wells={p.get('wells')}")
    if kind == "status_transition":
        return f"{p.get('exp_id')} {p.get('from')}→{p.get('to')}"
    if kind == "routing_bulk":
        return f"mode={p.get('mode')} n={p.get('n')} round={p.get('round_id')}"
    if kind == "model_updated":
        return f"round={p.get('round_id')} n_train={p.get('n_train')}"
    if kind == "checkpoint":
        return f"round={p.get('round_id')}"
    if kind == "resume":
        return f"from_round={p.get('from_round')}"
    if kind == "decision":
        return f"{p.get('kind')} actor={p.get('actor')} {p.get('decision_id')}"
    return " ".join(f"{k}={v}" for k, v in list(p.items())[:5])


def _inspect_events(args: argparse.Namespace, store) -> int:
    events = store.read_events(kind=args.kind)
    if args.tail:
        events = events[-args.tail:]
    if not events:
        print("no events", file=sys.stderr)
        return _emit(events, args.json, "")
    # 事件表可能很长——用 `| head`/`| less` 分页是常规用法。下游提前关管道会抛 BrokenPipe，
    # 若不接住，解释器退出时二次 flush 会再抛并以 120 退出；这里静默改指 /dev/null，干净退 0。
    try:
        if args.json:
            # JSON Lines：事件本身已是机器格式，直接透传（CLI_DESIGN §3.3）。
            for rec in events:
                print(json.dumps(rec, ensure_ascii=False, default=str))
        else:
            rows = [[str(r.get("seq")), r.get("ts", ""), r.get("kind", ""), _event_summary(r)]
                    for r in events]
            print(_render_table(["seq", "ts", "kind", "summary"], rows))
    except BrokenPipeError:
        # 下游管道关闭（如 | head）——标准缓解：stdout 重定向 devnull 防解释器
        # 退出期二次 BrokenPipe。降级本身失败时向 stderr 留痕（EXP005：不静默）。
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError as exc:
            print(f"[inspect] stdout 管道已断且 devnull 降级失败: {exc}", file=sys.stderr)
    return 0


def _inspect_obs(args: argparse.Namespace, store) -> int:
    obs_id = args.target
    if not obs_id:
        raise CliError("inspect obs 需要 <obs_id>")
    path = store.root / "observations" / f"{obs_id}.json"
    if not path.exists():
        raise CliError(f"观测不存在: {obs_id}")
    obs = store.load_observation(obs_id)
    r = obs.result
    props: list[tuple[str, str]] = [
        ("obs_id", obs.obs_id), ("exp_id", obs.exp_id), ("round_id", str(obs.round_id)),
        ("result", f"{r.value}±{r.uncertainty} {r.unit}".strip()),
        ("metric", r.metric),
        ("trust", f"{obs.trust.value} (conf {obs.trust_confidence})"),
        ("routing", obs.routing.value if obs.routing else "-"),
        ("layout", f"{obs.layout_meta.well_id} (row {obs.layout_meta.row}, "
                   f"col {obs.layout_meta.col}, block {obs.layout_meta.block_id})"),
        ("cand_id", obs.cand_id or "-"), ("control_id", obs.control_id or "-"),
        ("is_control", str(obs.is_control)),
        ("failure_attr", obs.failure_attr.top_cause if obs.failure_attr else "-"),
        ("next_action", obs.next_action.action.value if obs.next_action else "-"),
        ("created_at", obs.created_at),
    ]
    if obs.qc:
        props.append(("qc.flags", ", ".join(obs.qc.flags) or "-"))
        props.append(("qc.suspicion", f"{obs.qc.suspicion:.3f}"))
        for c in obs.qc.checks:
            props.append((f"qc[{c.name}]",
                          f"{'PASS' if c.passed else 'FAIL'} score={c.score:.3f} ({c.level})"))
    payload = obs.model_dump(mode="json")
    human = _render_table(["Property", "Value"], [[k, v] for k, v in props])
    human += "\n\nRun 'expos inspect <run_dir> events --kind decision' for verdicts"
    return _emit(payload, args.json, human)


def _inspect_exp(args: argparse.Namespace, store) -> int:
    if args.target is None:
        raise CliError("inspect exp 需要 <round>")
    try:
        round_id = int(args.target)
    except ValueError as e:
        raise CliError(f"round 必须是整数: {args.target!r}") from e
    exps = [e for e in store.list_experiments() if e.round_id == round_id]
    if not exps:
        raise CliError(f"round {round_id} 无实验对象")
    exp = exps[0]
    b = exp.budget
    n_wells = len(exp.layout.wells) if exp.layout else 0
    props = [
        ("exp_id", exp.exp_id), ("round_id", str(exp.round_id)),
        ("status", exp.status.value), ("domain", exp.domain),
        ("generator", exp.provenance.generator),
        ("acquisition", exp.provenance.acquisition or "-"),
        ("based_on_obs", str(exp.provenance.based_on_obs)),
        ("candidates", str(len(exp.candidates))),
        ("controls", str(len(exp.controls))), ("wells", str(n_wells)),
        ("budget", f"wells {b.wells_used}/{b.wells_total}  "
                   f"rounds {b.rounds_used}/{b.rounds_total}"),
        ("rationale", exp.provenance.rationale or "-"),
    ]
    payload = exp.model_dump(mode="json")
    human = _render_table(["Property", "Value"], [[k, v] for k, v in props])
    return _emit(payload, args.json, human)


def cmd_inspect(args: argparse.Namespace) -> int:
    store = _open_store(args.run_dir)
    if args.what == "events":
        return _inspect_events(args, store)
    if args.what == "obs":
        return _inspect_obs(args, store)
    if args.what == "exp":
        return _inspect_exp(args, store)
    raise CliError(f"未知 inspect 对象: {args.what}")  # argparse choices 已挡，双保险


# ---------------------------------------------------------------- override

def cmd_override(args: argparse.Namespace) -> int:
    """§13.13 pending 通道的 CLI 端——**唯一带写副作用的命令，且绝不触碰 RunStore**。

    只做两件事：(1) 直接读观测 JSON 文件取 base_version（乐观并发戳，用文件 mtime）；
    (2) 向 overrides/pending/ 原子投递（tmp + os.rename）一个提案文件。
    绝不打开 RunStore，绝不修改 run 目录内任何既有文件（零写证明）。
    """
    from expos.kernel.objects import utc_now  # 轻量：仅 datetime，无 numpy

    run_dir = _resolve_run_dir(args.run_dir)
    obs_path = run_dir / "observations" / f"{args.obs}.json"
    if not obs_path.exists():
        raise CliError(f"观测不存在: {args.obs}")
    # base_version：观测无 version 字段→用文件 mtime 作乐观并发戳（消费时不符=conflict）。
    base_version = os.path.getmtime(obs_path)

    to_trust = _TRUST_CHOICES[args.trust.lower()]
    to_routing = args.routing if args.routing else None

    proposal = {
        "obs_id": args.obs,
        "to_trust": to_trust,
        "to_routing": to_routing,
        "reason": args.reason,
        "base_version": base_version,
        "created_at": utc_now(),
        "actor": "human",
        "source": "cli",
    }

    pending = run_dir / "overrides" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    fname = f"ovr_{uuid.uuid4().hex}.json"
    dest = pending / fname
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dest)

    payload = {"pending_file": str(dest), "proposal": proposal}
    human = (f"override 已投递: {dest}\n"
             f"  obs={args.obs} trust→{to_trust}"
             f"{' routing→' + to_routing if to_routing else ''}\n"
             f"将于下一轮开始（plan_round 前）由 kernel.overrides.consume_pending_overrides 消费")
    print(human, file=sys.stderr) if args.json else None
    return _emit(payload, args.json, human)


# ---------------------------------------------------------------- check

# 四档退出码（照 redis-check-aof）：0 干净/已修 · 1 可修尾损但仅诊断(未修) · 3 中段损坏
# (CorruptedRun，结构性拒修) · 2 用法/领域错(CliError)。CI 据非零码与档位分流。
_CHECK_OK = 0
_CHECK_TRUNCATED_DIAGNOSED = 1
_CHECK_CORRUPT = 3


def cmd_check(args: argparse.Namespace) -> int:
    """events.jsonl 尾损诊断 + 有界自愈（O3-D 交接建议 3 + 收紧 3；redis-check-aof 三段式：
    诊断先行(双坐标) → 干净尾截断可 --fix 自愈(备份 .pre_fix) → 中段损坏响亮拒修）。

    默认只诊断：干净→exit 0；可修尾损→报告 + 指引 + exit 1（绝不静默自愈，呼应无静默降级
    红线）；中段损坏→CorruptedRun 响亮 exit 3。--fix 才截断，且交互确认默认 N（--yes 旁路
    供 CI）。行有效性判据复用 store（scan_events_tail），不重复实现。"""
    store = _open_store(args.run_dir)
    scan = store.scan_events_tail()
    status = scan["status"]

    # 视图健康分区（OS3 §一(b) + 用户架构裁决 P0，mailbox 020）：六项物化视图各报
    # healthy|stale|quarantined|missing——现对坏 obs/stale score/incomplete lineage 全盲报 clean，
    # 这里补齐。stale/incomplete 不装正常（→degraded→非 clean）；missing 为可缺前向产物（advisory）。
    health = store.scan_view_health()

    diag_lines = [
        f"events.jsonl 尾损诊断: {store.root / 'events.jsonl'}",
        f"  size            {scan['size']} bytes / {scan['n_lines']} lines",
        f"  valid_up_to     byte {scan['valid_up_to_byte']} · line {scan['valid_up_to_line']}",
        f"  status          {status}",
    ]
    if scan["first_bad_line"] is not None:
        diag_lines.append(f"  first_bad_line  {scan['first_bad_line']}")
    diag_lines.append(f"视图健康分区（overall={health['overall']}）:")
    for name in ("events", "observations", "experiments", "score", "lineage", "snapshot"):
        sec = health["sections"][name]
        diag_lines.append(f"  {name:13s} {sec['status']:11s} {sec['detail']}")
        for bp in sec.get("bad_files", []):
            diag_lines.append(f"      [bad] {bp}")
    human = "\n".join(diag_lines)

    # 视图层降级（events 尾部 clean 但视图 degraded）——stale/quarantined 皆非 clean（诊断=1）。
    if status == "clean":
        if health["overall"] == "degraded":
            bad = [n for n, s in health["sections"].items()
                   if s["status"] in ("stale", "quarantined")]
            msg = (human + f"\n  → 视图 degraded（{', '.join(bad)}）：events 尾部 clean，但物化视图"
                   "有 stale/quarantined 项（运行期坏文件已被隔离不 DoS，但非 clean）——请核查后重评/重跑")
            print(f"error: 视图 degraded：{', '.join(bad)}（events 尾部 clean）", file=sys.stderr)
            _emit({"status": "view_degraded", "view_health": health, **scan}, args.json, msg)
            return _CHECK_TRUNCATED_DIAGNOSED
        payload = {"status": "clean", "view_health": health, **scan}
        return _emit(payload, args.json, human + "\n  → OK：无尾损、视图健康，无需修复")

    if status == "corrupt":
        msg = (human + "\n  → CorruptedRun：水位后仍有非空行（中段损坏，非崩溃尾）——"
               "结构性拒修，绝不截断。疑似磁盘损伤/篡改/并发写，请人工核查")
        print(f"error: CorruptedRun: events.jsonl 中段损坏（行 {scan['first_bad_line']} 后仍有内容）",
              file=sys.stderr)
        _emit({"status": "corrupt", "view_health": health, **scan}, args.json, msg)
        return _CHECK_CORRUPT

    # status == "truncated"：可自愈的干净尾截断
    if not getattr(args, "fix", False):
        guide = (human + "\n  → 可修尾损（干净尾截断，末行不完整/坏 JSON 且其后直达 EOF）。"
                 "\n     自愈: expos check "
                 f"{args.run_dir} --fix   （截到水位、备份原文件 .pre_fix；--yes 免确认）")
        _emit({"status": "truncated", "fixable": True, **scan}, args.json, guide)
        return _CHECK_TRUNCATED_DIAGNOSED

    if not getattr(args, "yes", False):
        print(human, file=sys.stderr)
        resp = input(f"截断尾损残尾到 byte {scan['valid_up_to_byte']}（备份 .pre_fix）？[y/N] ")
        if resp.strip().lower() not in ("y", "yes"):
            print("已取消，未修改。", file=sys.stderr)
            return _CHECK_TRUNCATED_DIAGNOSED

    # --fix 取 writer.lock（OS3 §一(c)）：check --fix 截断写盘，"override 是唯一 CLI 写通道"
    # 已不成立——truncate 前取 writer.lock（与 loop 同协议），取不到即拒（loop 正在写/另一
    # --fix 并发时绝不交错截断 events.jsonl）。锁在同一进程内取，truncate 后释放。
    from expos.kernel.store import RunStore, StoreError
    try:
        locked = RunStore(store.root, create=False, lock=True)
    except StoreError as e:
        print(f"error: check --fix 无法取得 writer.lock（疑似 loop 正在写或另一 --fix 并发）：{e}",
              file=sys.stderr)
        return _CHECK_TRUNCATED_DIAGNOSED
    try:
        backup = locked.truncate_events_tail(scan)
    finally:
        locked.release_writer_lock()
    payload = {"status": "fixed", "backup": str(backup), **scan}
    fixed_human = (human + f"\n  → 已自愈：截到水位 byte {scan['valid_up_to_byte']}，"
                   f"原文件备份至 {backup}")
    return _emit(payload, args.json, fixed_human)


# ---------------------------------------------------------------- domains

def cmd_domains(args: argparse.Namespace) -> int:
    from expos.domain import load_domain  # DomainError 即 user_facing

    if args.action == "validate":
        yaml_path = Path(args.yaml)
        cfg = load_domain(yaml_path)  # 失败抛 DomainError→exit 2
        n_vars = len(cfg.design_space.variables)
        payload = {
            "ok": True, "name": cfg.name, "adapter": cfg.adapter,
            "objective": cfg.objective.metric, "direction": cfg.objective.direction,
            "n_variables": n_vars,
            "artifact_scenario": cfg.simulator.get("artifact_scenario"),
        }
        human = (f"OK {cfg.name}  adapter={cfg.adapter}  "
                 f"metric={cfg.objective.metric} ({cfg.objective.direction})  "
                 f"{n_vars} variables")
        return _emit(payload, args.json, human)
    raise CliError(f"未知 domains 子命令: {args.action}")


# ---------------------------------------------------------------- ui

def cmd_ui(args: argparse.Namespace) -> int:
    import importlib.util
    import subprocess

    if importlib.util.find_spec("streamlit") is None:
        raise CliError("streamlit 未安装——安装命令: pip install 'expos[ui]'（保持核心零 streamlit 依赖）")
    app = ROOT / "ui" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app)]
    passthrough = ["--server.port", str(args.port)]
    if args.runs_root:
        passthrough += ["--", "--runs-root", args.runs_root]
    cmd += passthrough
    print(f"launching: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="机器可读 JSON 输出到 stdout（人读提示走 stderr）")

    p = argparse.ArgumentParser(
        prog="expos", description="expos CLI v2（runs/ 的只读查询器 + override 投递端）")
    p.add_argument("--json", action="store_true", default=False,
                   help="全局机器可读 JSON 输出")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    # run
    pr = sub.add_parser("run", parents=[common], help="闭环运行（收编 run_loop.py）")
    pr.add_argument("--domain", required=True, help="域名（domains/<name>.yaml）或 YAML 路径")
    pr.add_argument("--mode", default="naive", choices=["naive", "robust", "os"])
    pr.add_argument("--loop", default="single", choices=["single", "mcl"],
                    help="single=单腿 run_loop（默认）；mcl=M16 双腿最小完整闭环"
                         "（dry PySCF + Dry→Wet 晋升 + wet 读板，仅 solvent_screen 域）")
    pr.add_argument("--rounds", type=int, required=True)
    pr.add_argument("--truth-profile", default=None,
                    help="[评测 harness surface] --loop mcl 选 sim_reader 隐藏真值面"
                         "（polar_high 默认==M16 面 / nonpolar_high 翻转 / flat 零信号）；"
                         "不进域配置——真值离 OS 路径，仅评测侧透传 serve()")
    pr.add_argument("--seed", type=int, default=7)
    pr.add_argument("--agent-backend", default="template",
                    choices=["template", "shadow", "llm"],
                    help="[--loop mcl, M18] agent 后端档：template=确定性模板（默认，逐位不变）；"
                         "shadow=决策仍模板出、LLM 并行产提案落 agent_shadow_proposal 审计事件；"
                         "llm=LLM 提案驱动决策（耗尽/provider 死→合法安静 + agent_generation_failed）")
    pr.add_argument("--agent-provider", default=None,
                    help="[--loop mcl, M18] shadow/llm 档的 litellm 路由（如 'openai/gpt-4o-mini'；"
                         "'litellm/...' 非合法路由，构造期响亮拒绝）")
    pr.add_argument("--out", required=True, help="运行目录（runs/<name>）")
    pr.add_argument("--resume", action="store_true", help="从 checkpoint.json 续跑")
    pr.add_argument("--allow-config-drift", action="store_true",
                    help="resume 时域配置指纹不符仍续跑（落 config_drift 事件留痕；"
                         "带漂移的 run 跨段语义不再等价，评测侧会据此标记）")
    pr.set_defaults(func=cmd_run)

    # status
    ps = sub.add_parser("status", parents=[common], help="一屏运行态")
    ps.add_argument("run_dir")
    ps.set_defaults(func=cmd_status)

    # verdicts
    pv = sub.add_parser("verdicts", parents=[common], help="裁决清单表")
    pv.add_argument("run_dir")
    pv.add_argument("--trust", choices=list(_TRUST_CHOICES),
                    help="按信任级过滤（大小写不敏感）")
    pv.set_defaults(func=cmd_verdicts)

    # inspect
    pi = sub.add_parser("inspect", parents=[common], help="对象与事件查询")
    pi.add_argument("run_dir")
    pi.add_argument("what", choices=["events", "obs", "exp"])
    pi.add_argument("target", nargs="?", help="obs=<obs_id> / exp=<round>；events 忽略")
    pi.add_argument("--kind", help="events：只看某 kind")
    pi.add_argument("--tail", type=int, help="events：只看末 N 条")
    pi.set_defaults(func=cmd_inspect)

    # override
    po = sub.add_parser("override", parents=[common],
                        help="人工改判——只写 overrides/pending/，绝不触碰 store")
    po.add_argument("run_dir")
    po.add_argument("--obs", required=True, help="目标观测 obs_id")
    po.add_argument("--trust", required=True, choices=["trusted", "suspect", "failed"])
    po.add_argument("--routing", choices=_ROUTING_CHOICES, help="可选新路由")
    po.add_argument("--reason", required=True, help="改判理由（审计不变量，必填）")
    po.set_defaults(func=cmd_override)

    # check
    pc = sub.add_parser("check", parents=[common],
                        help="events.jsonl 尾损诊断 + 有界自愈（--fix）")
    pc.add_argument("run_dir")
    pc.add_argument("--fix", action="store_true",
                    help="截断干净尾损到最后有效记录水位（备份原文件 .pre_fix）；"
                         "中段损坏结构性拒修")
    pc.add_argument("--yes", action="store_true",
                    help="--fix 时旁路交互确认（CI 用）；默认交互确认默认 N")
    pc.set_defaults(func=cmd_check)

    # domains
    pd = sub.add_parser("domains", parents=[common], help="域配置前置校验")
    pd_sub = pd.add_subparsers(dest="action", metavar="<action>")
    pdv = pd_sub.add_parser("validate", parents=[common], help="load_domain 通过/失败报告")
    pdv.add_argument("yaml", help="域配置 YAML 路径")
    pd.set_defaults(func=cmd_domains)

    # ui
    pu = sub.add_parser("ui", parents=[common], help="拉起只读 Streamlit 面板")
    pu.add_argument("--runs-root", help="runs/ 根目录")
    pu.add_argument("--port", type=int, default=8501)
    pu.set_defaults(func=cmd_ui)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help(sys.stderr)
        return 2
    # 全局 --json（可在子命令前或后给）：主 parser 与子 parser 共用一个 dest。
    args.json = bool(getattr(args, "json", False))
    try:
        return args.func(args)
    except ExposError as e:
        if not e.user_facing:
            raise  # 内部不变量破坏=bug，不许静默（保留响亮 traceback → exit 1）
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
