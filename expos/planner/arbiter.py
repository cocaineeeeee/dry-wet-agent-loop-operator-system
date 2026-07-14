"""M7 规划器仲裁模块（权威规格：docs/ARCHITECTURE.md §9 四步仲裁；
REFERENCE_MAP §11.5 失败感知规划器 / §13.8 Atlas fwa / §13.2 QueueItem 配方）。

四步仲裁的第 1–2 步落地——**动作队列收集 + 预算封顶仲裁 + 风险贴现采集 + ε 探索配额**：

1. `collect_actions`（§9 步1 + §13.2 QueueItem）：收集上一轮 SUSPECT/FAILED 观测的
   `next_action`（endogenous）与已 accept 的 agent ACTION_PROPOSAL（DecisionRecord 的
   refs/content 解析），去重（同 target 同 action 保 priority 高者）、按 priority 降序。
   动作项带 `semantics`（detour|addition，§13.2：REMEASURE/DISAMBIGUATION_REPEAT=detour
   顶替旧判、ADD_CONTROLS/REPEAT_CANDIDATE=addition 纯扩展）与 `supersedes`。
2. `arbitrate`（§9 步1 预算 ≤ max_action_frac + §11.5 KG 框架统一排序）：入选动作按孔预算
   封顶，换算成候选占孔数（detour 复测=replicates 孔、edge_center_pair=2 孔等——按
   placement_hint），返回 (入选, 溢出)。
3. `discounted_scores`（§13.8 Atlas fwa 直接移植）：scores **先 min-max 归一化**再乘
   `max(1−p, 0.5)`——归一化防量纲失衡、min-filter 防可行性项独裁。p 用失败模型的
   `p_artifact_optimistic`（乐观界，§11.5 缓解 RAHBO 覆盖偏差）。
4. `exploration_quota`（§11.5 RAHBO 缓解②）：每轮强制预留 ε≈10–15% 预算给"折扣后排名靠
   后但认知不确定性高"的区域。

依赖隔离红线：只依赖 kernel.objects 与 numpy/标准库；**不 import** loop/adapters/models
以及 agent 的**写面**（只经 kernel 类型消费其已 accept 的 DecisionRecord），不含任何真值旁路。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from expos.errors import ExposError
from expos.kernel.objects import (
    ActionType,
    Candidate,
    DecisionKind,
    DecisionRecord,
    ObservationObject,
    TrustLevel,
)


class ArbiterError(ExposError):
    """仲裁的响亮失败：动作提案无法解析、target 候选查表缺失等一律抛此异常而非静默。"""


# ---------------------------------------------------------------- 语义映射（§13.2）

#: FireWorks FWAction 图语义映射（§13.2）：detour 顶替旧判并门控归因、addition 纯扩展。
_SEMANTICS: dict[ActionType, str] = {
    ActionType.REMEASURE: "detour",
    ActionType.DISAMBIGUATION_REPEAT: "detour",
    ActionType.REPEAT_CANDIDATE: "addition",
    ActionType.ADD_CONTROLS: "addition",
    ActionType.NEW_CANDIDATES: "addition",
}

#: 需要向候选表查参的动作（复测/消歧/重复候选都是"重跑某候选条件"）。
_NEEDS_CANDIDATE = (
    ActionType.REMEASURE,
    ActionType.DISAMBIGUATION_REPEAT,
    ActionType.REPEAT_CANDIDATE,
)

_POSITIVE_TRUST = (TrustLevel.SUSPECT, TrustLevel.FAILED)


# ---------------------------------------------------------------- 提案 content 共用校验

#: design/layout.py 认可的 placement_hint 全集（None=默认复测布局）——与 layout 的
#: `c.placement_hint not in (None, "center_only", "edge_center_pair")` 守卫同源。
_VALID_PLACEMENT_HINTS = (None, "center_only", "edge_center_pair")

#: well_cost / 装配当作正整数消费的计数参数——非正整数会在装配期崩或算出负孔预算。
_COUNT_PARAMS = ("n_wells", "n", "n_controls")

#: 坏提案降级留痕回调：(decision_id, 中文理由) → None（arbiter 保持纯净，由调用方落事件）。
SkipSink = Callable[[str, str], None]


def _is_positive_int(v) -> bool:
    """严格正整数（bool 不算，浮点须为整值且 >0）。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v > 0
    if isinstance(v, float):
        return math.isfinite(v) and v > 0 and v.is_integer()
    return False


def _is_finite_number(v) -> bool:
    """有限实数（拒 NaN/inf/非数值；bool 作 0/1 有限数放行）。"""
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return math.isfinite(float(v))
    return False


def _clamp01(x: float) -> float:
    """钳 [0,1]（入参须已保证有限——见 validate_proposal_content 的 priority 面）。"""
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def validate_proposal_content(content) -> str | None:
    """agent ACTION_PROPOSAL 的 content **全解析面**校验（裁定闸门与装配侧共用）。

    返回中文拒绝理由（invalid）或 None（合法）。覆盖 _agent_items / well_cost /
    actions_to_candidates 会触碰的每一个字段——历史上裁定闸门只查了 action 与 target
    两项，`params:"oops"`（非 dict）、计数参数非正整数、非法 placement_hint、priority
    非有限值都能穿过闸门；被 accept 后因决策 append-only 每轮重放，在装配期
    `dict("oops")` 裸抛 ValueError 永久打停闭环（压测 J-2/J-4）。"""
    if not isinstance(content, dict):
        return f"content 必须是 dict，实为 {type(content).__name__}"
    raw_action = content.get("action")
    if raw_action is None:
        return "content 缺 'action' 字段，无法解析成动作项"
    try:
        ActionType(raw_action)
    except (TypeError, ValueError):
        return f"action={raw_action!r} 非合法 ActionType"
    params = content.get("params")
    if params is not None and not isinstance(params, dict):
        return f"params 必须是 dict（或缺省），实为 {type(params).__name__}"
    params = params or {}
    for k in _COUNT_PARAMS:
        if k in params and not _is_positive_int(params[k]):
            return f"params[{k!r}]={params[k]!r} 非正整数"
    hint = params.get("placement_hint")
    if hint not in _VALID_PLACEMENT_HINTS:
        return f"placement_hint={hint!r} 非法（合法：center_only / edge_center_pair / 缺省）"
    if "priority" in content and not _is_finite_number(content["priority"]):
        return f"priority={content['priority']!r} 非有限数值（拒 NaN/inf/非数值）"
    return None


def materializes_candidate(item: "ActionItem") -> bool:
    """该动作是否会被 actions_to_candidates 物化成一个候选（占用候选容量）。
    规划器用它做候选容量二次封顶——孔预算过闸不等于候选容量过闸。"""
    return item.action in _NEEDS_CANDIDATE


# ---------------------------------------------------------------- 动作项数据类

@dataclass(frozen=True)
class ActionItem:
    """仲裁队列项（§13.2 QueueItem 配方的内核侧物化）。

    - `semantics`：detour|addition（由 action 经 `_SEMANTICS` 派生，§13.2）；
    - `supersedes`：**记账字段，顶替语义未实现**（J-7；Backlog M13）。记录 detour 动作
      牵连的旧观测/判定 id 元组（addition 通常空），当前唯一效果 = 审计可见；全库无任何
      消费者读它来"顶替旧判/门控归因"——docstring 早先承诺的"detour 顶替旧判"未落地。
      好消息：agent 也无法借提案的 supersedes 静默翻旧判（这条路同样不通）。锁定现状的
      显式测试见 tests/test_planner_arbiter.py::test_supersedes_is_bookkeeping_only，
      防未来静默启用绕过信任语义；真要实现顶替须走 M13 并同步信任/训练集排除契约；
    - `source`：endogenous（内生 next_action）| agent（已 accept 的 ACTION_PROPOSAL）；
    - `priority`：归因置信度（endogenous）或提案权重（agent），仲裁排序键。
    """

    item_uid: str
    action: ActionType
    semantics: str
    target_cand_id: str | None
    target_obs_id: str | None
    params: dict
    placement_hint: str | None
    supersedes: tuple[str, ...]
    source: str
    priority: float


def _semantics_of(action: ActionType) -> str:
    return _SEMANTICS.get(action, "addition")


def _dedupe_key(item: ActionItem) -> tuple[str, str]:
    """去重键：同 target 同 action 视为同一动作（§9 步1 队列去重）。

    target 优先用候选 id（复测/消歧的第一性归属），退回观测 id。"""
    target = item.target_cand_id or item.target_obs_id or ""
    return (item.action.value, target)


# ---------------------------------------------------------------- 步1：collect

def _endogenous_items(observations: list[ObservationObject]) -> list[ActionItem]:
    """上一轮 SUSPECT/FAILED 观测的 next_action → ActionItem（source=endogenous）。

    priority = 归因置信度（failure_attr.confidence 优先，退回 trust_confidence，§9 步1
    "按归因置信度排序"）。detour 动作把被牵连观测记入 supersedes（§13.2 顶替旧判）。
    """
    out: list[ActionItem] = []
    for obs in observations:
        if obs.trust not in _POSITIVE_TRUST:
            continue
        act = obs.next_action
        if act is None or act.action == ActionType.NONE:
            continue
        semantics = _semantics_of(act.action)
        priority = (
            obs.failure_attr.confidence
            if obs.failure_attr is not None and obs.failure_attr.confidence
            else obs.trust_confidence
        )
        placement_hint = act.params.get("placement_hint")
        supersedes = (obs.obs_id,) if semantics == "detour" else ()
        out.append(
            ActionItem(
                item_uid=f"endogenous:{act.action.value}:{obs.cand_id or obs.obs_id}",
                action=act.action,
                semantics=semantics,
                target_cand_id=obs.cand_id,
                target_obs_id=obs.obs_id,
                params=dict(act.params),
                placement_hint=placement_hint,
                supersedes=supersedes,
                source="endogenous",
                priority=float(priority),
            )
        )
    return out


def _agent_items(
    accepted_agent_proposals: list[DecisionRecord],
    on_skip: SkipSink | None = None,
) -> list[ActionItem]:
    """已 accept 的 agent ACTION_PROPOSAL → ActionItem（source=agent）。

    content 契约（见 agent/backends.py TemplateBackend.suggest）：{action, target, obs_id,
    params, reason[, priority]}，全解析面经 `validate_proposal_content` 校验。

    坏提案不再裸抛（压测 J-2）：正常路径下裁定闸门 `_adjudicate_proposals` 已用同一
    校验函数把坏提案 reject 在门外；这里消费的是**已 accept**集合，若仍撞见坏解析
    （修复前的历史坏 accept），降级为 reject-after-the-fact——跳过 + 经 `on_skip` 回调
    留痕，**绝不裸抛打停 append-only 重放的闭环**。priority 经 [0,1] 钳位（拒 NaN/inf
    已在校验层完成，防 1e9 压倒内生补救，J-4）。
    """
    out: list[ActionItem] = []
    for prop in accepted_agent_proposals:
        if prop.kind != DecisionKind.ACTION_PROPOSAL:
            continue
        content = prop.content or {}
        reason = validate_proposal_content(content)
        if reason is not None:
            if on_skip is not None:
                on_skip(prop.decision_id, reason)
            continue
        action = ActionType(content["action"])  # 已校验合法
        target = content.get("target")
        obs_id = content.get("obs_id") or (
            target if isinstance(target, str) and target.startswith("obs") else None
        )
        cand_id = content.get("cand_id") or (
            target if isinstance(target, str) and target.startswith("cand") else None
        )
        params = dict(content.get("params") or {})
        semantics = _semantics_of(action)
        supersedes = (obs_id,) if (semantics == "detour" and obs_id) else ()
        out.append(
            ActionItem(
                item_uid=f"agent:{prop.decision_id}",
                action=action,
                semantics=semantics,
                target_cand_id=cand_id,
                target_obs_id=obs_id,
                params=params,
                placement_hint=params.get("placement_hint"),
                supersedes=supersedes,
                source="agent",
                priority=_clamp01(float(content.get("priority", 0.5))),
            )
        )
    return out


def _dedupe_prefer(new: ActionItem, cur: ActionItem) -> bool:
    """同 (action,target) 去重的优先规则：`new` 是否应顶替在位的 `cur`。

    内生项恒优先于 agent 项（压测 J-4：防高 priority 的 agent 提案替换掉内生补救的
    消歧几何）；同源则 priority 高者胜、并列保先遇者。
    """
    new_endo = new.source == "endogenous"
    cur_endo = cur.source == "endogenous"
    if new_endo != cur_endo:
        return new_endo
    return new.priority > cur.priority


def collect_actions(
    observations: list[ObservationObject],
    accepted_agent_proposals: list[DecisionRecord],
    on_skip: SkipSink | None = None,
) -> list[ActionItem]:
    """收集内生 + 已 accept 的 agent 动作，去重后按 priority 降序（§9 步1；§13.2）。

    去重：同 target 同 action，内生项恒优先、否则 priority 高者胜（并列保先到者，确定性）。
    排序确定性：priority 降序、并列按 item_uid 升序（同输入同输出）。坏 agent 提案经
    `on_skip` 降级留痕（见 `_agent_items`），不打停闭环。
    """
    items = _endogenous_items(observations) + _agent_items(
        accepted_agent_proposals, on_skip
    )
    best: dict[tuple[str, str], ActionItem] = {}
    for it in items:
        key = _dedupe_key(it)
        cur = best.get(key)
        if cur is None or _dedupe_prefer(it, cur):
            best[key] = it
    # 确定性排序：priority 降序、并列 item_uid 升序。
    return sorted(best.values(), key=lambda it: (-it.priority, it.item_uid))


# ---------------------------------------------------------------- 步2：arbitrate

def well_cost(item: ActionItem, replicates: int) -> int:
    """动作换算成候选占孔数（§9 步2 装配 / §13.2 语义）。

    - placement_hint == 'edge_center_pair'：歧义消解钉边+中心对 → 2 孔；
    - ADD_CONTROLS：params['n_controls']（默认 1）；
    - NEW_CANDIDATES：params['n']（默认 1）；
    - 其余 detour/repeat 复测：params['n_wells']（默认 replicates 孔）。
    """
    if item.placement_hint == "edge_center_pair":
        return 2
    if item.action == ActionType.ADD_CONTROLS:
        return int(item.params.get("n_controls", 1))
    if item.action == ActionType.NEW_CANDIDATES:
        return int(item.params.get("n", 1))
    return int(item.params.get("n_wells", replicates))


def arbitrate(
    actions: list[ActionItem],
    n_wells_for_actions: int,
    replicates: int,
) -> tuple[list[ActionItem], list[ActionItem]]:
    """预算封顶仲裁：按 priority 序贪心入选，占孔数累计 ≤ n_wells_for_actions（§9 步1）。

    `n_wells_for_actions` = 调用方按 max_action_frac 算好的动作孔预算（本模块不算比例，
    只封顶——预算归属单一真源在 loop/budget 侧）。返回 (入选, 溢出)；溢出保持原序，
    供事件日志留痕/下轮重竞（§13.7 AlabOS 显式重入队纪律）。确定性：同输入同输出。
    """
    if n_wells_for_actions < 0:
        raise ArbiterError(f"n_wells_for_actions={n_wells_for_actions} 非法，孔预算须 ≥0")
    if replicates <= 0:
        raise ArbiterError(f"replicates={replicates} 非法，复测孔数须 >0")
    admitted: list[ActionItem] = []
    overflow: list[ActionItem] = []
    used = 0
    for it in actions:
        cost = well_cost(it, replicates)
        if used + cost <= n_wells_for_actions:
            admitted.append(it)
            used += cost
        else:
            overflow.append(it)
    return admitted, overflow


# ---------------------------------------------------------------- 步3：风险贴现

def discounted_scores(scores: np.ndarray, p_artifact_opt) -> np.ndarray:
    """Atlas fwa 采集折扣（§13.8 直接移植）：scores 先 min-max 归一化，再乘
    `max(1−p, 0.5)`（min-filter 防可行性项独裁）。

    - 归一化：`(s − min)/(max − min)`；全等（退化）→ 全 1（保持无偏好、不被折扣清零）；
    - 折扣因子：`np.maximum(1 − p, 0.5)`，逐元素钳 ≥0.5——p=0.9 时折扣恰钳在 0.5；
    - `p_artifact_opt` 用失败模型的乐观下界（§11.5），可为标量（整批同折扣）或逐孔数组
      （广播）。
    """
    s = np.asarray(scores, dtype=float).ravel()
    if s.size == 0:
        return s
    # 入口断言（压测 J-6）：一个 NaN 会让 min-max 归一化产 NaN、下游 argsort 静默乱序；
    # p<0 令折扣因子 >1 反转为放大。两者都必须响亮失败，不许静默破坏全体排序。
    if not np.all(np.isfinite(s)):
        raise ArbiterError(
            "discounted_scores: scores 含非有限值（NaN/inf）——拒绝静默破坏排序"
        )
    p = np.asarray(p_artifact_opt, dtype=float)
    if not np.all(np.isfinite(p)):
        raise ArbiterError(
            f"discounted_scores: p_artifact_opt 含非有限值（NaN/inf）: {p_artifact_opt!r}"
        )
    if np.any(p < 0.0) or np.any(p > 1.0):
        raise ArbiterError(
            f"discounted_scores: p_artifact_opt 越界 [0,1]（p<0 会反转折扣为放大）: {p_artifact_opt!r}"
        )
    lo = float(s.min())
    hi = float(s.max())
    norm = (s - lo) / (hi - lo) if hi > lo else np.ones_like(s)
    factor = np.maximum(1.0 - p, 0.5)
    return norm * factor


# ---------------------------------------------------------------- 步4：ε 探索配额

def exploration_quota(n_total: int, frac: float = 0.12) -> int:
    """ε 强制探索配额（§11.5 RAHBO 缓解②）：预留 frac 份预算给"折扣后排名靠后但认知
    不确定性高"的区域。四舍五入（round-half-up，确定性），钳 [0, n_total]。"""
    if n_total < 0:
        raise ArbiterError(f"n_total={n_total} 非法，总孔数须 ≥0")
    if not 0.0 <= frac <= 1.0:
        raise ArbiterError(f"frac={frac} 非法，探索配额比例须 ∈ [0,1]")
    q = int(math.floor(n_total * frac + 0.5))
    return max(0, min(q, n_total))


# ---------------------------------------------------------------- 步2→装配：候选

def actions_to_candidates(
    items: list[ActionItem],
    params_lookup: dict[str, dict],
) -> list[Candidate]:
    """入选动作 → Candidate（§9 步2 装配）。

    复测/消歧/重复候选（`_NEEDS_CANDIDATE`）从 `params_lookup` 按 target_cand_id 查参数
    表复刻条件——**查不到（或无 target_cand_id）响亮抛 ArbiterError**（不静默丢动作，
    "bug 不许静默"红线）。Candidate 带 placement_hint 与 source 标记（arbiter:<source>）、
    parent_obs_id 回填 target_obs_id（§13.2 反向账 created_by 溯源的候选侧锚点）。

    ADD_CONTROLS / NEW_CANDIDATES 不在此换算（对照增补 / 全新点由规划器采集另路装配），
    跳过——本函数只物化"重跑某已知候选条件"的动作。
    """
    out: list[Candidate] = []
    for it in items:
        if it.action not in _NEEDS_CANDIDATE:
            continue
        if it.target_cand_id is None:
            raise ArbiterError(
                f"动作 {it.item_uid}（{it.action.value}）无 target_cand_id，无法查参装配候选"
            )
        params = params_lookup.get(it.target_cand_id)
        if params is None:
            raise ArbiterError(
                f"动作 {it.item_uid} 的 target_cand_id={it.target_cand_id!r} 不在 params_lookup 中"
                f"——候选参数查表失败，拒绝静默"
            )
        out.append(
            Candidate(
                params=dict(params),
                source=f"arbiter:{it.source}",
                rationale=f"{it.action.value}（{it.semantics}）复刻候选 {it.target_cand_id} 条件",
                placement_hint=it.placement_hint,
                parent_obs_id=it.target_obs_id,
            )
        )
    return out
