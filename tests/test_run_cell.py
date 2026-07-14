"""M9 格子运行器与扫描脚手架验收（docs/M9_PROTOCOL.md §5）。

- naive/os 各跑 1 格（rounds=2 tmp）→ 产物齐（score.json / trajectory.jsonl）+ 返回 summary 带格子元数据；
- **幂等**：重跑不重算 campaign（checkpoint.json mtime 不变 + 返回 skipped=True）；
- robust 臂已接线（NaivePolicy×MedianAggregation×BaselinePlanner）可整格跑通；
- gen_sweep 在 tmp 产 scenarios/*.yaml + cells.tsv + sweep.sbatch，且全部 yaml load_domain 通过。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from expos.domain import load_domain
from expos.eval.run_cell import cell_id, run_cell
from expos.eval.scoring import EvalError

import gen_sweep  # scripts/gen_sweep.py

CRYSTAL = ROOT / "domains" / "crystal.yaml"
ROUNDS = 2


# ---------------------------------------------------------------- run_cell 两臂产物

@pytest.mark.parametrize("arm", ["naive", "os"])
def test_run_cell_products_complete(tmp_path, arm):
    out_root = tmp_path / "runs"
    summary = run_cell(CRYSTAL, arm=arm, scenario_id="S0.demo", seed=3,
                       rounds=ROUNDS, out_root=out_root)
    cid = cell_id("S0.demo", arm, 3)
    run_dir = out_root / cid
    assert (run_dir / "report" / "score.json").exists()
    assert (run_dir / "report" / "trajectory.jsonl").exists()
    # 返回 summary 带格子元数据 + 评分字段
    assert summary["cell_id"] == cid and summary["arm"] == arm
    assert summary["scenario_id"] == "S0.demo" and summary["seed"] == 3
    assert summary["skipped"] is False
    assert summary["n_rounds"] == ROUNDS and summary["arm"] == arm
    # trajectory 逐轮一行
    lines = (run_dir / "report" / "trajectory.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == ROUNDS
    row = json.loads(lines[0])
    assert row["arm"] == arm and row["scenario_id"] == "S0.demo"
    # R2 ③措辞修正：artifact 键改名 artifact_orphan（孤儿派生值，无执行路径消费），
    # 补 exec_round0（执行流真源）——"同伪影实现"强声称降级为共同随机数近似
    assert set(row["seeds"]) == {"np", "exec_round0", "artifact_orphan", "layout"}


# ---------------------------------------------------------------- 幂等：重跑不重算

def test_run_cell_idempotent_skip(tmp_path):
    out_root = tmp_path / "runs"
    first = run_cell(CRYSTAL, arm="naive", scenario_id="S0.demo", seed=5,
                     rounds=ROUNDS, out_root=out_root)
    assert first["skipped"] is False
    ckpt = out_root / cell_id("S0.demo", "naive", 5) / "checkpoint.json"
    mtime0 = ckpt.stat().st_mtime_ns

    second = run_cell(CRYSTAL, arm="naive", scenario_id="S0.demo", seed=5,
                      rounds=ROUNDS, out_root=out_root)
    # campaign 未重算：跳过标志 + checkpoint.json mtime 不变
    assert second["skipped"] is True
    assert ckpt.stat().st_mtime_ns == mtime0
    # 补评分幂等：final_regret 一致
    assert second["final_regret"] == first["final_regret"]


# ---------------------------------------------------------------- robust 臂（已接线）

def test_robust_arm_runs(tmp_path):
    """robust-blind 已接线（NaivePolicy×MedianAggregation×BaselinePlanner）：
    格子应完整跑通并产评分产物。"""
    result = run_cell(CRYSTAL, arm="robust", scenario_id="S0.demo", seed=1,
                      rounds=ROUNDS, out_root=tmp_path / "runs")
    assert result and (tmp_path / "runs").exists()
    run_dirs = list((tmp_path / "runs").glob("S0.demo__robust__s1"))
    assert run_dirs and (run_dirs[0] / "report" / "score.json").exists()


def test_unknown_arm_is_eval_error(tmp_path):
    with pytest.raises(EvalError):
        run_cell(CRYSTAL, arm="bogus", scenario_id="S0.demo", seed=1,
                 rounds=ROUNDS, out_root=tmp_path / "runs")


# ---------------------------------------------------------------- M13 消融臂映射 + 台账物料

def test_ablation_arms_map_to_modes():
    """五消融臂 + 三基线全走 _ARM_TO_MODE 恒等映射（run_cell 零 if arm== 分支的对齐点）。"""
    from expos.eval.run_cell import _mode_for_arm
    for arm in ("os-lite", "os-minus-riskmap", "os-minus-arbiter",
                "os-minus-attribution", "os-soft"):
        assert _mode_for_arm(arm) == arm  # 臂名即 mode
    assert _mode_for_arm("robust") == "robust"


@pytest.mark.skipif(
    not (ROOT / "runs" / "ablation" / "gen_ablation.py").exists(),
    reason="runs/ablation/gen_ablation.py 缺失（副本无 runs/ 产物）——CI 可移植跳过",
)
def test_gen_ablation_manifest(tmp_path):
    """消融台账物料（不跑）：R2 定标 P0=760 + P1=480 = 1240 格；场景 yaml 全 load_domain 通过。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_ablation", ROOT / "runs" / "ablation" / "gen_ablation.py")
    gen_ablation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_ablation)

    out = tmp_path / "abl"
    info = gen_ablation.write_ablation(CRYSTAL, out, eval_seeds=20)
    assert info["n_p0"] == 760 and info["n_p1"] == 480  # 2×19×20 + 3×8×20
    assert info["n_cells"] == 1240

    cells = (out / "cells.tsv").read_text(encoding="utf-8").splitlines()
    assert cells[0].split("\t") == [
        "scenario_id", "arm", "seed", "seed_set", "domain_yaml"]
    assert len(cells) - 1 == 1240
    # 全 B 评估种子（消融冻结报数，绝不回标）
    assert {ln.split("\t")[3] for ln in cells[1:]} == {"B"}
    arms = {ln.split("\t")[1] for ln in cells[1:]}
    assert arms == {"os-lite", "os-soft", "os-minus-riskmap",
                    "os-minus-arbiter", "os-minus-attribution"}
    # 场景 yaml 全部经 load_domain 校验
    for y in sorted((out / "scenarios").glob("*.yaml")):
        assert load_domain(y).name


# ---------------------------------------------------------------- gen_sweep 脚手架

def test_gen_sweep_products(tmp_path):
    out = tmp_path / "sweep"
    info = gen_sweep.write_sweep(
        CRYSTAL, out, arms=["naive", "os"], calib_seeds=2, eval_seeds=2)

    scen_dir = out / "scenarios"
    yamls = sorted(scen_dir.glob("*.yaml"))
    # S0 + S1 + (edge5 + batch4 + glare4 + thermal4) = 19 场景
    assert len(yamls) == 19 == info["n_scenarios"]
    for y in yamls:                       # 全部经 load_domain 校验通过
        cfg = load_domain(y)
        assert cfg.name

    # cells.tsv：表头 + n_cells 行，列齐
    cells = (out / "cells.tsv").read_text(encoding="utf-8").splitlines()
    assert cells[0].split("\t") == [
        "scenario_id", "arm", "seed", "seed_set", "domain_yaml"]
    assert len(cells) - 1 == info["n_cells"]
    seed_sets = {ln.split("\t")[3] for ln in cells[1:]}
    assert seed_sets == {"A", "B"}        # A/B 集分离存在

    # sweep.sbatch：数组模板 + run_cell CLI + Slurm 路径占位
    sb = (out / "sweep.sbatch").read_text(encoding="utf-8")
    assert "#SBATCH --array=" in sb
    assert "python3 -m expos.eval.run_cell" in sb
    assert "/opt/slurm/bin" in sb


def test_gen_sweep_scenario_yaml_content(tmp_path):
    """S1 零伪影确为空注入器；S2 edge 确写入对应 strength。"""
    out = tmp_path / "sweep"
    gen_sweep.write_sweep(CRYSTAL, out, arms=["naive"], calib_seeds=1, eval_seeds=1)
    s1 = load_domain(out / "scenarios" / "S1.zero.yaml")
    assert s1.simulator.get("artifact_scenario") == []
    s2 = load_domain(out / "scenarios" / "S2.edge_evaporation.0.35.yaml")
    scen = s2.simulator["artifact_scenario"]
    assert len(scen) == 1 and scen[0]["injector"] == "edge_evaporation"
    assert scen[0]["params"]["strength"] == 0.35
