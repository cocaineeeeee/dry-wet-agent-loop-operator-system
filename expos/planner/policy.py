"""规划器策略（M7 接线，docs/ARCHITECTURE.md §9）——loop 的第三个策略注入点。

- BaselinePlanner：M4 行为逐字段原样提取（Sobol 起步 → UCB 池选 + κ 调度），
  naive/robust 臂共用——回归红线：generator/acquisition/候选序列不变；
- TrustAwarePlanner：os 臂——阶段 FSM（§13.4 Ax 配方）+ 动作仲裁（§13.2/§11.5）
  + Kriging Believer 批量选点（§13.9）+ 风险折扣（Atlas fwa，§13.8）
  + ε 探索配额（RAHBO 缓解，§11.5）+ 失败模型 risk_map 喂布局。

依赖方向：planner → {kernel, design, models, qc.failure_model}；不 import loop/agent/adapters。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from expos.design.sampler import _feasible_pool, propose_candidates, sobol_candidates
from expos.design.space import from_unit
from expos.domain import DomainConfig
from expos.errors import ExposError
from expos.kernel.lifecycle import (
    accepted_proposals,
    unresolved_proposals,
    validate_proposal,
)
from expos.kernel.objects import (
    ActionType,
    Actor,
    Candidate,
    DecisionKind,
    DesignProvenance,
    ExperimentObject,
    ObservationObject,
    TrustLevel,
)
from expos.kernel.store import RunStore
from expos.models.response_gp import ResponseModel
from expos.planner.arbiter import (
    _NEEDS_CANDIDATE,
    actions_to_candidates,
    arbitrate,
    collect_actions,
    discounted_scores,
    exploration_quota,
    materializes_candidate,
    validate_proposal_content,
    well_cost,
)
from expos.planner.stages import (
    DEFAULT_RULES,
    StageContext,
    StageState,
    decide_stage,
    validate_rules,
)
from expos.qc.failure_model import FailureModel


class PlannerError(ExposError):
    pass


#: 模型接管候选生成前所需的最少可信观测数（自 loop 迁移，语义不变）
MIN_TRAIN_FOR_BO = 8
#: 动作占用孔位预算上限比例（ARCHITECTURE §9）
MAX_ACTION_FRAC = 0.30
#: "高嫌疑轮"判定阈（喂 StageContext 的 streak 统计）
HIGH_SUSPECT_RATIO = 0.25


@dataclass
class PlanContext:
    """loop 每轮组装的规划输入（只读消费）。"""

    cfg: DomainConfig
    store: RunStore
    model: ResponseModel
    round_id: int
    seed: int
    n_cands: int
    kappa: float


@dataclass
class PlanResult:
    candidates: list[Candidate]
    provenance: DesignProvenance
    risk_map: dict[str, float] | None  # 喂 LayoutPlanner；None=不启用风险避让


@runtime_checkable
class PlannerPolicy(Protocol):
    name: str

    def plan_round(self, ctx: PlanContext) -> PlanResult: ...

    def checkpoint_state(self) -> dict[str, Any]: ...

    def restore_state(self, state: dict[str, Any]) -> None: ...


# ================================================================ naive/robust 臂

class BaselinePlanner:
    """M4 设计块的逐字段迁移：Sobol 起步 → UCB 池选（generator/acquisition/
    候选序列与 M4 完全一致——e2e 回归红线）。无阶段 FSM、无动作、无风险图。"""

    name = "baseline"

    def plan_round(self, ctx: PlanContext) -> PlanResult:
        from expos.loop import derive_seed  # 局部导入避免环（loop→planner→loop）

        gen_seed = derive_seed(ctx.seed, "gen", ctx.round_id)
        if ctx.model.n_train >= MIN_TRAIN_FOR_BO:
            ctx.model.kappa = ctx.kappa
            cands = propose_candidates(
                ctx.cfg.design_space, ctx.n_cands, seed=gen_seed,
                score_fn=ctx.model.score_pool, restrictions=ctx.cfg.restrictions,
            )
            prov = DesignProvenance(
                generator="response_gp+ucb",
                acquisition=f"ucb(kappa={ctx.model.kappa:.2f})",
                model_snapshot=ctx.model.snapshot(), based_on_obs=ctx.model.n_train,
                rationale="naive 基线：全体观测视为可信的 BO 轮",
            )
        else:
            cands = sobol_candidates(
                ctx.cfg.design_space, ctx.n_cands, seed=gen_seed,
                restrictions=ctx.cfg.restrictions,
            )
            prov = DesignProvenance(
                generator="sobol", based_on_obs=ctx.model.n_train,
                rationale="初始空间填充（可信观测不足以训练模型）",
            )
        return PlanResult(cands, prov, None)

    def checkpoint_state(self) -> dict[str, Any]:
        return {}

    def restore_state(self, state: dict[str, Any]) -> None:
        pass


# ================================================================ os 臂

#: 每轮溶液批次数——与 loop.build_experiment 硬编码的 ExecutionReq(n_solution_batches=2)
#: 对齐。DomainConfig 尚无该字段（已知配置面缺口，Backlog）：改批次数须两处同步。
_N_SOLUTION_BATCHES = 2

#: 风险分层粒度：LayoutPlanner._take 的优先键是 (risk, 区组均衡, 洗牌序)——连续风险值
#: 会令均衡项永无平手机会，贪心先抽干低风险区组、后续候选的跨区组强制无解（修 R1-2b
#: 使风险图真正非常数后，M6+M7 e2e 当场炸 LayoutError 实测）。故按 0.25 粗分层：弱证据
#: 不扰动布局（全 0 层，回落纯均衡），强证据才成层避让、层内均衡照旧。
_RISK_TIER = 0.25


def _plate_risk_map(cfg: DomainConfig, fm: FailureModel, round_id: int) -> dict[str, float]:
    """Whole-plate well_id -> optimistic artifact probability. Grid geometry matches
    LayoutPlanner; the per-well batch identity is the checkerboard parity of the execution
    face (``B{(row+col)%n}``, same parity as sim_base / attribution's ``R{round}-B{k}``).

    FM3 fix: the batch label handed to the failure model is round-invariant (``B{k}``,
    not ``R{round}-B{k}``). Coupled with the failure model's round-invariant bucket keys
    (``_batch_key``) and its round-marginal fallback, a well's learned batch history now
    reaches the risk map even when the current round's exposure has not yet landed — so a
    contaminated batch raises its checkerboard wells above the clean batch instead of being
    averaged into a batch-agnostic constant. (Previously the round-prefixed label never
    matched any stored bucket, forcing a batch-marginal fallback that flattened B0 and B1
    to the same value — the structural root of the "batch mid-band constant map".)

    Output is coarse-tiered to ``_RISK_TIER`` (order-preserving) so continuous risk values
    do not starve LayoutPlanner's block balancing (R1-2b).
    """
    from expos.design.layout import well_id_of

    rows, cols = cfg.plate.rows, cfg.plate.cols
    out: dict[str, float] = {}
    for r in range(rows):
        for c in range(cols):
            is_edge = r in (0, rows - 1) or c in (0, cols - 1)
            block = f"Q{(2 if r >= rows / 2 else 0) + (1 if c >= cols / 2 else 0)}"
            batch = f"B{(r + c) % _N_SOLUTION_BATCHES}"
            p = fm.p_artifact_optimistic(
                is_edge, block, solution_batch=batch, round_id=round_id
            )
            # 粗分层（见 _RISK_TIER 注释）：round 到最近层，保序、防均衡饿死
            out[well_id_of(r, c)] = round(p / _RISK_TIER) * _RISK_TIER
    return out


def _global_artifact_rate(fm: FailureModel) -> float:
    """读失败模型全局伪影率 p̄——契约键 ``summary()["p_global"]``，缺键响亮失败。

    教训（压测 R1-2a）：这里曾写 ``.get("global_rate", 0.0)``——键名失配被 ``.get``
    默认值静默吞掉，风险折扣因子恒 max(1−0, 0.5)=1，failure_aware 生成器与纯 UCB
    逐位相同。项目"无静默回退"红线：跨模块契约键一律显式取值、缺键当场炸。
    """
    summary = fm.summary()
    if "p_global" not in summary:
        raise PlannerError(
            f"FailureModel.summary() 缺契约键 'p_global'（现有键: {sorted(summary)}）"
            "——风险折扣拒绝默认值兜底"
        )
    return float(summary["p_global"])


class TrustAwarePlanner:
    """os 臂：阶段 FSM + 动作仲裁 + KB/风险折扣生成 + ε 配额 + risk_map。"""

    name = "trust_aware"

    def __init__(self, rules=None, *, enable_risk_map: bool = True,
                 enable_arbiter: bool = True):
        """os 全栈默认全开。两个消融开关（M13 消融臂，经策略对象参数化而非新类）：

        - ``enable_risk_map=False``（os-minus-riskmap 臂）：``plan_round`` 不产 risk_map
          （恒 None），布局无风险避让——回答"风险避让贡献多少"。风险图生产接线本身
          断开，与变异 E 同形，故 ``risk_map_applied`` 观测面 is_none 恒 True（机制活性
          反向断言：tests/test_mechanism_activity.py 消融用途）。
        - ``enable_arbiter=False``（os-minus-arbiter 臂）：动作仲裁空转——``_pending_actions``
          喂入恒空、零动作消费（``action_consumed`` 事件全程缺席），闭环检出/归因/提案照常
          但没有任何动作被物化进候选——回答"闭环动作贡献多少"。
        """
        self.rules = rules or DEFAULT_RULES
        validate_rules(self.rules)
        self.state = StageState(stage="sobol", entered_at_round=0)
        self._consecutive_high = 0
        self._consecutive_clear = 0
        self._enable_risk_map = enable_risk_map
        self._enable_arbiter = enable_arbiter

    # ------------------------------------------------------------ 状态持久化（§13.4：只存名字与数据）

    def checkpoint_state(self) -> dict[str, Any]:
        return {
            "stage": self.state.stage,
            "entered_at_round": self.state.entered_at_round,
            "consecutive_high": self._consecutive_high,
            "consecutive_clear": self._consecutive_clear,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        self.state = StageState(
            stage=state["stage"], entered_at_round=int(state["entered_at_round"])
        )
        self._consecutive_high = int(state.get("consecutive_high", 0))
        self._consecutive_clear = int(state.get("consecutive_clear", 0))

    # ------------------------------------------------------------ 内部

    def _stage_context(self, ctx: PlanContext) -> StageContext:
        store = ctx.store
        n_t = len(store.list_observations(trust=TrustLevel.TRUSTED))
        n_s = len(store.list_observations(trust=TrustLevel.SUSPECT))
        n_f = len(store.list_observations(trust=TrustLevel.FAILED))
        total = n_t + n_s + n_f
        # 上一轮 qc_report 的嫌疑占比 → streak 计数
        reports = store.read_events("qc_report")
        if reports:
            p = reports[-1]["payload"]
            rn = p["n_trusted"] + p["n_suspect"] + p["n_failed"]
            ratio = (p["n_suspect"] + p["n_failed"]) / rn if rn else 0.0
            if ratio >= HIGH_SUSPECT_RATIO:
                self._consecutive_high += 1
                self._consecutive_clear = 0
            else:
                self._consecutive_clear += 1
                self._consecutive_high = 0
        return StageContext(
            round_id=ctx.round_id,
            n_trusted=n_t, n_suspect=n_s, n_failed=n_f,
            trusted_ratio=(n_t / total) if total else 1.0,
            consecutive_high_suspect_rounds=self._consecutive_high,
        )

    def _adjudicate_proposals(self, store: RunStore) -> None:
        """未决 agent ACTION_PROPOSAL 的确定性裁定（M8 §10.1）。

        接受判据（与 arbiter._agent_items 的解析契约**共用** validate_proposal_content
        ——被接受的提案必须能无异常物化，否则会在装配期炸 ValueError/ArbiterError）：
        ① content 全解析面合法（action 合法 ActionType、params 是 dict、计数参数正整数、
        placement_hint 合法值、priority 有限——压测 J-2/J-4：毒丸 `params:"oops"` 曾穿过
        只查 action/target 的旧闸门，被 accept 后每轮重放裸抛 ValueError 打停闭环）；
        ② 需查参装配的动作（_NEEDS_CANDIDATE）必须带可解析且在案的 target 候选。
        其余一律 reject 并给中文理由留痕。非动作类提案（目标翻译/先验建议）不在规划器
        职权内，留给 human。"""
        known_cands = {
            c.cand_id for e in store.list_experiments() for c in e.candidates
        }
        for prop in unresolved_proposals(store):
            if prop.kind != DecisionKind.ACTION_PROPOSAL:
                continue
            content = prop.content or {}
            bad = validate_proposal_content(content)
            if bad is not None:
                validate_proposal(
                    store, prop, accepted=False, actor=Actor.PLANNER,
                    reason=f"提案 content 校验失败，无法物化：{bad}",
                )
                continue
            action = ActionType(content["action"])  # 已校验合法
            if action not in _NEEDS_CANDIDATE:
                # 对照增补/全新点的装配路径未实现（Backlog）——accept 会占仲裁孔预算
                # 却零物化（对抗审查 finding ④），诚实拒绝并留痕
                validate_proposal(
                    store, prop, accepted=False, actor=Actor.PLANNER,
                    reason=(
                        f"动作 {action.value} 的装配路径未实现（哨兵为每轮固定开销已覆盖"
                        "基本对照）；接受会占预算零物化，拒绝留痕待 Backlog"
                    ),
                )
                continue
            target = content.get("target")
            cand_id = content.get("cand_id") or (
                target if isinstance(target, str) and target.startswith("cand") else None
            )
            if cand_id is None or cand_id not in known_cands:
                validate_proposal(
                    store, prop, accepted=False, actor=Actor.PLANNER,
                    reason=(
                        f"动作 {action.value} 需复刻候选条件，但 target={target!r} "
                        "解析不出在案候选（哨兵/对照观测的复测不走提案通道）"
                    ),
                )
                continue
            validate_proposal(
                store, prop, accepted=True, actor=Actor.PLANNER,
                reason=f"动作 {action.value} 可解析且目标在案，交仲裁按预算竞位",
            )

    @staticmethod
    def _consumed_item_uids(store: RunStore) -> set[str]:
        """Item uids consumed by a *surviving* attempt (crash-aware).

        Crash-resume rolls back the materialized views of redone rounds via
        ``reconcile_redo_rounds`` but, by design, keeps the append-only event
        log intact (audit trail). A crashed attempt therefore leaves stale
        ``action_consumed`` events for the very rounds that are about to be
        redone. Reading the *full* event log would treat those stale records as
        "already consumed" and silently skip the remediation actions the redo is
        meant to re-issue -- I4 resume-equivalence breaks for the os family
        (observed: redo-round candidates {arbiter:endogenous, bo, sobol} collapse
        to {bo, sobol}, drifting best_trusted).

        Read-side filter (zero schema change): ignore an ``action_consumed`` event
        iff it predates the most recent ``redo_reconciliation`` marker (by seq)
        AND belongs to a round at or after that reconcile's ``from_round``.

        - The seq guard keeps consumption records produced by the redo itself
          (appended after the marker), so later rounds still see them consumed --
          this avoids re-consuming an action already redone in the same session.
        - The ``round_id >= from_round`` guard keeps consumption from rounds that
          were never rolled back (before ``from_round``).

        R5 MIR-1 fix (multi-reconcile supersession gap): the predicate folds over
        ALL markers, not just the most recent one. With last-marker-only logic, a
        record rolled back by an early marker and never re-consumed would survive a
        later marker with a higher ``from_round`` (rid < from_round of the last
        marker) and be wrongly retained as consumed. Fold semantics:
        superseded(e) iff any marker M has M.seq > e.seq and M.from_round <= e.round_id.
        Legitimately re-consumed records still survive: they are appended after
        every marker that covers their round. Orthogonal to the R4-A fix (marker
        generation); reviewer-sandbox verified 2500x60 steps with zero new
        counterexamples, and reverting R4-A still turns the property suite red.
        """
        reconciles = store.read_events("redo_reconciliation")
        if not reconciles:
            return {
                e["payload"]["item_uid"]
                for e in store.read_events("action_consumed")
            }
        markers = [(m["seq"], m["payload"]["from_round"]) for m in reconciles]

        def _superseded(e: dict) -> bool:
            rid = e["payload"].get("round_id")
            if rid is None:
                return False
            return any(m_seq > e["seq"] and m_from <= rid for m_seq, m_from in markers)

        return {
            e["payload"]["item_uid"]
            for e in store.read_events("action_consumed")
            if not _superseded(e)
        }

    def _pending_actions(self, ctx: PlanContext) -> list:
        store = ctx.store
        consumed = self._consumed_item_uids(store)
        flagged: list[ObservationObject] = [
            o for t in (TrustLevel.SUSPECT, TrustLevel.FAILED)
            for o in store.list_observations(trust=t)
            if o.next_action is not None and not o.is_control
            # 哨兵/对照观测的动作不进装配：哨兵每轮固定重置，其"复测"隐含在
            # 下一轮固定哨兵里（否则 target_cand_id=None 会在装配期响亮失败——
            # M6 联合端到端发现，M9 格子 agent 独立复现）
        ]
        # 掉落留痕去重集（每项只留痕一次：未消费项每轮重进队列，不去重会逐轮刷同事件）
        already = {
            e["payload"]["item_uid"] for e in store.read_events("action_skipped")
        }

        def _skip_bad_proposal(decision_id: str, reason: str) -> None:
            # 历史坏 accept 的 reject-after-the-fact 降级留痕（压测 J-2）：绝不裸抛打停闭环
            uid = f"agent:{decision_id}"
            if uid in already:
                return
            already.add(uid)
            store.append_event("action_skipped", {
                "item_uid": uid, "action": None, "source": "agent",
                "decision_id": decision_id,
                "reason": f"agent 提案坏解析，reject-after-the-fact 降级跳过：{reason}",
            })

        # 已消费的动作按其来源 obs 排除（item_uid 由 collect_actions 确定性派生）
        items = collect_actions(
            flagged, accepted_proposals(store), on_skip=_skip_bad_proposal
        )
        items = [it for it in items if it.item_uid not in consumed]
        # 只放行可物化动作（对抗审查 finding ④）：ADD_CONTROLS/NEW_CANDIDATES 的
        # 装配路径未实现，入选会占孔预算+发 action_consumed 却零产出——掉落须留痕
        out = []
        for it in items:
            if materializes_candidate(it):
                out.append(it)
            elif it.item_uid not in already:
                already.add(it.item_uid)
                store.append_event("action_skipped", {
                    "item_uid": it.item_uid, "action": it.action.value,
                    "source": it.source,
                    "reason": "装配路径未实现（Backlog）；哨兵为每轮固定开销已覆盖基本对照",
                })
        return out

    def _generate(self, ctx: PlanContext, generator: str, n: int, fm: FailureModel,
                  gen_seed: int) -> list[Candidate]:
        space, restrictions = ctx.cfg.design_space, ctx.cfg.restrictions
        if n <= 0:
            return []
        if generator == "sobol":
            return sobol_candidates(space, n, seed=gen_seed, restrictions=restrictions)
        ctx.model.kappa = ctx.kappa
        if generator == "response_gp+ucb":
            # Kriging Believer 批量（§11.1/§13.9）
            pool = _feasible_pool(space, max(256, 8 * n), gen_seed, restrictions)
            idx = ctx.model.select_batch_kb(pool, n)
            return [
                Candidate(params=from_unit(space, pool[i]), source="bo",
                          rationale="kriging-believer batch")
                for i in idx
            ]
        if generator == "response_gp+ucb+risk_discount":
            p_bar = _global_artifact_rate(fm)

            def score_fn(pool: np.ndarray) -> np.ndarray:
                return discounted_scores(ctx.model.score_pool(pool), float(p_bar))

            return propose_candidates(
                space, n, seed=gen_seed, score_fn=score_fn, restrictions=restrictions,
            )
        raise PlannerError(f"未知 generator: {generator!r}")

    # ------------------------------------------------------------ 主入口

    def plan_round(self, ctx: PlanContext) -> PlanResult:
        from expos.loop import derive_seed

        store = ctx.store
        gen_seed = derive_seed(ctx.seed, "gen", ctx.round_id)

        # 0) 提案裁定（M8 §10.1）：agent 只有提案权，裁定权在 planner/human——
        #    这里对未决 ACTION_PROPOSAL 逐条 accept/reject 落账（append-only 配对，
        #    公理 7 审计不变量）；GOAL_TRANSLATION/PRIOR_PROPOSAL 留给 human 裁定。
        self._adjudicate_proposals(store)

        # 1) 失败模型重建 + 风险图（event-sourced：观测当前裁决即真相源）
        fm = FailureModel().rebuild(store.list_observations())
        # os-minus-riskmap 消融臂：断开风险图生产接线（risk_map=None）→ 布局无风险避让。
        risk_map = _plate_risk_map(ctx.cfg, fm, ctx.round_id) if self._enable_risk_map else None

        # 2) 阶段 FSM
        sctx = self._stage_context(ctx)
        new_state, change = decide_stage(self.rules, self.state, sctx)
        if change is not None:
            store.append_event("stage_changed", change)
        self.state = new_state
        rule = self.rules[self.state.stage]

        # 3) 动作仲裁（预算 ≤30% 孔位）
        usable_wells = ctx.n_cands * ctx.cfg.replicates
        budget_wells = int(MAX_ACTION_FRAC * usable_wells)
        # os-minus-arbiter 消融臂：仲裁空转——喂入恒空 pending，零动作消费（action_consumed
        # 事件全程缺席）；检出/归因/提案照常，只是没有动作被物化进候选。
        pending = self._pending_actions(ctx) if self._enable_arbiter else []
        chosen, overflow = arbitrate(pending, budget_wells, ctx.cfg.replicates)
        # 候选容量二次封顶（缝隙审查）：arbitrate 只按孔预算过闸，而 well_cost 可被
        # 低估（如 agent 提案 n_wells=1、布局实占 replicates 孔）——物化候选数不得
        # 超过本轮候选容量 n_cands，超额按原 priority 序溢出（与孔预算溢出同路留痕）
        slots = ctx.n_cands
        kept = []
        for item in chosen:
            if materializes_candidate(item):
                if slots <= 0:
                    overflow.append(item)
                    continue
                slots -= 1
            kept.append(item)
        chosen = kept
        params_lut = {
            c.cand_id: c.params for e in store.list_experiments() for c in e.candidates
        }
        action_cands = actions_to_candidates(chosen, params_lut)
        for item in chosen:
            store.append_event("action_consumed", {
                "item_uid": item.item_uid, "round_id": ctx.round_id,
                "action": item.action.value, "semantics": item.semantics,
                "source": item.source,
            })

        # 4) 剩余名额：阶段生成器（模型未热身时强制 sobol）
        n_rest = max(0, ctx.n_cands - len(action_cands))
        generator = rule.generator if ctx.model.n_train >= MIN_TRAIN_FOR_BO else "sobol"
        gen_cands = self._generate(ctx, generator, n_rest, fm, gen_seed)

        # 5) ε 探索配额（RAHBO 缓解——折扣不得固化盲区）
        if generator != "sobol" and gen_cands:
            q = min(exploration_quota(len(gen_cands)), len(gen_cands))
            if q > 0:
                explore = sobol_candidates(
                    ctx.cfg.design_space, q,
                    seed=derive_seed(ctx.seed, "explore", ctx.round_id),
                    restrictions=ctx.cfg.restrictions,
                )
                for c in explore:
                    c.rationale = "ε-探索配额（防折扣盲区固化）"
                gen_cands = gen_cands[: len(gen_cands) - q] + explore

        prov = DesignProvenance(
            generator=generator,
            acquisition=(f"kb(kappa={ctx.kappa:.2f})" if generator == "response_gp+ucb"
                         else f"ucb+discount(kappa={ctx.kappa:.2f})"
                         if generator.endswith("risk_discount") else None),
            model_snapshot=(ctx.model.snapshot() if ctx.model.n_train else None),
            based_on_obs=ctx.model.n_train,
            actions_consumed=[it.item_uid for it in chosen],
            rationale=(
                f"stage={self.state.stage}; actions={len(action_cands)}"
                f"(overflow {len(overflow)}); explore_quota 生效"
            ),
        )
        return PlanResult(action_cands + gen_cands, prov, risk_map)
