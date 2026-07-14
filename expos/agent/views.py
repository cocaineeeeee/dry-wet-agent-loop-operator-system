"""提案队列——agent 侧唯一的"输出容器"（docs/ARCHITECTURE.md §10.3）。

结构性无写权（公理 7）：本模块只操作纯内存值对象，**不持有任何 RunStore 写句柄**，
也不 import RunStore 的写方法、lifecycle 裁决函数（submit_proposal/validate_proposal/
adjudicate/reclassify/route_observation）、adapters、planner 或 models。
agent 的一切产出都是 DecisionRecord **返回值**，经 ProposalQueue 交给 planner/loop，
由 loop.py 调 lifecycle.submit_proposal 统一落账——本模块自身不定义/导出任何
save/append/write/delete/update/remove 型公有 API（守门测试强制）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from expos.errors import ExposError
from expos.kernel.objects import Actor, DecisionRecord, PROPOSAL_KINDS


class AgentError(ExposError):
    user_facing = False  # 队列误用（塞入非 agent/非提案记录）是编程 bug，CLI 不吞


@dataclass
class ProposalQueue:
    """agent 产物的中转容器：只装 DecisionRecord（actor=agent、kind∈PROPOSAL_KINDS）。

    唯一消费者是 planner/loop——drain 取出后经 lifecycle.submit_proposal 落账。
    无任何 store 引用：结构上不可能写盘（公理 7）。
    """

    _items: list[DecisionRecord] = field(default_factory=list)

    def put(self, rec: DecisionRecord) -> None:
        """入队一条 agent 提案；actor 非 agent 或 kind 非提案类一律拒绝（AgentError）。"""
        if rec.actor != Actor.AGENT:
            raise AgentError(
                f"ProposalQueue 只接受 actor=agent 的记录，收到 actor={rec.actor.value}"
            )
        if rec.kind not in PROPOSAL_KINDS:
            raise AgentError(
                f"ProposalQueue 只接受提案类 kind∈PROPOSAL_KINDS，收到 kind={rec.kind.value}"
            )
        self._items.append(rec)

    def drain(self) -> list[DecisionRecord]:
        """取出全部提案并清空队列（一次性移交给消费者）。"""
        out = self._items
        self._items = []
        return out

    def __len__(self) -> int:
        return len(self._items)
