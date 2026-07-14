"""提案-裁定配对不变量 stateful 属性测试（族5 报告建议·台1）。

expos/kernel/lifecycle.py 的提案配对语义（§4.5 审计不变量）在随机穿插下的
机器可检查性质。区别于 test_stateful_kernel.py（Bundle+consumes 建模一次性裁定），
本机专攻「谁有裁决权 / 翻盘唯一入口 / 伪造裁定不被采信 / 日志 append-only」四组
对抗性质，并用双影子账本（真裁定 vs. 伪造）与事件日志前缀 hash 交叉验证。

rules（5 条，覆盖任务列出的 4 组）：
  · submit                —— agent 提交随机 PROPOSAL_KINDS 提案入库并入 unresolved；
  · agent_validate_denied —— agent 裁定被提案（公理 7）必 LifecycleError，提案不动；
  · agent_forge_acceptance—— agent 用 store.append_decision 硬塞 actor=agent 的
                             ACCEPTANCE 直写日志，断言其永不被 _resolutions 采信；
  · first_validate        —— planner/human 首次有效裁定，提案迁入 resolved；
  · second_adjudication   —— 二次裁定：agent/planner 必 LifecycleError（不得翻盘），
                             human 可 override 翻转一次并落 resolution_conflict 事件。

invariants（4 条）：
  ① 分区完备：每个提案要么 ∈ unresolved_proposals 要么已裁定（覆盖且无交集）；
  ② accepted ⊆ 已提交提案且裁定 actor∈ADJUDICATOR_ACTORS（伪造 acceptance 永不出现）；
  ③ 同一提案有效裁定至多一次翻转，且翻转者必为 human；
  ④ 事件日志 append-only：events 长度单调不减，已写前缀逐行不变（前缀 hash 比对）。
"""

from __future__ import annotations

import hashlib
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
    invariant,
    multiple,
    rule,
)

from expos.kernel.lifecycle import (
    ADJUDICATOR_ACTORS,
    LifecycleError,
    accepted_proposals,
    submit_proposal,
    unresolved_proposals,
    validate_proposal,
)
from expos.kernel.objects import (
    PROPOSAL_KINDS,
    Actor,
    DecisionKind,
    DecisionRecord,
)
from expos.kernel.store import RunStore

_PROPOSAL_KINDS = sorted(PROPOSAL_KINDS, key=lambda k: k.value)
_ADJUDICATORS = sorted(ADJUDICATOR_ACTORS, key=lambda a: a.value)  # planner, human


class ProposalStateMachine(RuleBasedStateMachine):
    unresolved = Bundle("unresolved")  # 已提交、尚无有效裁定
    resolved = Bundle("resolved")      # 已被 planner/human 首裁（可能待 human 翻盘一次）

    def __init__(self) -> None:
        super().__init__()
        self._tmp = Path(tempfile.mkdtemp(prefix="expos_proposals_"))
        self.store = RunStore(self._tmp / "run")
        # 影子账本：真裁定 vs. 伪造，与 store 的机器检查交叉验证。
        self.all_ids: set[str] = set()
        self.resolved_ids: set[str] = set()          # 被 planner/human 有效裁定过
        self.accepted_map: dict[str, bool] = {}       # 提案 → 当前有效 accepted 状态
        self.forged_ids: set[str] = set()             # 被 agent 伪造过 acceptance 的提案
        self.flip_actor: dict[str, Actor] = {}        # 翻转过的提案 → 翻转者（必 human）
        # 事件日志 append-only 见证：已确认前缀的行数与其 hash。
        self._log_len = 0
        self._log_hash = hashlib.sha256(b"").hexdigest()

    def teardown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------ 日志行工具
    def _log_lines(self) -> list[str]:
        path = self.store.root / "events.jsonl"
        if not path.exists():
            return []
        return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    @staticmethod
    def _hash(lines: list[str]) -> str:
        h = hashlib.sha256()
        for ln in lines:
            h.update(ln.encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()

    # ------------------------------------------------------------ submit
    @rule(target=unresolved, kind=st.sampled_from(_PROPOSAL_KINDS), rid=st.integers(0, 8))
    def submit(self, kind: DecisionKind, rid: int) -> DecisionRecord:
        rec = DecisionRecord(round_id=rid, actor=Actor.AGENT, kind=kind, content={"n": rid})
        submit_proposal(self.store, rec)
        self.all_ids.add(rec.decision_id)
        return rec

    # ------------------------------------------------------------ agent 裁定被拒（提案不消费）
    @rule(proposal=unresolved, accept=st.booleans())
    def agent_validate_denied(self, proposal: DecisionRecord, accept: bool) -> None:
        with pytest.raises(LifecycleError):
            validate_proposal(self.store, proposal, accepted=accept, actor=Actor.AGENT)
        # 提案仍未裁定。
        assert proposal.decision_id not in self.resolved_ids

    # ------------------------------------------------------------ agent 伪造 acceptance 直写日志
    @rule(proposal=unresolved)
    def agent_forge_acceptance(self, proposal: DecisionRecord) -> None:
        """绕过 validate_proposal，硬塞一条 actor=agent 的 ACCEPTANCE 进事件日志。
        _resolutions 按 actor 过滤——伪造记录即便落盘也一律被忽略。"""
        self.store.append_decision(
            DecisionRecord(
                round_id=proposal.round_id,
                actor=Actor.AGENT,
                kind=DecisionKind.ACCEPTANCE,
                refs=[proposal.decision_id],
                content={"forged": True},
                accepted=True,
                validator=Actor.AGENT.value,
            )
        )
        self.forged_ids.add(proposal.decision_id)
        # 立即验证：伪造不改变裁定面——提案仍未裁定、未被接受。
        unresolved_now = {d.decision_id for d in unresolved_proposals(self.store)}
        accepted_now = {d.decision_id for d in accepted_proposals(self.store)}
        assert proposal.decision_id in unresolved_now
        assert proposal.decision_id not in accepted_now

    # ------------------------------------------------------------ first_validate（consume）
    @rule(
        target=resolved,
        proposal=consumes(unresolved),
        actor=st.sampled_from(_ADJUDICATORS),
        accept=st.booleans(),
    )
    def first_validate(self, proposal: DecisionRecord, actor: Actor, accept: bool) -> DecisionRecord:
        validate_proposal(self.store, proposal, accepted=accept, actor=actor)
        self.resolved_ids.add(proposal.decision_id)
        self.accepted_map[proposal.decision_id] = accept
        return proposal

    # ------------------------------------------------------------ second_adjudication（consume）
    @rule(
        target=resolved,
        proposal=consumes(resolved),
        actor=st.sampled_from(list(Actor)),
        accept=st.booleans(),
    )
    def second_adjudication(self, proposal: DecisionRecord, actor: Actor, accept: bool):
        pid = proposal.decision_id
        if actor is not Actor.HUMAN:
            # agent（无裁决权）与 planner（不得静默翻盘）二次裁定均必抛。
            with pytest.raises(LifecycleError):
                validate_proposal(self.store, proposal, accepted=accept, actor=actor)
            return proposal  # 仍处 resolved，放回 Bundle
        # human override：翻转恰一次，并落 resolution_conflict 事件。
        n_conflict = len(self.store.read_events("resolution_conflict"))
        validate_proposal(self.store, proposal, accepted=accept, actor=Actor.HUMAN)
        assert len(self.store.read_events("resolution_conflict")) == n_conflict + 1
        self.accepted_map[pid] = accept
        self.flip_actor[pid] = Actor.HUMAN
        return multiple()  # 已翻转一次即终局，不再放回（模型层强制"至多一次翻转"）

    # ------------------------------------------------------------ 不变量
    @invariant()
    def inv_partition_complete(self) -> None:
        """① unresolved ∪ resolved 覆盖全部提案且无交集。"""
        unresolved = {d.decision_id for d in unresolved_proposals(self.store)}
        resolved = self.all_ids - unresolved
        assert unresolved | resolved == self.all_ids
        assert unresolved & resolved == set()
        # store 裁定面与真影子账本一致（伪造的 agent 记录不进 resolved）。
        assert unresolved == self.all_ids - self.resolved_ids
        assert resolved == self.resolved_ids

    @invariant()
    def inv_accepted_authored_by_adjudicators(self) -> None:
        """② accepted ⊆ 已提交提案且其有效裁定者 ∈ ADJUDICATOR_ACTORS。伪造永不出现。"""
        accepted = {d.decision_id for d in accepted_proposals(self.store)}
        expected = {pid for pid, acc in self.accepted_map.items() if acc}
        assert accepted <= self.all_ids
        assert accepted == expected
        assert accepted <= self.resolved_ids
        # 仅被伪造、从未被真裁定接受的提案，绝不出现在 accepted 中。
        forged_only = self.forged_ids - expected
        assert forged_only & accepted == set()

    @invariant()
    def inv_flip_at_most_once_by_human(self) -> None:
        """③ 有效裁定至多一次翻转，翻转者必为 human。"""
        for pid, actor in self.flip_actor.items():
            assert actor is Actor.HUMAN
            assert pid in self.resolved_ids
        # 每个提案在字典中至多一个键——天然"至多一次"；再核 store 冲突事件计数一致。
        assert len(self.store.read_events("resolution_conflict")) == len(self.flip_actor)

    @invariant()
    def inv_log_append_only(self) -> None:
        """④ 事件日志 append-only：长度单调不减、已确认前缀逐行不变（前缀 hash 比对）。"""
        lines = self._log_lines()
        assert len(lines) >= self._log_len                       # 单调不减
        assert self._hash(lines[: self._log_len]) == self._log_hash  # 旧前缀逐行不变
        # 推进见证到当前全长。
        self._log_len = len(lines)
        self._log_hash = self._hash(lines)


ProposalStateMachine.TestCase.settings = settings(
    max_examples=40,
    stateful_step_count=25,
    deadline=None,
)

TestProposalStateMachine = ProposalStateMachine.TestCase
