"""M9 三臂对比编排与主图验收（docs/M9_PROTOCOL.md §1/§6）。

控时：rounds=2、seeds=[1,2]。验收：
- 三臂产物齐（compare_report/{compare_summary.json, compare.png}）+ summary 字段全；
- compare.png 非空且确定性（同输入两次跑字节相同）；
- 幂等：重跑不重算 campaign（各格子 checkpoint.json mtime 不变）；
- 单臂失败（伪造坏 scenario yaml）响亮 EvalError，且消息带臂标识。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from expos.domain import load_domain
from expos.eval.compare import _aggregate, compare
from expos.eval.run_cell import cell_id
from expos.eval.scoring import EvalError

ZERO_ARTIFACT_YAML = ROOT / "runs" / "full_sweep" / "scenarios" / "S1.zero.yaml"

CRYSTAL = ROOT / "domains" / "crystal.yaml"
ARMS = ("naive", "robust", "os")
SEEDS = [1, 2]
ROUNDS = 2


# ---------------------------------------------------------------- 三臂产物齐 + 字段全

def test_compare_products_and_fields(tmp_path):
    out_root = tmp_path / "runs"
    agg = compare(CRYSTAL, scenario_id="S0.demo", seeds=SEEDS, rounds=ROUNDS,
                  out_root=out_root, arms=ARMS)

    report = out_root / "compare_report"
    assert (report / "compare_summary.json").exists()
    png = report / "compare.png"
    assert png.exists() and png.stat().st_size > 0     # 非空

    # summary 顶层字段
    assert agg["scenario_id"] == "S0.demo"
    assert agg["seeds"] == SEEDS and agg["rounds"] == ROUNDS
    assert tuple(agg["arms_order"]) == ARMS
    assert set(agg["arms"]) == set(ARMS)

    # 每臂字段全：末轮 regret、contaminated_ratio、wrong_optimum_hit 率、n_suspect 率
    for arm in ARMS:
        a = agg["arms"][arm]
        assert a["semantic"]
        for key in ("final_regret", "contaminated_ratio",
                    "training_contamination", "n_suspect_rate"):
            assert set(a[key]) >= {"mean", "std", "n", "values"}
        assert "wrong_optimum_hit_rate" in a
        # 末轮 regret 有数值（demo 场景有真值面）
        assert a["final_regret"]["mean"] is not None

    # n_suspect 率：os 才有（naive/robust 恒 0）
    assert agg["arms"]["naive"]["n_suspect_rate"]["mean"] == 0.0
    assert agg["arms"]["robust"]["n_suspect_rate"]["mean"] == 0.0

    # 落盘 json 与返回值一致
    on_disk = json.loads((report / "compare_summary.json").read_text(encoding="utf-8"))
    assert on_disk["arms"].keys() == agg["arms"].keys()


# ---------------------------------------------------------------- png 确定性

def test_compare_png_deterministic(tmp_path):
    a_root = tmp_path / "a"
    b_root = tmp_path / "b"
    compare(CRYSTAL, scenario_id="S0.demo", seeds=SEEDS, rounds=ROUNDS,
            out_root=a_root, arms=ARMS)
    compare(CRYSTAL, scenario_id="S0.demo", seeds=SEEDS, rounds=ROUNDS,
            out_root=b_root, arms=ARMS)
    pa = (a_root / "compare_report" / "compare.png").read_bytes()
    pb = (b_root / "compare_report" / "compare.png").read_bytes()
    assert pa == pb and len(pa) > 0


# ---------------------------------------------------------------- 幂等：重跑不重算

def test_compare_idempotent_no_recompute(tmp_path):
    out_root = tmp_path / "runs"
    compare(CRYSTAL, scenario_id="S0.demo", seeds=SEEDS, rounds=ROUNDS,
            out_root=out_root, arms=ARMS)
    # 记录每格子 checkpoint mtime
    ckpts = {}
    for arm in ARMS:
        for seed in SEEDS:
            ck = out_root / cell_id("S0.demo", arm, seed) / "checkpoint.json"
            assert ck.exists()
            ckpts[(arm, seed)] = ck.stat().st_mtime_ns

    agg2 = compare(CRYSTAL, scenario_id="S0.demo", seeds=SEEDS, rounds=ROUNDS,
                   out_root=out_root, arms=ARMS)
    for (arm, seed), mt in ckpts.items():
        ck = out_root / cell_id("S0.demo", arm, seed) / "checkpoint.json"
        assert ck.stat().st_mtime_ns == mt      # campaign 未重算
    # 结果稳定
    for arm in ARMS:
        assert agg2["arms"][arm]["final_regret"]["mean"] is not None


# ---------------------------------------------------------------- 单臂失败带臂标识

def test_compare_bad_scenario_raises_with_arm(tmp_path):
    bad = tmp_path / "bad_domain.yaml"
    bad.write_text("just a string, not a mapping\n", encoding="utf-8")
    with pytest.raises(EvalError) as ei:
        compare(bad, scenario_id="S9.bad", seeds=[1], rounds=ROUNDS,
                out_root=tmp_path / "runs", arms=("naive",))
    assert "arm=naive" in str(ei.value)     # 响亮且带臂标识


# ---------------------------------------------------------------- QC 税门：按场景配置判定（R1 死代码修复 ④）

def _fake_score(regret, contam=0.0, ns=0, nf=0, nt=10):
    """合成 score.json：绕过慢 campaign，直测 _aggregate 的 QC 税门逻辑。"""
    rounds = [
        {"round": i, "best_true_so_far": 1.0 + 0.1 * i,
         "n_suspect": ns, "n_failed": nf, "n_trusted": nt}
        for i in range(2)
    ]
    return {"final_regret": regret, "contaminated_in_training": contam,
            "training_contamination": contam, "wrong_optimum_hit_any": False,
            "rounds": rounds}


def test_qc_tax_gated_by_scenario_config_not_by_contamination():
    """R1 修复：QC 税块由 zero_artifact（场景配置）判定，与经验污染值解耦。
    旧死代码要求所有臂所有种子污染恒 0——纯噪声本底 0.3% 使其永不生成。"""
    seeds = [1, 2]
    arms = ("naive", "os")
    # 关键：污染非零（本底噪声），旧判据下永不出 qc_tax；新判据只看场景配置
    per_arm = {
        "naive": {s: _fake_score(0.10, contam=0.02) for s in seeds},
        "os": {s: _fake_score(0.12, contam=0.02, ns=1) for s in seeds},
    }
    # 零伪影场景 → 出 QC 税（即便污染非零）
    z = _aggregate(per_arm, arms, seeds, "S1.zero", 2, True)
    assert z["zero_artifact_scenario"] is True
    assert "qc_tax" in z
    assert z["qc_tax"]["os_suspect_failed_rate"]["mean"] > 0
    assert z["qc_tax"]["regret_delta_os_minus_naive"] == pytest.approx(0.02)
    # 有伪影场景 → 无 QC 税
    nz = _aggregate(per_arm, arms, seeds, "S2.edge", 2, False)
    assert nz["zero_artifact_scenario"] is False
    assert "qc_tax" not in nz


@pytest.mark.skipif(
    not ZERO_ARTIFACT_YAML.exists(),
    reason=f"零伪影场景 yaml 缺失（{ZERO_ARTIFACT_YAML}）——副本无 runs/ 产物，CI 可移植跳过",
)
def test_zero_artifact_flag_derived_from_domain_config():
    """compare() 的 zero_artifact = simulator.artifact_scenario 为空。"""
    assert load_domain(CRYSTAL).simulator.get("artifact_scenario")  # 非零伪影
    assert not load_domain(ZERO_ARTIFACT_YAML).simulator.get("artifact_scenario")  # S1 零伪影


# ---------------------------------------------------------------- 全量聚合器 R2 修订
# aggregate.py 是 runs/full_sweep 下的评测叶子脚本（import expos.eval.stats_tests），
# 用 importlib 直接加载做单元验收（L-1 量纲 / §4问3 cause 级 / P-11 弃权 / G-P3b 台账）。

import importlib.util as _ilu  # noqa: E402

_AGG_PATH = ROOT / "runs" / "full_sweep" / "_tools" / "aggregate.py"

# CI 可移植：aggregate.py 是 runs/full_sweep 下的评测叶子脚本，克隆/副本可能不含 runs/
# 产物。缺失时按理由跳过（而非 collection-time FileNotFoundError），保证纯代码副本全绿。
_AGG_SKIP = pytest.mark.skipif(
    not _AGG_PATH.exists(),
    reason=f"runs/full_sweep 聚合脚本缺失（{_AGG_PATH}）——副本无 runs/ 产物，CI 可移植跳过",
)


def _load_aggregate():
    spec = _ilu.spec_from_file_location("m9_aggregate", _AGG_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@_AGG_SKIP
def test_aggregate_duplicate_cells_audit(tmp_path):
    """G-P3b（R2）：跨台账重复格子响亮列出（load_cells 会静默去重，须有断言口径）。"""
    agg = _load_aggregate()
    a = tmp_path / "cells.tsv"
    b = tmp_path / "cells_g209.tsv"
    hdr = "scenario_id\tarm\tseed\n"
    a.write_text(hdr + "S2.x.0.1\tnaive\t1000\nS2.x.0.1\tos\t1000\n", encoding="utf-8")
    # b 混入 1 条与 a 重复的 naive 行 + 1 条独有行
    b.write_text(hdr + "S2.x.0.1\tnaive\t1000\nS2.x.0.1\trcgp\t1000\n", encoding="utf-8")
    dups = agg.duplicate_cells((a, b))
    assert dups == ["S2.x.0.1__naive__s1000"]
    # 无重复时空列表
    assert agg.duplicate_cells((a,)) == []


@_AGG_SKIP
def test_aggregate_realized_effect_over_noise(tmp_path):
    """L-1（R2）：实现绝对效应/噪声尺度从 truth 逐孔算（all-affected，含 round0）。
    effect = |measured − true − noise|；只统计 artifacts 含注入器名的被影响孔。"""
    import json

    agg = _load_aggregate()
    run = tmp_path / "run"
    (run / "truth").mkdir(parents=True)
    # 两孔被注入（effect=0.04, 0.02），一孔无注入（应被排除）
    rows = [
        {"well_id": "A1", "true_value": 0.5, "noise": 0.01,
         "measured_value": 0.5 + 0.01 + 0.04, "artifacts": ["edge_evaporation"]},
        {"well_id": "A2", "true_value": 0.5, "noise": -0.005,
         "measured_value": 0.5 - 0.005 + 0.02, "artifacts": ["edge_evaporation"]},
        {"well_id": "A3", "true_value": 0.5, "noise": 0.0,
         "measured_value": 0.5, "artifacts": []},
    ]
    (run / "truth" / "round_0.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    mean, median, n = agg.realized_effect_over_noise(run, "edge_evaporation", noise_sd=0.02)
    assert n == 2
    # (0.04/0.02 + 0.02/0.02)/2 = (2.0 + 1.0)/2 = 1.5
    assert mean == pytest.approx(1.5)
    assert median == pytest.approx(1.5)
    # 无 truth 目录 → (None, None, 0)
    assert agg.realized_effect_over_noise(tmp_path / "nope", "edge_evaporation") == (None, None, 0)


@_AGG_SKIP
def test_aggregate_cause_level_metrics():
    """§4问3 / P-11 / G-P3（R2）：cause 级配对（≥1 注入孔命中即计对）+ 弃权率 +
    种子级 bootstrap CI。"""
    agg = _load_aggregate()
    per_seed = [
        {"inj_correct": 2, "inj_wrong": 0, "inj_incon": 1},  # 命中 + 弃权 1/3
        {"inj_correct": 0, "inj_wrong": 0, "inj_incon": 3},  # 全弃权 → 未识出
        {"inj_correct": 1, "inj_wrong": 1, "inj_incon": 0},  # 命中，无弃权
        {"inj_correct": 0, "inj_wrong": 2, "inj_incon": 0},  # 错判，无命中 → 未识出
    ]
    m = agg.cause_level_metrics(per_seed)
    assert m["n_seeds"] == 4
    assert m["cause_hit_rate"] == pytest.approx(0.5)   # 2/4 种子识出主导 cause
    # 弃权率（每种子 incon/总）：1/3, 3/3, 0, 0 → mean = (0.3333+1+0+0)/4
    assert m["abstention_rate_injected"] == pytest.approx((1/3 + 1.0) / 4)
    assert 0.0 <= m["cause_hit_ci_low"] <= m["cause_hit_rate"] <= m["cause_hit_ci_high"] <= 1.0
    # 空输入退化
    empty = agg.cause_level_metrics([])
    assert empty["cause_hit_rate"] is None and empty["n_seeds"] == 0
