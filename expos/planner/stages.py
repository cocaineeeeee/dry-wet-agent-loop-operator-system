"""M7 阶段 FSM（配方：REFERENCE_MAP §13.4 Ax GenerationNode/TransitionCriterion）。

纯函数 + 纯数据，**不** import loop/store/agent/adapters。照 Ax 语义：
- `StageRule` = 一个 GenerationNode（name + generator 标签 + 有序出边）；
- `transitions` 有序，**第一条满足的边胜**（Ax 边序语义）；
- 切换只改指针名（stage）并记 entered_at_round，强制 loop 侧重 fit / 换 generator；
- `decide_stage` 纯函数吃只读 `StageContext`，产 (新状态, 切换事件载荷|None)；
- 不用异常驱动控制流（Ax 的 DataRequiredError 坑）——普通 if 判定。

generator 只是字符串标签，标签→生成器/score_fn 的映射在 loop 侧。失败感知阶段
= 「gp 生成器 + 风险贴现 score_fn」组合，无需新节点类型（§13.4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from expos.errors import ExposError


class StageError(ExposError):
    """阶段规则表非法（未知 stage / 未知 transition 目标 / 空 generator）。

    加载期问题——`user_facing` 沿用 ExposError 默认（True，配置错，CLI 干净退出）。
    """


# ---------------------------------------------------------------- 只读上下文/状态

@dataclass(frozen=True)
class StageState:
    """FSM 快照（checkpoint 只增 `planner:{stage, entered_at_round}`）。"""

    stage: str
    entered_at_round: int


@dataclass(frozen=True)
class StageContext:
    """loop 每轮组装的最小只读快照——criterion 只吃它，不碰内核对象。"""

    round_id: int
    n_trusted: int
    n_suspect: int
    n_failed: int
    trusted_ratio: float
    consecutive_high_suspect_rounds: int


#: 只读判据：吃上下文、产布尔。工厂返回**具名**可调用，切换事件能报人类可读名。
Criterion = Callable[["StageContext"], bool]


@dataclass(frozen=True)
class StageRule:
    """一个 GenerationNode：generator 标签 + 有序出边（AND 内、第一条满足者胜）。

    `min_dwell`：进入本阶段后的最小驻留轮数——驻留未满则本轮抑制**所有**出边、锁定
    当前阶段（压测 J-5：trusted_ratio<0.5 持续而 streak=0 时 gp↔failure_aware 曾逐轮
    翻转，实测 8 轮 8 翻）。默认 0=无驻留约束。
    """

    name: str
    generator: str
    transitions: tuple[tuple[Criterion, str], ...] = ()
    min_dwell: int = 0


# ---------------------------------------------------------------- 具名 criterion

class _NamedCriterion:
    """具名可调用：切换事件载荷里报出 `min_trusted(8)` 这类人类可读名。"""

    __slots__ = ("name", "_fn")

    def __init__(self, name: str, fn: Callable[[StageContext], bool]) -> None:
        self.name = name
        self._fn = fn

    def __call__(self, ctx: StageContext) -> bool:
        return bool(self._fn(ctx))

    @property
    def __name__(self) -> str:  # 兼容按 __name__ 取名的调用方
        return self.name

    def __repr__(self) -> str:
        return f"<criterion {self.name}>"


def _crit_name(crit: Criterion) -> str:
    return getattr(crit, "name", None) or getattr(crit, "__name__", None) or repr(crit)


def min_trusted(n: int) -> _NamedCriterion:
    """信任观测数达到 n（sobol→gp：先攒够种子点再上代理模型）。"""
    return _NamedCriterion(f"min_trusted({n})", lambda ctx: ctx.n_trusted >= n)


def trusted_ratio_below(x: float) -> _NamedCriterion:
    """信任占比跌破 x（信任质量恶化的慢信号）。"""
    return _NamedCriterion(f"trusted_ratio_below({x})", lambda ctx: ctx.trusted_ratio < x)


def high_suspect_streak(k: int) -> _NamedCriterion:
    """连续 k 轮高嫌疑（信任恶化的快信号，触发失败感知阶段）。"""
    return _NamedCriterion(
        f"high_suspect_streak({k})",
        lambda ctx: ctx.consecutive_high_suspect_rounds >= k,
    )


def streak_cleared(k: int) -> _NamedCriterion:
    """高嫌疑连击跌回 k 以下（恢复信号，failure_aware→gp）。与 high_suspect_streak(k) 互补。"""
    return _NamedCriterion(
        f"streak_cleared({k})",
        lambda ctx: ctx.consecutive_high_suspect_rounds < k,
    )


# ---------------------------------------------------------------- 默认规则表

#: sobol →(攒够信任)→ gp →(信任恶化)→ failure_aware →(恢复)→ gp
DEFAULT_RULES: dict[str, StageRule] = {
    "sobol": StageRule(
        "sobol",
        "sobol",
        ((min_trusted(8), "gp"),),
    ),
    "gp": StageRule(
        "gp",
        "response_gp+ucb",
        (
            (high_suspect_streak(2), "failure_aware"),
            (trusted_ratio_below(0.5), "failure_aware"),
        ),
        min_dwell=2,
    ),
    "failure_aware": StageRule(
        "failure_aware",
        "response_gp+ucb+risk_discount",
        ((streak_cleared(2), "gp"),),
        min_dwell=2,
    ),
}


# ---------------------------------------------------------------- 校验 / 决策 / 描述

def validate_rules(rules: dict[str, StageRule]) -> None:
    """加载期预检：每条 transition 目标存在、无空 generator。违规抛 StageError。"""
    for name, rule in rules.items():
        if not rule.generator:
            raise StageError(f"stage {name!r} 的 generator 为空")
        if rule.min_dwell < 0:
            raise StageError(f"stage {name!r} 的 min_dwell={rule.min_dwell} 非法（须 ≥0）")
        for _crit, target in rule.transitions:
            if target not in rules:
                raise StageError(
                    f"stage {name!r} 的 transition 目标 {target!r} 不在 rules 中"
                )


def decide_stage(
    rules: dict[str, StageRule],
    state: StageState,
    ctx: StageContext,
) -> tuple[StageState, dict | None]:
    """返回 (新状态, 切换事件载荷|None)。

    有序遍历当前 stage 的出边，第一条满足的边胜（Ax 边序语义）；无边满足则
    状态不变、返回 None（确定性：同输入同输出）。未知 stage / 未知 transition
    目标 → StageError（加载期可用 validate_rules 预检）。
    """
    rule = rules.get(state.stage)
    if rule is None:
        raise StageError(f"未知 stage: {state.stage!r}")
    for crit, target in rule.transitions:
        if crit(ctx):
            # 最小驻留未满 → 抑制本轮所有出边，锁定当前阶段（防逐轮振荡，J-5）。
            # 一条边已满足但驻留未到即视为"未到切换时机"，确定性返回不变、无事件。
            if rule.min_dwell and ctx.round_id - state.entered_at_round < rule.min_dwell:
                return state, None
            if target not in rules:
                raise StageError(
                    f"stage {state.stage!r} 的 transition 目标 {target!r} 不在 rules 中"
                )
            new_state = StageState(stage=target, entered_at_round=ctx.round_id)
            event = {
                "from": state.stage,
                "to": target,
                "criterion": _crit_name(crit),
                "round_id": ctx.round_id,
            }
            return new_state, event
    return state, None


def describe(rules: dict[str, StageRule]) -> dict:
    """规则表的 JSON 化描述（供事件日志 / UI）。criterion 只报名，不含闭包。"""
    return {
        name: {
            "generator": rule.generator,
            "min_dwell": rule.min_dwell,
            "transitions": [
                {"criterion": _crit_name(crit), "to": target}
                for crit, target in rule.transitions
            ],
        }
        for name, rule in rules.items()
    }
