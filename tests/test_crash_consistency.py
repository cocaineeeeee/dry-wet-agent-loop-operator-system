"""R1-5 崩溃一致性三硬伤的回归测试（docs/STRESS_TEST_R1.md R1-5）。

(a) torn-tail 治愈与 seq 恢复共用同一行有效性谓词（store._parse_line）：
    完整末行缺换行 → 补换行不截断、seq 连续；真半写 → 截断+告警；中间行坏 → 响亮抛。
(b) 崩溃窗口（观测已落盘、write_checkpoint 未落）→ resume 前 level-triggered 对账：
    清孤儿观测/实验 + 落 redo_reconciliation 事件；resume 后观测数/n_train 与一次跑完一致。
(c) snapshot 指纹纳入拟合出的核超参（theta，round 1e-10）+ alpha 模式标记：
    同数据同拟合态同指纹、拟合态变指纹变；fit 对输入行序严格不变（根因修复）。
"""

import json

import numpy as np
import pytest

from expos.kernel.objects import TrustLevel
from expos.kernel.store import RunStore, StoreError
from expos.loop import run_loop
from expos.models.response_gp import ResponseModel

from tests.test_loop_e2e import CRYSTAL
from tests.test_response_model import make_training


# ================================================================ (a) torn-tail × seq 恢复

def _seed_events(tmp_path, n=3):
    store = RunStore(tmp_path / "run")
    for i in range(n):
        store.append_event("checkpoint", {"round_id": i})
    return store, tmp_path / "run" / "events.jsonl"


def test_complete_tail_missing_newline_not_truncated(tmp_path, caplog):
    """崩溃恰在"记录字节已落、尾换行未落"：末行完整 JSON 缺换行 → 补换行不截断，
    追加与重读 seq 连续（此前 heal 截掉该行而 seq 恢复已计入 → 空洞 → 恒拒读）。"""
    _store, p = _seed_events(tmp_path, 3)
    data = p.read_bytes()
    assert data.endswith(b"\n")
    p.write_bytes(data[:-1])  # 去掉尾换行——记录本身完整

    store2 = RunStore(tmp_path / "run", create=False)
    assert store2._seq == 3  # 末行有效，计入恢复
    with caplog.at_level("WARNING"):
        ev = store2.append_event("checkpoint", {"round_id": 3})
    assert ev["seq"] == 3
    events = store2.read_events()
    assert [e["seq"] for e in events] == [0, 1, 2, 3]  # 无空洞：seq 2 的记录未被截掉
    assert events[2]["payload"]["round_id"] == 2
    # 不静默：补换行有告警且不是截断
    assert any("补换行" in r.getMessage() for r in caplog.records)


def test_true_torn_tail_truncated_with_warning(tmp_path, caplog):
    """真半写末行（JSON 不可解析）→ heal 截断 + 告警；seq 恢复不计入它，续号一致。"""
    _store, p = _seed_events(tmp_path, 3)
    with p.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "kind": "checkpoint", "payl')  # 半写尾

    store2 = RunStore(tmp_path / "run", create=False)
    with caplog.at_level("WARNING"):
        ev = store2.append_event("checkpoint", {"round_id": 3})
    assert ev["seq"] == 3  # 半写尾不占号
    assert [e["seq"] for e in store2.read_events()] == [0, 1, 2, 3]
    assert any("截断" in r.getMessage() for r in caplog.records)
    # 残留确被截掉：文件恰 4 行且每行都是完整 JSON
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    for line in lines:
        json.loads(line)


def test_middle_line_corruption_still_raises(tmp_path):
    """谓词统一后中间行损坏语义不变：非崩溃尾 → 响亮抛 StoreError。"""
    _store, p = _seed_events(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = '{"seq": 1, "kind": "checkpoint", broken'
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(StoreError):
        RunStore(tmp_path / "run", create=False).read_events()


def test_missing_newline_readable_without_write(tmp_path):
    """只读路径（status/UI）对缺换行末行同样按有效记录读，不丢不抛。"""
    _store, p = _seed_events(tmp_path, 3)
    p.write_bytes(p.read_bytes()[:-1])
    events = RunStore(tmp_path / "run", create=False).read_events()
    assert [e["seq"] for e in events] == [0, 1, 2]


# ================================================================ (b) 崩溃窗口重做轮对账

@pytest.fixture(scope="module")
def crash_window_runs(tmp_path_factory):
    """一次跑完参照 + 崩溃窗口复现（跑 2 轮后把 checkpoint 回退到 1 再 resume）。"""
    root = tmp_path_factory.mktemp("crash_b")
    ref, out = root / "ref", root / "crash"
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=11, out_dir=ref)
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=11, out_dir=out)
    ckpt = json.loads((out / "checkpoint.json").read_text())
    ckpt["completed_rounds"] = 1  # 模拟崩于"round1 观测已落盘、checkpoint 未落"
    (out / "checkpoint.json").write_text(json.dumps(ckpt))
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=11, out_dir=out, resume=True)
    return ref, out


def test_redo_round_observations_not_double_counted(crash_window_runs):
    ref, out = crash_window_runs
    s, sref = RunStore(out, create=False), RunStore(ref, create=False)
    assert len(s.list_observations(round_id=1)) == len(sref.list_observations(round_id=1))
    assert len(s.list_observations()) == len(sref.list_observations())
    assert (len(s.list_observations(trust=TrustLevel.TRUSTED))
            == len(sref.list_observations(trust=TrustLevel.TRUSTED)))
    # 重做轮只留一份实验文件
    assert len([e for e in s.list_experiments() if e.round_id == 1]) == 1


def test_redo_round_n_train_matches_oneshot(crash_window_runs):
    ref, out = crash_window_runs
    nt = json.loads((out / "models" / "snapshot_r1.json").read_text())["n_train"]
    ntref = json.loads((ref / "models" / "snapshot_r1.json").read_text())["n_train"]
    assert nt == ntref  # 双计会虚胖（复现时 141 vs 94）


def test_redo_reconciliation_event_logged_once(crash_window_runs):
    """处置响亮留痕：恰一条 redo_reconciliation，payload 齐全；事件日志 append-only
    ——重做前旧轮的事件痕迹仍在（审计特性），只清物化视图。"""
    _ref, out = crash_window_runs
    s = RunStore(out, create=False)
    recon = s.read_events("redo_reconciliation")
    assert len(recon) == 1
    pl = recon[0]["payload"]
    assert pl["from_round"] == 1
    assert pl["n_observations_removed"] > 0
    assert pl["n_experiments_removed"] == 1
    assert pl["exp_ids"]  # 被清实验留痕可追溯
    # 旧轮痕迹保留：round1 的 round_designed 事件出现两次（原跑 + 重做）
    designed_r1 = [e for e in s.read_events("round_designed")
                   if e["payload"]["round_id"] == 1]
    assert len(designed_r1) == 2
    # seq 全程连续可读
    seqs = [e["seq"] for e in s.read_events()]
    assert seqs == list(range(len(seqs)))


def test_clean_resume_emits_no_reconciliation(tmp_path):
    """Contract updated for R4-A [P1]: a clean resume now always lands exactly one
    redo_reconciliation marker with zero removals. The old contract (no marker when
    no orphan files) was the enabling assumption of the R4-A crash window: a crash
    between action_consumed and save_experiment leaves zero orphans, and without a
    marker the planner-side filter degrades to pre-fix behavior. The marker on a
    clean resume is a semantic no-op for the filter (no stale events >= from_round)
    but keeps the boundary well-defined in every resume path."""
    out = tmp_path / "clean"
    run_loop(CRYSTAL, mode="naive", rounds=1, seed=11, out_dir=out)
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=11, out_dir=out, resume=True)
    s = RunStore(out, create=False)
    markers = s.read_events("redo_reconciliation")
    assert len(markers) == 1
    assert markers[0]["payload"]["n_observations_removed"] == 0
    assert markers[0]["payload"]["n_experiments_removed"] == 0
    assert len(s.list_observations(round_id=0)) == len(s.list_observations(round_id=1))


# ================================================================ (c) snapshot 超参 + fit 行序不变

def test_snapshot_includes_fitted_hyperparams():
    """同数据不同拟合态 → 指纹必须不同（此前只哈希 (X,y)，对超参盲）。"""
    space, exp, obs = make_training(n=25, seed=3)
    m1 = ResponseModel(space, seed=0).fit(obs, [exp])
    m2 = ResponseModel(space, seed=0).fit(obs, [exp])
    assert m1.snapshot() == m2.snapshot()  # 确定性拟合 → 同指纹
    # 人为改拟合态（超 1e-10 容差）：数据没变，指纹必须变
    m2._gp.kernel_.theta = m2._gp.kernel_.theta + 1e-3
    assert m1.snapshot() != m2.snapshot()


def test_snapshot_theta_rounding_tolerance():
    """theta round 到 1e-10：容差内抖动不改指纹，容差外必改（docstring 权衡）。"""
    space, exp, obs = make_training(n=25, seed=3)
    m1 = ResponseModel(space, seed=0).fit(obs, [exp])
    m2 = ResponseModel(space, seed=0).fit(obs, [exp])
    safe = np.full_like(m1._gp.kernel_.theta, 0.25)  # 远离 round 边界的安全值
    m1._gp.kernel_.theta = safe
    m2._gp.kernel_.theta = safe + 1e-13
    assert m1.snapshot() == m2.snapshot()
    m2._gp.kernel_.theta = safe + 1e-6
    assert m1.snapshot() != m2.snapshot()


def test_snapshot_alpha_mode_marker():
    """alpha 对角模式与 WhiteKernel 模式核结构不同 → 指纹必须区分。"""
    space, exp, obs = make_training(n=25, seed=3)
    m_white = ResponseModel(space, seed=0).fit(obs, [exp])
    m_alpha = ResponseModel(space, seed=0).fit(
        obs, [exp], per_point_alpha=np.full(len(obs), 1e-4)
    )
    assert m_white.snapshot() != m_alpha.snapshot()


def test_fit_bitwise_invariant_to_observation_order():
    """R1-5(c) 根因：行序抖动经 L-BFGS 放大 → 同 seed 轨迹分叉。修后 fit 内部
    规范化 (X,y) 行序，打乱输入顺序 → theta 与指纹逐位一致。"""
    space, exp, obs = make_training(n=40, seed=3)
    m1 = ResponseModel(space, seed=0).fit(obs, [exp])
    perm = np.random.default_rng(1).permutation(len(obs))
    m2 = ResponseModel(space, seed=0).fit([obs[i] for i in perm], [exp])
    assert np.array_equal(m1._gp.kernel_.theta, m2._gp.kernel_.theta)
    assert m1.snapshot() == m2.snapshot()


def test_fit_order_invariant_with_per_point_alpha():
    """per-point alpha 模式：alpha 与行一起重排，打乱输入 → 拟合态逐位一致。"""
    space, exp, obs = make_training(n=30, seed=5)
    alpha = np.linspace(1e-5, 1e-3, len(obs))
    m1 = ResponseModel(space, seed=0).fit(obs, [exp], per_point_alpha=alpha)
    perm = np.random.default_rng(2).permutation(len(obs))
    m2 = ResponseModel(space, seed=0).fit(
        [obs[i] for i in perm], [exp], per_point_alpha=alpha[perm]
    )
    assert np.array_equal(m1._gp.kernel_.theta, m2._gp.kernel_.theta)
    assert np.array_equal(np.asarray(m1._gp.alpha), np.asarray(m2._gp.alpha))
    assert m1.snapshot() == m2.snapshot()


def test_store_listing_order_content_deterministic(tmp_path):
    """list_observations 按 (round_id, well_id) 内容序而非 uuid 文件名序——
    uuid 随机性不得泄入任何消费者的浮点求和顺序（QC 历史/聚合/重训）。"""
    from tests.test_kernel import make_experiment, make_observation

    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = [make_observation(exp) for _ in range(6)]
    wells = [f"A{i + 1}" for i in range(6)]
    for o, w in zip(obs, wells):
        o.layout_meta = o.layout_meta.model_copy(update={"well_id": w})
        store.save_observation(o)
    got = [o.layout_meta.well_id for o in store.list_observations()]
    assert got == sorted(wells)  # 与 uuid 抽签无关
