"""内核 stateful 属性测试（REFERENCE_MAP §18.1 族8：RuleBasedStateMachine）。

用 Bundle+consumes 精确建模「提案 → 一次性裁定」的一次性语义，随机穿插：
  · submit    —— agent 提交 PROPOSAL_KINDS 提案入库并入 Bundle；
  · resolve   —— 随机 actor 裁定被消费的提案：agent 必拒；planner 首裁后同提案
                再 planner 裁定触发翻盘守卫（LifecycleError）、human 可 override
                并留 resolution_conflict 事件；
  · advance   —— 随机实验做随机目标状态迁移：合法成功、非法必 LifecycleError 且状态不变；
  · reclassify_obs —— 对预置的已路由观测随机改判：agent 必拒；planner/human 成功后
                reclassification 事件与 OVERRIDE 决策成对新增。

三条 @invariant 在每步后机器检查：
  ① 配对完备性：unresolved ∪ resolved 覆盖全部提案且无交集；accepted ⊆ resolved；
  ② 改判留痕：reclassification 事件数 == OVERRIDE 决策数；
  ③ seq 单调无重复，且 events.jsonl 行数 == seq 计数 == 事件条数。
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest

if importlib.util.find_spec("hypothesis") is None:  # dev extra 未装时整模块优雅跳过（照 test_ui_smoke find_spec 范本）
    pytest.skip("hypothesis 未安装（pip install -e '.[dev]'）", allow_module_level=True)

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    initialize,
    invariant,
    multiple,
    rule,
)

from expos.kernel.lifecycle import (
    VALID_TRANSITIONS,
    LifecycleError,
    accepted_proposals,
    advance_status,
    reclassify,
    route_observation,
    submit_proposal,
    unresolved_proposals,
    validate_proposal,
)
from expos.kernel.objects import (
    PROPOSAL_KINDS,
    Actor,
    Budget,
    Candidate,
    DecisionKind,
    DecisionRecord,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    ExpStatus,
    LayoutMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    QCCheck,
    QCReport,
    Routing,
    TrustLevel,
    VariableDef,
)
from expos.kernel.store import RunStore

N_EXP = 3   # 预置实验数（advance 的随机目标）
N_OBS = 3   # 预置已路由观测数（reclassify_obs 的随机目标）

_PROPOSAL_KINDS = sorted(PROPOSAL_KINDS, key=lambda k: k.value)


# ---------------------------------------------------------------- 最小对象构造器

def _make_experiment(round_id: int) -> ExperimentObject:
    space = DesignSpace(name="s", variables=[VariableDef(name="x", low=0.0, high=1.0)])
    cand = Candidate(params={"x": 0.5}, source="sobol")
    return ExperimentObject(
        round_id=round_id,
        domain="d",
        objective=Objective(name="o", metric="m"),
        design_space=space,
        candidates=[cand],
        budget=Budget(wells_total=96, rounds_total=4),
        execution_req=ExecutionReq(adapter="sim"),
        provenance=DesignProvenance(generator="g"),
    )


def _make_observation(exp: ExperimentObject, suspicion: float) -> ObservationObject:
    checks = [QCCheck(name="value_range", level="hard", passed=True, score=0.0)]
    if suspicion > 0:
        checks.append(QCCheck(name="edge", level="structural", passed=False, score=suspicion))
    return ObservationObject(
        exp_id=exp.exp_id,
        round_id=exp.round_id,
        cand_id=exp.candidates[0].cand_id,
        result=MeasuredResult(metric="m", value=0.5),
        layout_meta=LayoutMeta(well_id="A1", row=0, col=0),
        qc=QCReport(checks=checks),
    )


# ---------------------------------------------------------------- 状态机

class KernelStateMachine(RuleBasedStateMachine):
    proposals = Bundle("proposals")

    def __init__(self) -> None:
        super().__init__()
        # 每个 machine 实例独立 tmp 目录（teardown 清理）。
        self._tmp = Path(tempfile.mkdtemp(prefix="expos_stateful_"))
        self.store = RunStore(self._tmp / "run")
        # 影子账本：与 store 的机器检查交叉验证。
        self.all_proposal_ids: set[str] = set()
        self.resolved_ids: set[str] = set()
        self.accepted_ids: set[str] = set()

    def teardown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------ 预置固定资源
    @initialize()
    def _seed(self) -> None:
        # 预置若干 DESIGNED 实验供 advance 随机迁移。
        self.experiments = [_make_experiment(i) for i in range(N_EXP)]
        for exp in self.experiments:
            self.store.save_experiment(exp)
        # 预置若干已路由观测供 reclassify_obs 随机改判。
        self.observations = []
        for i in range(N_OBS):
            obs = route_observation(self.store, _make_observation(self.experiments[0], 0.45 + 0.1 * i))
            self.observations.append(obs)

    # ------------------------------------------------------------ submit
    @rule(target=proposals, kind=st.sampled_from(_PROPOSAL_KINDS), rid=st.integers(0, 8))
    def submit(self, kind: DecisionKind, rid: int) -> DecisionRecord:
        rec = DecisionRecord(round_id=rid, actor=Actor.AGENT, kind=kind, content={"n": rid})
        submit_proposal(self.store, rec)
        self.all_proposal_ids.add(rec.decision_id)
        return rec

    # ------------------------------------------------------------ resolve（consumes）
    @rule(
        target=proposals,
        proposal=consumes(proposals),
        actor=st.sampled_from(list(Actor)),
        accept=st.booleans(),
    )
    def resolve(self, proposal: DecisionRecord, actor: Actor, accept: bool):
        if actor is Actor.AGENT:
            # 公理 7：agent 无裁决权 —— 必拒，提案仍未裁定，放回 Bundle。
            with pytest.raises(LifecycleError):
                validate_proposal(self.store, proposal, accepted=accept, actor=Actor.AGENT)
            assert proposal.decision_id not in self.resolved_ids
            return proposal

        # planner/human 首次有效裁定。
        validate_proposal(self.store, proposal, accepted=accept, actor=actor)
        self.resolved_ids.add(proposal.decision_id)
        (self.accepted_ids.add if accept else self.accepted_ids.discard)(proposal.decision_id)

        # 翻盘守卫：已裁定提案再被 planner 裁定必拒（不得静默翻盘）。
        with pytest.raises(LifecycleError):
            validate_proposal(self.store, proposal, accepted=not accept, actor=Actor.PLANNER)

        # human 可 override 翻盘，并留 resolution_conflict 事件。
        n_conflict = len(self.store.read_events("resolution_conflict"))
        validate_proposal(self.store, proposal, accepted=not accept, actor=Actor.HUMAN)
        assert len(self.store.read_events("resolution_conflict")) == n_conflict + 1
        (self.accepted_ids.add if not accept else self.accepted_ids.discard)(proposal.decision_id)

        return multiple()  # 已终局裁定，不放回 Bundle

    # ------------------------------------------------------------ advance
    @rule(idx=st.integers(0, N_EXP - 1), target_status=st.sampled_from(list(ExpStatus)))
    def advance(self, idx: int, target_status: ExpStatus) -> None:
        exp = self.experiments[idx]
        old = exp.status
        if target_status in VALID_TRANSITIONS[old]:
            advance_status(self.store, exp, target_status)
            assert exp.status is target_status
            assert self.store.load_experiment(exp.exp_id).status is target_status
        else:
            with pytest.raises(LifecycleError):
                advance_status(self.store, exp, target_status)
            assert exp.status is old  # 非法迁移不改状态
            assert self.store.load_experiment(exp.exp_id).status is old

    # ------------------------------------------------------------ reclassify_obs
    @rule(
        idx=st.integers(0, N_OBS - 1),
        actor=st.sampled_from(list(Actor)),
        new_trust=st.sampled_from(list(TrustLevel)),
        new_routing=st.sampled_from(list(Routing)),
    )
    def reclassify_obs(self, idx: int, actor: Actor, new_trust: TrustLevel, new_routing: Routing) -> None:
        """随机改判：合法性预期用 check_trust_transition 自身作预言机（守卫函数的
        正确性由 test_kernel 单测覆盖转移表逐格；状态机只负责验证——无论随机操作
        合法与否，不变量①②③恒保持、非法操作后观测零改动）。R1 P2 转移表落地后，
        随机 (trust,routing,actor) 多数组合应被响亮拒绝，这正是要覆盖的面。"""
        from expos.kernel.lifecycle import check_trust_transition

        obs_id = self.observations[idx].obs_id
        before = self.store.load_observation(obs_id)
        try:
            check_trust_transition(before.trust, new_trust, new_routing, actor, "x")
            legal = True
        except LifecycleError:
            legal = False
        if actor is Actor.AGENT or not legal:
            with pytest.raises(LifecycleError):
                reclassify(self.store, obs_id, new_trust, new_routing, actor=actor, reason="x")
            after = self.store.load_observation(obs_id)
            assert (after.trust, after.routing) == (before.trust, before.routing)  # 未改动
        else:
            n_rc = len(self.store.read_events("reclassification"))
            n_ov = len(self.store.list_decisions(kind=DecisionKind.OVERRIDE))
            reclassify(self.store, obs_id, new_trust, new_routing, actor=actor, reason="x")
            assert len(self.store.read_events("reclassification")) == n_rc + 1
            assert len(self.store.list_decisions(kind=DecisionKind.OVERRIDE)) == n_ov + 1

    # ------------------------------------------------------------ 不变量
    @invariant()
    def inv_pairing_complete(self) -> None:
        """① unresolved ∪ resolved 覆盖全部提案且无交集；accepted ⊆ resolved。"""
        unresolved = {d.decision_id for d in unresolved_proposals(self.store)}
        accepted = {d.decision_id for d in accepted_proposals(self.store)}
        resolved = self.all_proposal_ids - unresolved
        # 覆盖 + 无交集
        assert unresolved | resolved == self.all_proposal_ids
        assert unresolved & resolved == set()
        # store 账本与影子账本一致
        assert unresolved == self.all_proposal_ids - self.resolved_ids
        assert accepted == self.accepted_ids
        # accepted ⊆ resolved
        assert accepted <= resolved

    @invariant()
    def inv_override_paired(self) -> None:
        """② 每次改判都成对：reclassification 事件数 == OVERRIDE 决策数。"""
        n_rc = len(self.store.read_events("reclassification"))
        n_ov = len(self.store.list_decisions(kind=DecisionKind.OVERRIDE))
        assert n_rc == n_ov

    @invariant()
    def inv_seq_monotone(self) -> None:
        """③ seq 单调无重复 + events.jsonl 行数 == seq 计数 == 事件条数。"""
        events = self.store.read_events()
        seqs = [e["seq"] for e in events]
        assert seqs == list(range(len(seqs)))       # 单调递增、无缺、无重复
        assert len(seqs) == len(set(seqs))
        path = self.store.root / "events.jsonl"
        n_lines = 0
        if path.exists():
            n_lines = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        assert n_lines == len(events) == self.store._seq


KernelStateMachine.TestCase.settings = settings(
    max_examples=30,
    stateful_step_count=20,
    deadline=None,
)

TestKernelStateMachine = KernelStateMachine.TestCase
