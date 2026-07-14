"""人类改判 pending 通道的消费端（REFERENCE_MAP §13.13；STRESS_TEST_R1 P2「CLI override 死投递」修复）。

`expos.cli override` 只向 `<run_root>/overrides/pending/` 原子投递提案文件；
本模块是**唯一消费者**：run_loop 每轮开始（plan_round 之前）调用
consume_pending_overrides——消费发生在规划前，本轮规划立即看到人类改判。

红线：
- 仅 actor=="human" 的投递被接受（agent 伪造 actor 的文件 → rejected，公理 7）；
- 消费者只做**合法性校验**（schema/枚举/组合/存在性/陈旧检查），不做任何裁决
  判断——改判语义全部走 lifecycle.reclassify（自动落 reclassification 事件
  + OVERRIDE DecisionRecord，这是既有的唯一合法通道）；
- 非法投递绝不静默丢：移入 overrides/rejected/、文件内写明 reject_reason，
  并 logging.warning 响亮告警。

幂等：applied/ 里已有同名文件 → 跳过（同文件重复消费不双改判）。崩溃窗口
（reclassify 已落账、applied/ 未写成）重放会再追加一次同向改判事件——审计
只多不丢（append-only 安全方向的偏斜）。

路由约定（单一事实来源 = lifecycle.LEGAL_TRUST_ROUTING，即 adjudicate 只产出的组合）：
    TRUSTED → TO_RESPONSE_MODEL
    SUSPECT → TO_FAILURE_MODEL | QUARANTINE
    FAILED  → TO_FAILURE_MODEL
to_routing 键必填；值为 null（CLI 不带 --routing 的 README 形态）→ 按上表
取该 trust 的保守默认（SUSPECT 默认 QUARANTINE）。

转移合法性（STRESS_TEST_R1 P2「reclassify 绕状态机」联动）：消费端在投递入口
就用 lifecycle.check_trust_transition 预检 from_trust→to_trust（PENDING 观测不可
override、组合须在表内）——非法投递落 rejected/ 而不是让 reclassify 在轮中响亮炸。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from expos.errors import ExposError
from expos.kernel.lifecycle import (
    LEGAL_TRUST_ROUTING,
    LifecycleError,
    check_trust_transition,
    reclassify,
)
from expos.kernel.objects import Actor, Routing, TrustLevel, utc_now
from expos.kernel.store import RunStore

_log = logging.getLogger("expos.kernel.overrides")

#: obs_id 白名单（同时封死 P2-E 的路径逃逸：`--obs ../../x` 类投递直接拒）。
_OBS_ID_RE = re.compile(r"[A-Za-z0-9_\-]+")

_REQUIRED_FIELDS = ("obs_id", "to_trust", "to_routing", "reason", "actor")

#: to_routing 为 null 时按 trust 取的保守默认（SUSPECT 取隔离而非失败模型正例）。
_DEFAULT_ROUTING: dict[TrustLevel, Routing] = {
    TrustLevel.TRUSTED: Routing.TO_RESPONSE_MODEL,
    TrustLevel.SUSPECT: Routing.QUARANTINE,
    TrustLevel.FAILED: Routing.TO_FAILURE_MODEL,
}

#: base_version（文件 mtime 浮点）比对容差：json 往返保双精度，同文件未动则逐位相等；
#: 容差只为吸收极端文件系统精度差，不放宽"改过即陈旧"的语义。
_MTIME_TOL = 1e-6


class OverrideError(ExposError):
    """消费端自身的不变量破坏（如 store 与 run_root 指向不同目录）——编程 bug，响亮。"""

    user_facing = False


class _Reject(Exception):
    """单个提案文件的校验失败——携带人读理由，落入 rejected/。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _validate(store: RunStore, data: Any) -> tuple[str, TrustLevel, Routing, str]:
    """合法性校验（≠裁决）：schema/actor/存在性/枚举/组合/陈旧。通过则返回
    (obs_id, to_trust, to_routing, reason)，失败抛 _Reject(理由)。"""
    if not isinstance(data, dict):
        raise _Reject("提案不是 JSON 对象")
    missing = [k for k in _REQUIRED_FIELDS if k not in data]
    if missing:
        raise _Reject(f"缺必填字段: {missing}")
    if data["actor"] != "human":
        raise _Reject(
            f"actor={data['actor']!r} 非 human——仅人类投递可被接受（公理 7：agent 只有建议权）"
        )
    obs_id = data["obs_id"]
    if not isinstance(obs_id, str) or not _OBS_ID_RE.fullmatch(obs_id):
        raise _Reject(f"obs_id 非法（须匹配 [A-Za-z0-9_-]+，防路径逃逸）: {obs_id!r}")
    obs_path = store.root / "observations" / f"{obs_id}.json"
    if not obs_path.is_file():
        raise _Reject(f"观测不存在于 store: {obs_id}")
    try:
        obs = store.load_observation(obs_id)  # 确认可解析——坏观测文件不进 reclassify
    except ValueError as e:  # pydantic ValidationError ⊂ ValueError
        raise _Reject(f"观测文件不可解析: {obs_id}（{e}）") from e

    try:
        to_trust = TrustLevel(data["to_trust"])
    except ValueError as e:
        raise _Reject(f"to_trust 非法枚举: {data['to_trust']!r}") from e
    if to_trust not in LEGAL_TRUST_ROUTING:
        raise _Reject(f"to_trust={to_trust.value} 不是合法改判目标（PENDING 不可作为终态）")

    raw_routing = data["to_routing"]
    if raw_routing is None:
        to_routing = _DEFAULT_ROUTING[to_trust]  # README 无 --routing 形态：按约定取默认
    else:
        try:
            to_routing = Routing(raw_routing)
        except ValueError as e:
            raise _Reject(f"to_routing 非法枚举: {raw_routing!r}") from e

    reason = data["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise _Reject("reason 必填且非空（审计不变量）")

    if obs.qc is None:
        raise _Reject(f"观测 {obs_id} 无 QCReport（PENDING 首判走 route_observation，不可 override）")
    try:
        # 单一守卫入口：from→to 转移表 + trust×routing 组合 + 高危 reason 要求
        #（actor 已在上方强制为 human，human 全表可发起）。
        check_trust_transition(obs.trust, to_trust, to_routing, Actor.HUMAN, reason)
    except LifecycleError as e:
        raise _Reject(str(e)) from e

    base_version = data.get("base_version")
    if base_version is not None:
        current = os.path.getmtime(obs_path)
        if abs(current - float(base_version)) > _MTIME_TOL:
            raise _Reject(
                f"陈旧投递: base_version={base_version} ≠ 观测当前 mtime={current}"
                "——投递后观测已被改写（乐观并发冲突），请基于新状态重新投递"
            )
    return obs_id, to_trust, to_routing, reason


def _atomic_write_json(dest: Path, payload: dict[str, Any]) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, dest)


def _reject_file(rejected_dir: Path, path: Path, data: Any, reason: str) -> None:
    """移入 rejected/：保留原文件名与原内容（不可解析则存 raw_text），
    附加 reject_reason/rejected_at；响亮 logging.warning，绝不静默丢。"""
    rejected_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(data) if isinstance(data, dict) else {"raw_text": str(data)}
    payload["reject_reason"] = reason
    payload["rejected_at"] = utc_now()
    _atomic_write_json(rejected_dir / path.name, payload)
    path.unlink()
    _log.warning("override 投递被拒: %s — %s", path.name, reason)


def consume_pending_overrides(store: RunStore, run_root: str | Path) -> list[dict[str, Any]]:
    """扫 `<run_root>/overrides/pending/*.json`，逐个校验并处置，返回处置摘要列表。

    - 合法 → lifecycle.reclassify(actor=HUMAN)（落 reclassification 事件 +
      OVERRIDE DecisionRecord）→ 文件重写（附 applied_at/applied_routing）移入 applied/；
    - 非法 → 移入 rejected/ 写 reject_reason + logging.warning；
    - applied/ 已有同名 → 跳过不双改判（幂等），清掉 pending 重复件。

    摘要元素: {"file", "status": "applied"|"rejected"|"skipped_duplicate", ...}。
    """
    root = Path(run_root)
    if root.resolve() != store.root.resolve():
        raise OverrideError(
            f"store.root={store.root} 与 run_root={root} 不一致——消费端只服务同一运行目录"
        )
    pending_dir = root / "overrides" / "pending"
    applied_dir = root / "overrides" / "applied"
    rejected_dir = root / "overrides" / "rejected"
    if not pending_dir.is_dir():
        return []

    summaries: list[dict[str, Any]] = []
    for path in sorted(pending_dir.glob("*.json")):
        name = path.name
        if (applied_dir / name).is_file():
            # 幂等红线：同名已应用 → 不再 reclassify；pending 重复件清除（canonical 在 applied/）。
            _log.warning("override 重复投递跳过（applied/ 已有同名，不双改判）: %s", name)
            path.unlink()
            summaries.append({"file": name, "status": "skipped_duplicate"})
            continue

        raw_text = path.read_text(encoding="utf-8")
        try:
            data: Any = json.loads(raw_text)
        except json.JSONDecodeError as e:
            _reject_file(rejected_dir, path, raw_text, f"JSON 不可解析: {e}")
            summaries.append({"file": name, "status": "rejected",
                              "reject_reason": f"JSON 不可解析: {e}"})
            continue

        try:
            obs_id, to_trust, to_routing, reason = _validate(store, data)
        except _Reject as rej:
            _reject_file(rejected_dir, path, data, rej.reason)
            summaries.append({"file": name, "status": "rejected",
                              "obs_id": data.get("obs_id") if isinstance(data, dict) else None,
                              "reject_reason": rej.reason})
            continue

        # 改判语义全部走既有合法通道：reclassification 事件 + OVERRIDE decision 由它落账。
        reclassify(store, obs_id, to_trust, to_routing,
                   actor=Actor.HUMAN, reason=reason, refs=[f"override_file:{name}"])
        applied_dir.mkdir(parents=True, exist_ok=True)
        data["applied_at"] = utc_now()
        data["applied_routing"] = to_routing.value  # to_routing=null 时记录派生结果
        _atomic_write_json(applied_dir / name, data)  # 先落 applied（重放见同名即跳过）
        path.unlink()
        _log.info("override 已应用: %s obs=%s → trust=%s routing=%s",
                  name, obs_id, to_trust.value, to_routing.value)
        summaries.append({"file": name, "status": "applied", "obs_id": obs_id,
                          "to_trust": to_trust.value, "to_routing": to_routing.value})
    return summaries
