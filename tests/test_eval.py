"""M9 事后评分与轨迹层验收（docs/M9_PROTOCOL.md §3/§4）。

用**真实 run** 做 fixture：crystal os 2 轮 seed=3 + naive 2 轮同 seed 到 tmp（同 seed →
sobol 首轮候选/布局/噪声一致，伪影场景配对可比）。断言：score_run 字段齐全、regret 非负
且不增、os 污染利用率 ≤ naive；truth 缺失响亮失败；trajectory 行数=轮数、schema 齐、幂等。
**红线**：scoring/trajectory 之外无人 import expos.eval（评分是叶子）。
"""

import re
from pathlib import Path

import pytest

from expos.eval.scoring import EvalError, load_truth, score_run
from expos.eval.stats_tests import (
    StatsError,
    compare_arms_paired,
    paired_permutation_test,
    percentile_bootstrap_ci,
)
from expos.eval.trajectory import write_trajectory
from expos.kernel.objects import (
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    Routing,
    TrustLevel,
)
from expos.kernel.store import RunStore
from expos.loop import run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"
SEED = 3
ROUNDS = 2


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    base = tmp_path_factory.mktemp("m9")
    os_dir = base / "os"
    naive_dir = base / "naive"
    run_loop(CRYSTAL, mode="os", rounds=ROUNDS, seed=SEED, out_dir=os_dir)
    run_loop(CRYSTAL, mode="naive", rounds=ROUNDS, seed=SEED, out_dir=naive_dir)
    return os_dir, naive_dir


# ---------------------------------------------------------------- load_truth

def test_load_truth_shape(runs):
    os_dir, _ = runs
    truth = load_truth(os_dir)
    assert set(truth) == set(range(ROUNDS))  # round -> well_id -> record
    for wells in truth.values():
        assert wells
        rec = next(iter(wells.values()))
        assert {"well_id", "true_value", "measured_value", "artifacts"} <= set(rec)


def test_load_truth_missing_dir_fails_loud(tmp_path):
    """truth 缺失 → EvalError（评分绝不在缺真值时静默降级）。"""
    empty = tmp_path / "no_truth"
    empty.mkdir()
    with pytest.raises(EvalError):
        load_truth(empty)
    (empty / "truth").mkdir()  # 目录在但无 round 文件也须炸
    with pytest.raises(EvalError):
        load_truth(empty)


# ---------------------------------------------------------------- score_run

_ROUND_FIELDS = {
    "round",
    "best_true_so_far",
    "simple_regret",
    "best_trusted",
    "contaminated_in_training",
    "training_contamination",
    "training_injected",
    "wrong_optimum_hit",
    "n_trusted",
    "n_suspect",
    "n_failed",
}


def test_score_run_fields_complete(runs):
    os_dir, _ = runs
    s = score_run(os_dir, CRYSTAL)
    assert s["n_rounds"] == ROUNDS
    assert s["arm"] == "os"
    assert (os_dir / "report" / "score.json").exists()
    assert len(s["rounds"]) == ROUNDS
    for d in s["rounds"]:
        assert _ROUND_FIELDS <= set(d)
        assert 0.0 <= d["contaminated_in_training"] <= 1.0
        bt = d["best_trusted"]
        assert bt is not None and bt["cand_id"] and bt["well_id"]
        assert bt["true"] is not None


@pytest.mark.parametrize("arm", ["os", "naive"])
def test_regret_nonneg_and_nonincreasing(runs, arm):
    os_dir, naive_dir = runs
    s = score_run(os_dir if arm == "os" else naive_dir, CRYSTAL)
    regrets = [d["simple_regret"] for d in s["rounds"]]
    assert all(r is not None and r >= 0.0 for r in regrets)
    assert all(b <= a + 1e-9 for a, b in zip(regrets, regrets[1:]))  # 不增


def test_os_contamination_le_naive(runs):
    """同 seed 伪影场景：os 隔离高偏差伪影孔 → 训练集真污染比例 ≤ naive。"""
    os_dir, naive_dir = runs
    so = score_run(os_dir, CRYSTAL)
    sn = score_run(naive_dir, CRYSTAL)
    assert so["contaminated_in_training"] <= sn["contaminated_in_training"] + 1e-9


# ------------------------------------------------- 污染分母双口径（R1-3(b)）

def _synthetic_run(tmp_path: Path, mode: str) -> Path:
    """手工构造最小 run：2 条干净 TRUSTED + 1 条被污染 QUARANTINE（bias=0.4 ≫ 3σ=0.06）。"""
    run_dir = tmp_path / f"run_{mode}"
    store = RunStore(run_dir)
    store.save_config({"mode": mode})

    def _obs(oid, cand, well, col, value, trust, routing, conf):
        o = ObservationObject(
            obs_id=oid, exp_id="exp_x", round_id=0, cand_id=cand,
            result=MeasuredResult(metric="quality", value=value),
            layout_meta=LayoutMeta(well_id=well, row=0, col=col),
        )
        o.trust, o.routing, o.trust_confidence = trust, routing, conf
        store.save_observation(o)

    _obs("t1", "c1", "A1", 0, 0.50, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL, 1.0)
    _obs("t2", "c2", "A2", 1, 0.40, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL, 1.0)
    _obs("q1", "c3", "A3", 2, 0.90, TrustLevel.SUSPECT, Routing.QUARANTINE, 0.45)
    store.save_truth(0, [
        {"well_id": "A1", "true_value": 0.50, "measured_value": 0.50, "artifacts": []},
        {"well_id": "A2", "true_value": 0.40, "measured_value": 0.40, "artifacts": []},
        {"well_id": "A3", "true_value": 0.50, "measured_value": 0.90,
         "artifacts": ["glare"]},
    ])
    return run_dir


def test_soft_arm_training_contamination_exceeds_legacy(tmp_path):
    """os-soft 软并入被污染 QUARANTINE：新口径（实际入模集合）> 旧口径（raw TRUSTED）。"""
    s = score_run(_synthetic_run(tmp_path, "os-soft"), CRYSTAL, n_opt_scan=64)
    assert s["contaminated_in_training"] == pytest.approx(0.0)   # 旧口径看不见软并入
    assert s["training_contamination"] == pytest.approx(1.0 / 3.0)
    assert s["training_contamination"] > s["contaminated_in_training"]
    assert s["training_injected"] == pytest.approx(1.0 / 3.0)


def test_os_arm_training_contamination_matches_legacy(tmp_path):
    """os 硬隔离：QUARANTINE 不入模 → 新旧口径同集合、同值。"""
    s = score_run(_synthetic_run(tmp_path, "os"), CRYSTAL, n_opt_scan=64)
    assert s["training_contamination"] == pytest.approx(s["contaminated_in_training"])
    assert s["training_contamination"] == pytest.approx(0.0)


# ------------------------------------------------- G-2：训练集成员清单可复算（R2）

def test_training_members_sidecar_reproducible(tmp_path):
    """G-2（R2）：score_run 落 report/training_members.json，逐轮记入模成员 +
    逐孔 bias/contaminated/injected 标志——第三方据此可独立重算污染率。
    os-soft 软并入被污染 QUARANTINE → 成员里含该孔且标 contaminated=True。"""
    import json

    run_dir = _synthetic_run(tmp_path, "os-soft")
    s = score_run(run_dir, CRYSTAL, n_opt_scan=64)
    tm_path = run_dir / "report" / "training_members.json"
    assert tm_path.exists()
    tm = json.loads(tm_path.read_text(encoding="utf-8"))
    assert tm["arm"] == "os-soft"
    last = tm["rounds"][-1]
    # os-soft 实际入模集合 = 2 干净 TRUSTED + 1 被污染 QUARANTINE = 3
    assert last["n_effective"] == 3
    members = {m["well_id"]: m for m in last["members"]}
    assert set(members) == {"A1", "A2", "A3"}
    assert members["A3"]["contaminated"] is True and members["A3"]["injected"] is True
    assert members["A1"]["contaminated"] is False
    # 独立重算污染率 = 与 score.json 的 training_contamination 逐位一致
    recomputed = sum(1 for m in last["members"] if m["contaminated"]) / last["n_effective"]
    assert recomputed == pytest.approx(s["training_contamination"])


# ------------------------------------------------- 统计检验（R1-3(d) 已知答案）

def test_permutation_all_zero_diffs_p_is_one():
    res = paired_permutation_test([0.0] * 12)
    assert res["p_value"] == 1.0
    assert res["observed_mean_diff"] == 0.0


def test_permutation_clear_difference_significant():
    """10 对全同向差 → 双侧符号翻转 p ≈ 2/2¹⁰ ≈ 0.002 < 0.05。"""
    diffs = [0.50, 0.60, 0.55, 0.62, 0.58, 0.53, 0.61, 0.57, 0.59, 0.54]
    res = paired_permutation_test(diffs, seed=1)
    assert res["p_value"] < 0.05
    assert res["observed_mean_diff"] == pytest.approx(sum(diffs) / len(diffs))
    # 确定性：同 seed 同输入同 p
    assert paired_permutation_test(diffs, seed=1)["p_value"] == res["p_value"]


def test_permutation_symmetric_noise_not_significant():
    diffs = [0.3, -0.31, 0.12, -0.11, 0.05, -0.06, 0.2, -0.19]
    assert paired_permutation_test(diffs, seed=2)["p_value"] > 0.4


def test_bootstrap_ci_known_answers():
    res = percentile_bootstrap_ci([1.0] * 8)
    assert (res["mean"], res["ci_low"], res["ci_high"]) == (1.0, 1.0, 1.0)
    vals = [0.9, 1.1, 1.0, 0.95, 1.05, 1.02, 0.98, 1.04, 0.96, 1.0]
    res2 = percentile_bootstrap_ci(vals, seed=3)
    assert res2["ci_low"] <= res2["mean"] <= res2["ci_high"]
    assert res2["ci_low"] > 0.8  # 明显为正的样本 → CI 不含 0
    with pytest.raises(StatsError):
        percentile_bootstrap_ci([None, None])


def test_compare_arms_paired_pairing_and_low_n():
    a = {s: 1.0 for s in range(25)}
    b = {s: 1.2 for s in range(25)}
    res = compare_arms_paired(a, b)
    assert res["n_pairs"] == 25 and res["low_n"] is False
    assert res["mean_diff"] == pytest.approx(-0.2)
    assert res["ci95_low"] == pytest.approx(-0.2) and res["ci95_high"] == pytest.approx(-0.2)
    # 只配对共同非 None 种子；n<20 → low_n（协议 §3 N≥20 下限）
    res_small = compare_arms_paired({1: 0.5, 2: 0.6, 3: None}, {2: 0.1, 3: 0.2, 4: 0.3})
    assert res_small["n_pairs"] == 1 and res_small["seeds"] == [2]
    assert res_small["low_n"] is True
    with pytest.raises(StatsError):
        compare_arms_paired({1: 0.5}, {2: 0.5})


# ---------------------------------------------------------------- trajectory

_TRAJ_FIELDS = {
    "round",
    "arm",
    "scenario_id",
    "seeds",
    "best_trusted",
    "best_true_so_far",
    "regret",
    "n_by_trust",
    "generator",
    "kappa",
    "contaminated_ratio",
}


def test_trajectory_rows_schema_and_idempotent(runs):
    import json

    os_dir, _ = runs
    seeds = {"np": SEED, "artifact": 111, "layout": 222}
    p = write_trajectory(os_dir, CRYSTAL, arm="os", scenario_id="S0.smoke.3", seeds=seeds)
    assert p == os_dir / "report" / "trajectory.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == ROUNDS  # 逐轮一行
    for i, line in enumerate(lines):
        row = json.loads(line)
        assert _TRAJ_FIELDS <= set(row)
        assert row["round"] == i and row["arm"] == "os"
        assert row["scenario_id"] == "S0.smoke.3" and row["seeds"] == seeds
        assert set(row["n_by_trust"]) == {"trusted", "suspect", "failed"}

    # 幂等覆盖写：再写一次字节完全一致
    first = p.read_bytes()
    write_trajectory(os_dir, CRYSTAL, arm="os", scenario_id="S0.smoke.3", seeds=seeds)
    assert p.read_bytes() == first


# ---------------------------------------------------------------- 红线：评分是叶子

def test_eval_is_a_leaf_no_incoming_imports():
    """scoring/trajectory 之外，无任何 expos 模块 import expos.eval（源扫描）。"""
    eval_dir = ROOT / "expos" / "eval"
    offenders = []
    for py in (ROOT / "expos").rglob("*.py"):
        if eval_dir in py.parents or py.parent == eval_dir:
            continue  # 允许 eval 包内部相互 import
        src = py.read_text(encoding="utf-8")
        if "expos.eval" in src or re.search(r"from\s+expos\s+import\s+[^\n]*\beval\b", src):
            offenders.append(str(py))
    assert not offenders, f"评分不是叶子——以下模块 import 了 expos.eval: {offenders}"
