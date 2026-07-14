"""崩溃/续跑组合态属性测试：RunStore + 消费侧谓词（MIR-1 有状态属性机）。

驱动**真实** RunStore（expos/kernel/store.py）与**真实**消费谓词
（TrustAwarePlanner._consumed_item_uids，expos/planner/policy.py），用一台独立
Python oracle（纯 list 增量回滚模型）作参照，让 hypothesis shrinker 自搜"崩溃相位 ×
事件日志 × 物化视图"组合态。

覆盖三条既有 P1 的方法学复现：
  · E2E3-F1 / R4 A-F1（stale action_consumed 被当"已消费"→ 静默跳过重做动作）；
  · 崩溃窗口 reconcile 标记落账（A-F1 修复）；
  · torn-tail 愈合的 seq 连续性。

不变量：
  1. seq 单调连续（read_events 不抛 + 外核 0..N-1）；
  2. **重建已消费集 == 独立 oracle 存活集**（核心）；
  3. checkpoint 单调一致（盘 == 模型）；
  4. reconcile 后物化视图无 round>=from_round 孤儿。

忠实性（对齐 loop.py）：
  · checkpoint 逐轮原子（completed_rounds=round_id+1，loop.py:597）→ 盘上 completed_rounds
    单调非降，崩溃至多丢一个在飞轮，from_round=completed_rounds；
  · **单写者崩溃=进程死亡；续跑=全新 RunStore 实例**（_tail_healed=False，首 append 前愈合
    torn tail）。crash_resume 每次重建 store 实例——复用旧实例会让 torn 残留与下一 append
    拼接成中段损坏，是脚手架伪缺陷（非生产可达），必须建模进程重启才忠实。

判别性校验（MIR-1 方法学结论"该缺陷本可被自动发现"）：
  设环境变量 MIR1_REVERT_AF1=1 → reconcile 回退成"零孤儿早退不落标记"（A-F1 修复前
  行为），本机必自动变红并 shrink 出最小 2 步序列 consume(u0); crash_resume()。

运行时预算（CI 可承受档）：默认 max_examples=200 × step_count=30，本机真实谓词路径
约 3–6s（谓词只读最近 marker，快）。加大到 2500×60 亦 <10s。判别性回退档 300×40 约 7s。
（对比：折叠全 marker 的候选修复跑满 2500×60 绿约 185s——正常谓词无此开销。）
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

if importlib.util.find_spec("hypothesis") is None:  # 未装 dev extra 时整模块优雅跳过
    pytest.skip("hypothesis 未安装（pip install -e '.[dev]'）", allow_module_level=True)

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from expos.kernel.store import RunStore
from expos.planner.policy import TrustAwarePlanner
from tests.test_kernel import make_experiment, make_observation

UID_POOL = ["u0", "u1", "u2"]
MAX_ROUND = 8
REVERT = os.environ.get("MIR1_REVERT_AF1") == "1"

# 沙盒目录：优先 pytest tmp，回退系统 tmp。
_SCRATCH = os.environ.get("MIR1_SCRATCH") or tempfile.gettempdir()


def _reverted_reconcile(store: RunStore, from_round: int):
    """A-F1 修复前 reconcile：零孤儿即早退、不落 redo_reconciliation 标记（原缺陷签名）。

    仅供 MIR1_REVERT_AF1=1 判别性校验挂载——证明本机能自动发现 A-F1。"""
    orphan_obs = [o for o in store.list_observations() if o.round_id >= from_round]
    orphan_exps = [e for e in store.list_experiments() if e.round_id >= from_round]
    if not orphan_obs and not orphan_exps:
        return None  # <-- 撤回的修复
    for o in orphan_obs:
        (store.root / "observations" / f"{o.obs_id}.json").unlink()
    for e in orphan_exps:
        (store.root / "experiments" / f"{e.exp_id}.json").unlink()
    store._obs_cache = None
    payload = {"from_round": from_round, "n_observations_removed": len(orphan_obs),
               "n_experiments_removed": len(orphan_exps),
               "exp_ids": sorted(e.exp_id for e in orphan_exps)}
    store.append_event("redo_reconciliation", payload)
    return payload


class StoreResumeMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self._dir = Path(tempfile.mkdtemp(prefix="mir1_", dir=_SCRATCH))
        self.store = RunStore(self._dir / "run", create=True)
        # oracle：独立地面真值（不抄谓词"仅看最近 reconcile"捷径）。每条消费记录
        # {"uid","round","alive"}；reconcile(from_round=R) 令所有 round>=R 的 alive 记录变
        # dead（该段被回滚、待重做）。存活已消费集 = {uid | 存在 alive 记录}。
        self.consumptions: list[dict] = []
        self.cur_round = 0          # 当前在飞轮（逐轮随 checkpoint 推进）
        self.completed_rounds = 0   # 盘上最近 checkpoint 覆盖轮数（单调非降）

    def teardown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _oracle_consumed(self) -> set[str]:
        return {c["uid"] for c in self.consumptions if c["alive"]}

    # -------------------------------------------------- rules

    @rule(uid=st.sampled_from(UID_POOL))
    def consume(self, uid):
        """planner 在当前在飞轮消费一个动作：落 action_consumed（policy.py:510 同形）。"""
        self.store.append_event(
            "action_consumed",
            {"item_uid": uid, "round_id": self.cur_round, "action": "REMEASURE",
             "semantics": "endogenous", "source": "arbiter"})
        self.consumptions.append({"uid": uid, "round": self.cur_round, "alive": True})

    @rule()
    def materialize(self):
        """落盘当前在飞轮实验+观测；与 consume 解耦 → "已消费未物化"窗口（A-F1）可达。"""
        exp = make_experiment(round_id=self.cur_round)
        self.store.save_experiment(exp)
        self.store.save_observation(make_observation(exp))

    @precondition(lambda self: self.cur_round < MAX_ROUND)
    @rule()
    def checkpoint(self):
        """收口当前轮：write_checkpoint(completed_rounds=cur_round+1) 并进下一轮。"""
        cr = self.cur_round + 1
        self.store.write_checkpoint({"completed_rounds": cr})
        self.completed_rounds = cr
        self.cur_round = cr

    @rule(torn=st.booleans())
    def crash_resume(self, torn):
        """崩溃在飞轮（consume/materialize 已落、checkpoint 未落）+ 续跑对账。

        torn=True 注入 torn-tail 半写残留。**续跑=全新 RunStore 实例**（进程重启忠实建模）。
        reconcile 清 round>=from_round 孤儿并（修复后）恒落标记；oracle 同步把 round>=from_round
        的 alive 消费回滚为 dead；cur_round 退回 from_round。"""
        from_round = self.completed_rounds
        if torn:
            with (self.store.root / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write('{"seq": 999999, "kind": "action_cons')  # 半写残留

        self.store = RunStore(self._dir / "run", create=False)  # 全新实例=新进程
        if REVERT:
            _reverted_reconcile(self.store, from_round)
        else:
            self.store.reconcile_redo_rounds(from_round)

        for c in self.consumptions:
            if c["alive"] and c["round"] >= from_round:
                c["alive"] = False
        self.cur_round = from_round

        for o in self.store.list_observations():
            assert o.round_id < from_round, f"orphan obs round={o.round_id} >= {from_round}"
        for e in self.store.list_experiments():
            assert e.round_id < from_round, f"orphan exp round={e.round_id} >= {from_round}"

    # -------------------------------------------------- invariants

    @invariant()
    def seq_monotone_contiguous(self):
        seqs = [e["seq"] for e in self.store.read_events()]
        assert seqs == list(range(len(seqs))), f"seq 非连续: {seqs[:16]}"

    @invariant()
    def consumed_equals_oracle(self):
        got = TrustAwarePlanner._consumed_item_uids(self.store)
        want = self._oracle_consumed()
        assert got == want, (
            f"consumed != oracle | 谓词多={sorted(got - want)} "
            f"oracle多={sorted(want - got)} | got={sorted(got)} want={sorted(want)}")

    @invariant()
    def checkpoint_matches_model(self):
        ck = self.store.read_checkpoint()
        disk = 0 if ck is None else ck.get("completed_rounds")
        assert disk == self.completed_rounds, f"盘 {disk} != 模型 {self.completed_rounds}"


StoreResumeMachine.TestCase.settings = settings(
    max_examples=int(os.environ.get("MIR1_MAX_EXAMPLES", "200")),
    stateful_step_count=int(os.environ.get("MIR1_STEPS", "30")),
    deadline=None,
)


# 【MIR-1 finding，待修】_consumed_item_uids 仅取 reconciles[-1]（最近 marker）；一条被
# **更早** marker 回滚、且此后从未被重新消费的 action_consumed，会在**更晚**且 from_round
# 更高的 marker 下被保留为"已消费"——即 stale 消费残留，同 A-F1 失败类（静默跳过），但走
# 多-reconcile 路径，A-F1 修复（恒落标记）不封此缺口。沙盒已验证：候选修复"折叠全部 marker"
# 使本机满档转绿且不引入新反例。谓词修好后删除本 xfail（strict 会在 xpass 时响亮提醒）。
# 探索性搜索引擎（非稳定门）：strict=False——命中缺陷则 xfail（绿），未命中则 xpass（亦绿），
# 两态皆稳定不 flaky。缺陷的**可靠**pin/告警由确定性 test_multi_reconcile_supersession_gap
# （strict=True）承担。谓词折叠全 marker 修复后，本机应恒绿；届时可把本标记降级为普通 class
# 收编为组合态回归（沙盒已验证候选修复下满档 2500×60 转绿、无新反例）。
# Post-fix promotion (2026-07-12): with the fold-all-markers predicate the machine
# should stay green at any budget; kept as a plain combinatorial regression.
class TestStoreResume(StoreResumeMachine.TestCase):
    pass


# ============================================================ 确定性锚点（不依赖随机）

def _fresh_run(tmp_path):
    return tmp_path / "run"


def test_af1_window_crash_marker_lands_and_supersedes(tmp_path):
    """A-F1 正向回归（修复生效，恒绿）：consume 后崩溃（零孤儿）→ resume 必落
    redo_reconciliation 标记，谓词据此**排除** stale 消费。撤回修复即变红（判别性）。"""
    run = _fresh_run(tmp_path)
    s = RunStore(run, create=True)
    s.append_event("action_consumed", {"item_uid": "x0", "round_id": 0})
    del s
    s = RunStore(run, create=False)  # resume = 新进程
    s.reconcile_redo_rounds(0)
    markers = s.read_events("redo_reconciliation")
    assert len(markers) == 1, "零孤儿窗口崩溃必须仍落 reconcile 标记（A-F1 修复）"
    assert markers[0]["payload"]["from_round"] == 0
    assert TrustAwarePlanner._consumed_item_uids(s) == set(), \
        "stale action_consumed（被 round0 reconcile 回滚）不得被当已消费"


def test_torn_tail_heal_keeps_seq_contiguous(tmp_path):
    """torn-tail 愈合后 seq 仍 0..N-1 连续、marker 完好（进程重启忠实路径）。"""
    run = _fresh_run(tmp_path)
    s = RunStore(run, create=True)
    s.append_event("action_consumed", {"item_uid": "x0", "round_id": 0})
    with (run / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"seq": 999999, "kind": "action_cons')  # 崩溃半写尾
    del s
    s = RunStore(run, create=False)  # 新进程：首 append 前愈合
    s.reconcile_redo_rounds(0)
    seqs = [e["seq"] for e in s.read_events()]
    assert seqs == [0, 1], f"愈合后 seq 应连续 [0,1]，实得 {seqs}"
    assert len(s.read_events("redo_reconciliation")) == 1


# Promoted from strict-xfail pin to a regular regression on 2026-07-12: the
# fold-all-markers predicate landed in _consumed_item_uids (see red_to_blue/043),
# so the pinned counterexample must now pass as ground truth.
def test_multi_reconcile_supersession_gap(tmp_path):
    """MIR-1 新反例最小确定性复现（4 步，忠实新实例续跑）：

      consume u0 @round0 → 崩溃#1 reconcile(from_round=0) 回滚 u0（marker#1）
      → redo round0 不重消费 u0 → checkpoint(cr=1)
      → 崩溃#2 reconcile(from_round=1)（marker#2）。

    地面真值：u0 在 round0 被消费、被 marker#1 回滚、从未重做 → 应为 not-consumed({})。
    谓词只认 marker#2（from_round=1），u0.round0<1 未被 supersede → 误判 {'u0'}。"""
    run = _fresh_run(tmp_path)
    s = RunStore(run, create=True)
    s.append_event("action_consumed", {"item_uid": "u0", "round_id": 0}); del s
    s = RunStore(run, create=False); s.reconcile_redo_rounds(0); del s          # 崩溃#1
    s = RunStore(run, create=False); s.write_checkpoint({"completed_rounds": 1}); del s
    s = RunStore(run, create=False); s.reconcile_redo_rounds(1)                  # 崩溃#2
    assert [m["payload"]["from_round"] for m in s.read_events("redo_reconciliation")] == [0, 1]
    # 期望地面真值 {}；当前谓词返回 {'u0'} → 本断言现在失败（xfail 固定该缺陷）。
    assert TrustAwarePlanner._consumed_item_uids(s) == set()
