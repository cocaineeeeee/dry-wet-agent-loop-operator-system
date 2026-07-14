"""Agent 编排策略（M8 接线，docs/ARCHITECTURE.md §10）——loop 的第四个策略注入点。

红线（公理 7）：agent 只有**提案权与解释权**。本模块只经两条合法通道写账：
- lifecycle.submit_proposal：提案类 DecisionRecord（入队等待 planner/human 裁定）；
- store.append_decision：ROUND_RATIONALE 轮次叙述（非提案、非裁决，纯解释记录）。
不碰观测 trust/routing、不碰响应模型/失败模型数据、不读真值旁路（export_view 结构性无真值）。
naive/robust 臂注入 NullAgentPolicy（零行为）——loop 主体保持零 mode 分支（DEEP_REVIEW §3.2）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from expos.agent.backends import AgentBackend, TemplateBackend
from expos.kernel.lifecycle import submit_proposal
from expos.kernel.objects import ExperimentObject, TrustLevel
from expos.kernel.store import RunStore


@runtime_checkable
class LoopAgentPolicy(Protocol):
    """裁决落账后、聚合重训前调用：agent 观察本轮 → 提案 → 叙述。"""

    def after_round(
        self, store: RunStore, exp: ExperimentObject, round_id: int
    ) -> None: ...


class NullAgentPolicy:
    """naive/robust 臂：无 agent 参与（对照臂公平性——除策略对象外代码全共享）。"""

    def after_round(
        self, store: RunStore, exp: ExperimentObject, round_id: int
    ) -> None:
        return None


class TemplateAgentPolicy:
    """os 臂：ingest 冻结视图 → 本轮 SUSPECT 的 ACTION_PROPOSAL 入提案队列
    → ROUND_RATIONALE 叙述落账。裁定发生在下一轮 TrustAwarePlanner.plan_round 开场
    （agent 永不自裁——ADJUDICATOR_ACTORS 在日志层强制）。"""

    def __init__(self, backend: AgentBackend | None = None, batch_size: int = 3):
        self.backend = backend or TemplateBackend()
        self.batch_size = batch_size

    def after_round(
        self, store: RunStore, exp: ExperimentObject, round_id: int
    ) -> None:
        view = store.export_view()
        self.backend.ingest(view)
        # 只提交**本轮**嫌疑观测的提案：跨轮旧嫌疑的动作已在往轮入队/裁定过，
        # 重复提交会因 decision_id 含 round_id 而绕开幂等、堆积重复提案。
        # 截断次序（对抗审查 finding）：backend.suggest 的 batch_size 截断发生在
        # 全历史 SUSPECT 上——旧轮嫌疑会挤光本轮名额，故先放开枚举、过滤后再封顶。
        this_round = {
            o.obs_id
            for o in view.observations_by_trust(TrustLevel.SUSPECT)
            if o.round_id == round_id
        }
        submitted = 0
        for prop in self.backend.suggest(
            view, round_id, batch_size=max(1, len(view.observations))
        ):
            if (prop.content or {}).get("obs_id") not in this_round:
                continue
            submit_proposal(store, prop)
            submitted += 1
            if submitted >= self.batch_size:
                break
        # n_submitted is this round's real submit_proposal count — narrate_round must
        # not derive it from candidate counts (NARR3 red-team P2 fix, mailbox/red_to_blue/029:
        # narrative previously implied all identified suggestions were "enqueued", but
        # submission is capped by batch_size above).
        store.append_decision(
            self.backend.narrate_round(view, round_id, n_submitted=submitted)
        )
