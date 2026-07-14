"""M3 adapter 层验收测试（BUILD_PLAN M3）：
协议形状 / 真值内部最优 / 六注入器方向 / 真值-测量分离 / truth 只出自模拟器 /
ingest 产 PENDING / 图像指标稳定 / worklist / 域热插拔 / 依赖隔离 / 响亮失败。"""

import csv as csv_mod
from pathlib import Path

import re
import numpy as np
import pytest

from expos.adapters.artifacts import (
    BatchShift,
    DustNucleation,
    EdgeEvaporation,
    Glare,
    InstrumentDrift,
    ThermalGradient,
    WellContext,
    build_injector,
    injectors_for_round,
)
from expos.adapters.base import AdapterError, ExecutionResult, RawResult
from expos.adapters.bench_manual import BenchManualAdapter
from expos.adapters.ingest import raw_to_observations
from expos.adapters.ingest.csv_loader import load_results_csv
from expos.adapters.ingest.image_metrics import crystal_metrics
from expos.adapters.sim_coating import CoatingSim
from expos.adapters.sim_crystal import CrystalSim
from expos.design.layout import LayoutPlanner
from expos.design.sampler import sobol_candidates
from expos.domain import DomainConfig, DomainError, build_adapter, load_domain
from expos.kernel.objects import (
    Budget,
    Control,
    DesignProvenance,
    ExecutionReq,
    ExperimentObject,
    TrustLevel,
)

ROOT = Path(__file__).resolve().parent.parent


def ctx(row=0, col=0, rows=6, cols=8, batch="R0-B0", capture=0):
    return WellContext(
        well_id=f"{chr(65 + row)}{col + 1}", row=row, col=col, rows=rows, cols=cols,
        is_edge=row in (0, rows - 1) or col in (0, cols - 1),
        block_id="Q0", solution_batch=batch, capture_index=capture, round_id=0,
    )


def make_experiment(domain="crystal", round_id=0, n_cands=6, seed=7) -> ExperimentObject:
    cfg = load_domain(ROOT / "domains" / f"{domain}.yaml")
    cands = sobol_candidates(cfg.design_space, n_cands, seed=seed, restrictions=cfg.restrictions)
    controls = [Control(kind="sentinel", params=cfg.sentinel.params,
                        expected_band=cfg.sentinel.expected_band)
                for _ in range(cfg.sentinel.n)]
    layout = LayoutPlanner(cfg.plate.rows, cfg.plate.cols, seed=seed).assign(
        cands, controls, n_replicates=cfg.replicates
    )
    return ExperimentObject(
        round_id=round_id, domain=cfg.name, objective=cfg.objective,
        design_space=cfg.design_space, active_vars=[v.name for v in cfg.design_space.variables],
        restrictions=cfg.restrictions, candidates=cands, controls=controls,
        layout=layout, budget=Budget(**cfg.budget.model_dump()),
        execution_req=ExecutionReq(adapter=cfg.adapter, n_solution_batches=2),
        provenance=DesignProvenance(generator="sobol"),
    )


# ---------------------------------------------------------------- 协议形状与非变异

def test_adapter_protocol_shape_and_no_mutation():
    exp = make_experiment()
    snapshot = exp.model_dump()
    sim = CrystalSim({"noise_sd": 0.0})
    result = sim.execute(exp, np.random.default_rng(0))
    assert isinstance(result, ExecutionResult)
    assert len(result.raw_results) == len(exp.layout.wells)
    assert result.truth_records is not None and len(result.truth_records) == len(result.raw_results)
    assert exp.model_dump() == snapshot  # adapter 不得修改 ExperimentObject
    r0 = result.raw_results[0]
    assert isinstance(r0, RawResult) and r0.metric == "quality_index"


def test_raw_results_do_not_expose_truth():
    # RawResult schema 无任何真值字段；真值只在 truth_records
    field_names = set(RawResult.model_fields)
    assert not any("true" in f or "truth" in f for f in field_names)
    exp = make_experiment()
    result = CrystalSim({"noise_sd": 0.02}).execute(exp, np.random.default_rng(1))
    for t in result.truth_records:
        assert "true_value" in t and "artifacts" in t


# ---------------------------------------------------------------- 真值面形状

def test_crystal_true_surface_has_interior_optimum():
    cfg = load_domain(ROOT / "domains" / "crystal.yaml")
    sim = CrystalSim()
    best_p, best_v = sim.true_optimum(cfg.design_space, n=2048, seed=0)
    assert best_v > 0.2
    s = cfg.design_space.var("supersaturation")
    a = cfg.design_space.var("additive_frac")
    r = cfg.design_space.var("cool_rate")
    # 内部最优：不贴任何连续维边界（5% 余量；additive 在 log 空间判断）
    assert s.low + 0.05 * (s.high - s.low) < best_p["supersaturation"] < s.high - 0.05 * (s.high - s.low)
    la, lo_a, hi_a = np.log(best_p["additive_frac"]), np.log(a.low), np.log(a.high)
    assert lo_a + 0.05 * (hi_a - lo_a) < la < hi_a - 0.05 * (hi_a - lo_a)
    assert r.low + 0.05 * (r.high - r.low) < best_p["cool_rate"] < r.high - 0.05 * (r.high - r.low)
    # 籽晶更优（已核实的定性结论）
    assert best_p["seeded"] == 1
    # 角点显著劣于最优
    corner = {"supersaturation": 1.6, "additive_frac": 1e-2, "cool_rate": 2.0, "seeded": 0}
    assert sim.true_value(corner) < best_v * 0.5


def test_sentinel_band_calibrated_against_sim():
    cfg = load_domain(ROOT / "domains" / "crystal.yaml")
    tv = CrystalSim().true_value(cfg.sentinel.params)
    lo, hi = cfg.sentinel.expected_band
    assert lo <= tv <= hi, f"哨兵真值 {tv:.3f} 不在配置带 [{lo},{hi}] 内——配置需重标定"


# ---------------------------------------------------------------- 六注入器方向与分离

def test_edge_evaporation_direction():
    inj = EdgeEvaporation(strength=0.5, decay_wells=1.0)
    rng = np.random.default_rng(0)
    v_edge, hit_e, tag = inj.apply(0.5, ctx(row=0, col=0), rng)
    v_center, hit_c, _ = inj.apply(0.5, ctx(row=3, col=4), rng)
    assert tag == "edge_evaporation" and hit_e and v_edge > 0.5
    assert not hit_c and v_center == 0.5  # 中心衰减后不命中


def test_thermal_gradient_direction():
    inj = ThermalGradient(axis="row", magnitude=0.2)
    rng = np.random.default_rng(0)
    v0, _, _ = inj.apply(0.5, ctx(row=0), rng)
    v5, _, _ = inj.apply(0.5, ctx(row=5), rng)
    assert v0 < 0.5 < v5  # 沿行单调
    with pytest.raises(AdapterError):
        ThermalGradient(axis="diag").apply(0.5, ctx(), rng)


def test_glare_and_dust_directions():
    rng = np.random.default_rng(0)
    v, hit, _ = Glare(prob=1.0, boost=0.4).apply(0.5, ctx(), rng)
    assert hit and v == pytest.approx(0.7)
    v, hit, _ = DustNucleation(prob=1.0, drop=0.4).apply(0.5, ctx(), rng)
    assert hit and v == pytest.approx(0.3)
    v, hit, _ = Glare(prob=0.0).apply(0.5, ctx(), rng)
    assert not hit and v == 0.5


def test_batch_shift_direction():
    inj = BatchShift(batch_suffix="B1", shift=-0.2)
    rng = np.random.default_rng(0)
    v1, hit1, _ = inj.apply(0.5, ctx(batch="R0-B1"), rng)
    v0, hit0, _ = inj.apply(0.5, ctx(batch="R0-B0"), rng)
    assert hit1 and v1 == pytest.approx(0.4)
    assert not hit0 and v0 == 0.5


def test_instrument_drift_modes():
    rng = np.random.default_rng(0)
    lin = InstrumentDrift(mode="linear", rate=-0.005)
    v0, _, _ = lin.apply(0.5, ctx(capture=0), rng)
    v40, _, _ = lin.apply(0.5, ctx(capture=40), rng)
    assert v40 < v0 == 0.5
    # AR(1)：同种子确定性；乱序调用响亮失败
    a1 = InstrumentDrift(mode="ar1", phi=0.95, sigma=0.01)
    a2 = InstrumentDrift(mode="ar1", phi=0.95, sigma=0.01)
    seq1 = [a1.apply(0.5, ctx(capture=i), np.random.default_rng(7) if i == 0 else rng)[0] for i in range(3)]
    with pytest.raises(AdapterError):
        a1.apply(0.5, ctx(capture=0), rng)  # capture_index 回退
    with pytest.raises(AdapterError):
        InstrumentDrift(mode="wave").apply(0.5, ctx(), rng)
    assert len(seq1) == 3


def test_instrument_drift_resident_components_and_baseline_determinism():
    """resident 四分量 + 轮级基线的确定性（resume 等价的单元级证明）。"""
    # 老化趋势：baseline 随 round_id 线性推进（纯函数，无随机）
    aging = InstrumentDrift(mode="resident", rate_per_round=-0.02)
    assert aging.resident_baseline(0) == pytest.approx(0.0)
    assert aging.resident_baseline(5) == pytest.approx(-0.10)
    # 会话间随机游走：确定性于 (rw_seed, round_id)，且是真游走（非常数、非纯线性）
    rw1 = InstrumentDrift(mode="resident", sigma_between=0.02, rw_seed=7)
    rw2 = InstrumentDrift(mode="resident", sigma_between=0.02, rw_seed=7)
    seq1 = [rw1.resident_baseline(r) for r in range(6)]
    seq2 = [rw2.resident_baseline(r) for r in range(6)]
    assert seq1 == seq2  # 同种子恒同 → resume 后同 round_id 恒得同基线
    assert len(set(round(x, 9) for x in seq1)) > 1  # 游走非常数
    # 不同 rw_seed → 不同轨迹
    assert [InstrumentDrift(mode="resident", sigma_between=0.02, rw_seed=99).resident_baseline(r)
            for r in range(6)] != seq1
    # 温周期：round_id 的纯正弦函数
    per = InstrumentDrift(mode="resident", period_rounds=4.0, period_amp=0.03)
    assert per.resident_baseline(0) == pytest.approx(0.0)
    assert per.resident_baseline(1) == pytest.approx(0.03)  # sin(π/2)
    # baseline 只含轮级三分量（不含轮内 AR(1)）；round_id<0 响亮失败
    with pytest.raises(AdapterError):
        aging.resident_baseline(-1)


def test_instrument_drift_resident_applied_threshold_and_legacy_unchanged():
    """resident 用 applied_eps 判据（消全亮标签）；ar1/linear 仍用 1e-9（旧行为零变化）。"""
    rng = np.random.default_rng(0)
    # resident：微小漂移（baseline≈0、轮内 AR(1) 未越 applied_eps）→ 不打标
    tiny = InstrumentDrift(mode="resident", rate_per_round=0.0, sigma=1e-4, applied_eps=0.005)
    _, applied, _ = tiny.apply(0.5, ctx(capture=0), rng)
    assert applied is False
    # resident：显著老化（aging=rate_per_round·round_id=-0.20 越 applied_eps）→ 打标
    big = InstrumentDrift(mode="resident", rate_per_round=-0.05)
    _, applied_big, _ = big.apply(0.5, _ctx_round(rid=4, capture=0), np.random.default_rng(1))
    assert applied_big is True
    # 旧 ar1/linear：applied 阈仍 1e-9（微漂也打标，行为不变）
    lin = InstrumentDrift(mode="linear", rate=1e-6)
    _, ap_lin, _ = lin.apply(0.5, ctx(capture=1), rng)
    assert ap_lin is True


def _ctx_round(rid, capture):
    return WellContext(
        well_id="A1", row=1, col=1, rows=6, cols=8, is_edge=False,
        block_id="Q0", solution_batch="R0-B0", capture_index=capture, round_id=rid,
    )


def test_instrument_drift_resident_resume_equivalence_at_adapter():
    """resume 等价（判别测试 3）：连续 4 轮（单 sim）vs 2 轮+新 sim 续 2 轮，
    truth 测量序列逐孔恒等——因 resident 漂移确定性于 (seed,round)、无跨轮可变状态。"""
    params = {"mode": "resident", "rate_per_round": -0.04,
              "sigma_between": 0.015, "phi": 0.95, "sigma": 0.01}
    scen = [{"injector": "instrument_drift", "params": params}]

    def run_round(sim, rid):
        exp = make_experiment(round_id=rid, n_cands=6, seed=7)
        res = sim.execute(exp, np.random.default_rng(500 + rid))
        return [t["measured_value"] for t in res.truth_records]

    cont = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen})
    seq_cont = [run_round(cont, r) for r in range(4)]
    sim_a = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen})
    sim_b = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen})  # resume: 新实例续跑
    seq_resume = [run_round(sim_a, r) for r in range(2)] + \
                 [run_round(sim_b, r) for r in range(2, 4)]
    assert seq_cont == seq_resume, "resident 漂移跨 resume 不等价——违确定性-按种子契约"


def test_instrument_drift_ar1_mode_byte_identical_to_before():
    """旧 ar1 模式行为零变化护栏：resident 分支的加入不改 ar1 的逐孔输出。"""
    scen = [{"injector": "instrument_drift", "params": {"mode": "ar1", "phi": 0.95, "sigma": 0.01}}]
    exp = make_experiment(round_id=2, n_cands=6, seed=7)
    r1 = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen}).execute(exp, np.random.default_rng(3))
    r2 = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen}).execute(exp, np.random.default_rng(3))
    assert [r.model_dump() for r in r1.raw_results] == [r.model_dump() for r in r2.raw_results]
    # ar1 跨轮不持久：capture_index 回退仍响亮失败
    a = InstrumentDrift(mode="ar1")
    a.apply(0.5, ctx(capture=2), np.random.default_rng(0))
    with pytest.raises(AdapterError):
        a.apply(0.5, ctx(capture=1), np.random.default_rng(0))


def test_artifact_separation_from_truth():
    """注入器只污染测量值；truth sidecar 保留干净真值与透明标签。"""
    cfg_sim = {"noise_sd": 0.0, "artifact_scenario": [
        {"injector": "edge_evaporation", "params": {"strength": 0.5, "decay_wells": 1.0}}
    ]}
    exp = make_experiment()
    result = CrystalSim(cfg_sim).execute(exp, np.random.default_rng(0))
    by_well = {t["well_id"]: t for t in result.truth_records}
    edge_hit = center_clean = False
    for r in result.raw_results:
        t = by_well[r.well_id]
        if "edge_evaporation" in t["artifacts"]:
            assert r.value > t["true_value"]  # 测量被抬高
            edge_hit = True
        elif not t["artifacts"]:
            assert r.value == pytest.approx(t["true_value"])  # 无伪影无噪声 → 相等
            center_clean = True
    assert edge_hit and center_clean


def test_truth_only_from_simulators():
    """truth_records 的合法产地只有 adapters/sim_*；bench 不产 truth；
    qc/models/planner/agent 源码不得引用 truth 标识符。

    Delegates the four-package scan to lint rule EXP001 (AST identifier-level):
    a raw substring scan cannot coexist with the truth-isolation guard, whose
    BLACKLIST data must itself name the forbidden keys (llm_backend.py), nor
    with docstrings that legitimately state the "no hidden surface" invariant.
    Identifier-level semantics keeps the red line at full strength: any truth-
    named variable/attribute/function in these packages still fails."""
    import importlib.util as _ilu
    import sys as _sys
    _spec = _ilu.spec_from_file_location("expos_lint", ROOT / "scripts" / "expos_lint.py")
    _lint = _ilu.module_from_spec(_spec)
    _sys.modules.setdefault("expos_lint", _lint)
    _spec.loader.exec_module(_lint)
    findings = _lint.run_lint(ROOT, select=["EXP001"])
    assert not findings, f"EXP001 truth-identifier findings: {findings}"
    non_sim = [p for p in (ROOT / "expos" / "adapters").rglob("*.py")
               if not p.name.startswith("sim_") and p.name != "base.py"]
    # An honest ``truth_records=None`` ("I produce NO truth") is a LEGITIMATE
    # declaration for a non-sim adapter -- only GENERATING or CONSUMING real
    # truth_records is forbidden. Exempt the =None null-declaration before the
    # raw-substring scan (letter 145 (b): sequence_adapter's honest null was
    # false-flagged by the blanket substring).
    usage_patterns = ("truth_records=", ".truth_records", '["truth_records"]', "'truth_records'")
    for src in non_sim:
        text = src.read_text(encoding="utf-8")
        scrubbed = re.sub(r"truth_records\s*=\s*None", "", text)
        hits = [pat for pat in usage_patterns if pat in scrubbed]
        assert hits == [], f"{src} 生成/消费 truth_records: {hits}（docstring 提及/=None 诚实声明不算）"


# ---------------------------------------------------------------- ingest

def test_raw_to_observations_pending_and_no_verdict():
    exp = make_experiment()
    result = CrystalSim({"noise_sd": 0.01}).execute(exp, np.random.default_rng(0))
    obs = raw_to_observations(exp, result.raw_results)
    assert len(obs) == len(result.raw_results)
    for o in obs:
        assert o.trust == TrustLevel.PENDING
        assert o.qc is None and o.routing is None and o.failure_attr is None and o.next_action is None
    controls = [o for o in obs if o.is_control]
    assert len(controls) == 5


def test_csv_ingestion(tmp_path):
    exp = make_experiment()
    path = tmp_path / "results.csv"
    wells = [w.well_id for w in exp.layout.wells][:4]
    with path.open("w", newline="") as f:
        wr = csv_mod.writer(f)
        wr.writerow(["well_id", "value", "grain_count"])
        for i, wid in enumerate(wells):
            wr.writerow([wid, 0.3 + 0.1 * i, 12])
    obs = load_results_csv(path, exp)
    assert len(obs) == 4
    assert all(o.trust == TrustLevel.PENDING for o in obs)
    assert obs[0].result.secondary.get("grain_count") == 12
    assert obs[0].raw_ref.kind == "csv"


def test_csv_malformed_fails_loudly(tmp_path):
    exp = make_experiment()
    bad_col = tmp_path / "bad1.csv"
    bad_col.write_text("well,value\nA1,0.5\n")
    with pytest.raises(AdapterError):
        load_results_csv(bad_col, exp)  # 缺 well_id 列
    bad_well = tmp_path / "bad2.csv"
    bad_well.write_text("well_id,value\nZ99,0.5\n")
    with pytest.raises(AdapterError):
        load_results_csv(bad_well, exp)  # 未知孔位
    bad_val = tmp_path / "bad3.csv"
    bad_val.write_text(f"well_id,value\n{exp.layout.wells[0].well_id},oops\n")
    with pytest.raises(AdapterError):
        load_results_csv(bad_val, exp)  # 非数值
    with pytest.raises(AdapterError):
        load_results_csv(tmp_path / "ghost.csv", exp)  # 文件不存在


def test_image_metrics_stable_on_synthetic():
    img = np.zeros((60, 80), dtype=float)
    for i, (r, c) in enumerate([(5, 5), (5, 40), (30, 20), (45, 60), (50, 10)]):
        img[r:r + 6, c:c + 6] = 0.9
    m1 = crystal_metrics(img)
    m2 = crystal_metrics(img)
    assert m1 == m2  # 确定性
    assert m1["grain_count"] == 5
    assert m1["coverage"] == pytest.approx(5 * 36 / (60 * 80), rel=0.2)
    with pytest.raises(AdapterError):
        crystal_metrics(np.full((10, 10), 0.5))  # 全同值无法阈值化


# ---------------------------------------------------------------- bench worklist

def test_bench_manual_emits_worklist_and_platemap(tmp_path):
    exp = make_experiment()
    snapshot = exp.model_dump()
    bench = BenchManualAdapter()
    paths = bench.prepare(exp, tmp_path)
    assert exp.model_dump() == snapshot
    text = Path(paths["worklist"]).read_text(encoding="utf-8")
    assert exp.exp_id in text and "supersaturation" in text
    for w in exp.layout.wells[:3]:
        assert w.well_id in text
    with Path(paths["platemap"]).open() as f:
        rows = list(csv_mod.reader(f))
    assert len(rows) == exp.layout.rows + 1 and len(rows[0]) == exp.layout.cols + 1
    with pytest.raises(AdapterError):
        bench.execute(exp, np.random.default_rng(0))


# ---------------------------------------------------------------- 域装配与热插拔

@pytest.mark.parametrize("domain", ["crystal", "coating"])
def test_domain_hot_swap_same_generic_path(domain):
    """同一段泛型代码跑两个域——换域零内核改动的测试面。"""
    cfg = load_domain(ROOT / "domains" / f"{domain}.yaml")
    assert isinstance(cfg, DomainConfig)
    adapter = build_adapter(cfg)
    exp = make_experiment(domain=domain)
    snapshot = exp.model_dump()
    result = adapter.execute(exp, np.random.default_rng(0))
    assert exp.model_dump() == snapshot  # 两个域的 adapter 都不得变异 exp
    obs = raw_to_observations(exp, result.raw_results)
    assert len(obs) == len(exp.layout.wells)
    assert all(o.trust == TrustLevel.PENDING for o in obs)
    assert all(o.result.metric == cfg.objective.metric for o in obs)


def test_domain_config_fails_loudly(tmp_path):
    with pytest.raises(DomainError):
        load_domain(tmp_path / "nope.yaml")  # 不存在
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nadapter: sim_crystal\nunknown_key: 1\n")
    with pytest.raises(DomainError):
        load_domain(bad)  # 未知键 + 缺必需字段
    crystal = (ROOT / "domains" / "crystal.yaml").read_text(encoding="utf-8")
    wrong_inj = tmp_path / "inj.yaml"
    wrong_inj.write_text(crystal.replace("edge_evaporation", "edge_evap_typo"))
    with pytest.raises(DomainError):
        load_domain(wrong_inj)  # 拼错注入器名在加载期就炸
    wrong_adapter = tmp_path / "ad.yaml"
    wrong_adapter.write_text(crystal.replace("adapter: sim_crystal", "adapter: sim_ghost"))
    with pytest.raises(DomainError):
        load_domain(wrong_adapter)


def test_unknown_metric_fails_loudly():
    exp = make_experiment(domain="crystal")
    with pytest.raises(AdapterError):
        CoatingSim().execute(exp, np.random.default_rng(0))  # crystal 目标 vs coating 指标


def test_unknown_injector_and_bad_params_fail_loudly():
    with pytest.raises(AdapterError):
        build_injector("ghost_injector")
    with pytest.raises(AdapterError):
        build_injector("glare", {"probability": 1.0})  # 参数名拼错
    assert injectors_for_round([{"injector": "glare"}], round_id=5)[0].name == "glare"
    assert injectors_for_round([{"round": 3, "injector": "glare"}], round_id=5) == []


# ---------------------------------------------------------------- 审查补测（对抗审查 + 压力测试盲区）

def test_coating_true_surface_has_interior_optimum():
    cfg = load_domain(ROOT / "domains" / "coating.yaml")
    sim = CoatingSim()
    best_p, best_v = sim.true_optimum(cfg.design_space, n=2048, seed=0)
    assert best_v > 0.2
    for name in ("concentration", "dry_temp", "surfactant"):  # tilt 允许贴 0（物理单调）
        v = cfg.design_space.var(name)
        x = best_p[name]
        if v.transform == "log":
            x, lo, hi = np.log(x), np.log(v.low), np.log(v.high)
        else:
            lo, hi = v.low, v.high
        assert lo + 0.05 * (hi - lo) < x < hi - 0.05 * (hi - lo), f"{name} 贴边: {best_p[name]}"


def test_secondary_signature_is_params_only():
    """真值不入 secondary 的结构性守门：签名只允许 (self, params)。"""
    import inspect

    for cls in (CrystalSim, CoatingSim):
        sig = inspect.signature(cls.secondary)
        assert list(sig.parameters) == ["self", "params"], (
            f"{cls.__name__}.secondary 签名 {list(sig.parameters)}——"
            "带 true_value 参数就是 oracle 泄漏通道"
        )


def test_ar1_drift_state_resets_across_executes():
    """同一 adapter 实例连续执行两轮：injectors_for_round 每轮新实例，无状态污染。"""
    sim = CrystalSim({"noise_sd": 0.0, "artifact_scenario": [
        {"injector": "instrument_drift", "params": {"mode": "ar1", "phi": 0.95, "sigma": 0.01}}
    ]})
    exp = make_experiment()
    r1 = sim.execute(exp, np.random.default_rng(3))
    r2 = sim.execute(exp, np.random.default_rng(3))
    assert [r.model_dump() for r in r1.raw_results] == [r.model_dump() for r in r2.raw_results]


def test_glare_footprint_in_execute():
    exp = make_experiment()
    hit = CrystalSim({"noise_sd": 0.0, "artifact_scenario": [
        {"injector": "glare", "params": {"prob": 1.0, "boost": 0.3}}
    ]}).execute(exp, np.random.default_rng(0))
    clean = CrystalSim({"noise_sd": 0.0}).execute(exp, np.random.default_rng(0))
    assert all(r.exposure > 1.2 for r in hit.raw_results)     # 命中足迹（带噪但可分）
    assert all(r.exposure < 1.15 for r in clean.raw_results)  # 基线


def test_batch_assignment_decoupled_from_capture_order():
    exp = make_experiment()
    result = CrystalSim({"noise_sd": 0.0}).execute(exp, np.random.default_rng(0))
    ordered = sorted(result.raw_results, key=lambda r: r.capture_index)
    first4 = [r.solution_batch for r in ordered[:4]]
    assert len(set(first4)) == 2, f"批次应轮转交错而非阶梯: {first4}"


def test_image_metrics_pil_path_and_rgb(tmp_path):
    from PIL import Image

    img = np.zeros((40, 40), dtype=np.uint8)
    img[5:12, 5:12] = 230
    img[25:32, 20:27] = 230
    png = tmp_path / "plate.png"
    Image.fromarray(img).save(png)
    m_path = crystal_metrics(png)
    assert m_path["grain_count"] == 2
    rgb = np.stack([img, img, img], axis=-1).astype(float) / 255.0
    m_rgb = crystal_metrics(rgb)
    assert m_rgb["grain_count"] == 2


def test_image_metrics_empty_foreground_returns_zeros():
    img = np.zeros((30, 30))
    img[10:15, 10:15] = 0.4
    # 灰度先做动态范围归一化（0.4→1.0），故用 threshold=1.0 制造空前景
    m = crystal_metrics(img, threshold=1.0)
    assert m["grain_count"] == 0 and m["coverage"] == 0.0


def test_csv_duplicate_well_id_fails_loudly(tmp_path):
    exp = make_experiment()
    wid = exp.layout.wells[0].well_id
    path = tmp_path / "dup.csv"
    path.write_text(f"well_id,value\n{wid},0.3\n{wid},0.7\n")
    with pytest.raises(AdapterError):
        load_results_csv(path, exp)


def test_true_value_public_guards():
    """true_value 公开评分面：缺键/非数值/超物理域一律 AdapterError，绝不静默返回。"""
    ok = {"supersaturation": 1.2, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 1}
    sim = CrystalSim()
    assert 0.0 <= sim.true_value(ok) <= 1.0
    with pytest.raises(AdapterError):
        sim.true_value({**ok, "supersaturation": 99.0})  # 越界不再静默
    with pytest.raises(AdapterError):
        sim.true_value({k: v for k, v in ok.items() if k != "cool_rate"})  # 缺键
    with pytest.raises(AdapterError):
        sim.true_value({**ok, "cool_rate": "fast"})  # 类型错


# ---------------------------------------------------------------- 依赖隔离（M3 红线）

def test_adapters_import_no_forbidden_modules():
    # Import-level (AST), not raw substring: a docstring/comment cross-reference
    # like ``:func:`expos.qc.replicate_collapse` `` is documentation pointing at
    # where a field is CONSUMED, not a dependency-inverting import (letter 145 (a):
    # domain.py's replicate_kind docstring was false-flagged by the substring scan).
    # The red line is on actual imports, mirroring EXP007's AST discipline.
    import ast as _ast
    forbidden = ("expos.qc", "expos.planner", "expos.models", "expos.agent", "ui")
    for src in list((ROOT / "expos" / "adapters").rglob("*.py")) + [ROOT / "expos" / "domain.py"]:
        tree = _ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        mods: list[str] = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.module:
                mods.append(node.module)
            elif isinstance(node, _ast.Import):
                mods.extend(a.name for a in node.names)
        hits = [f for f in forbidden for m in mods if m == f or m.startswith(f + ".")]
        assert hits == [], f"{src} import 了禁区模块: {sorted(set(hits))}"


def test_resident_applied_excludes_ar1_transient():
    """RES3 P1（mailbox/red_to_blue/007）：applied 标签只看跨轮基线分量——
    纯轮内 AR(1)（sigma=0.01, rate_per_round=0, sigma_between=0, period_amp=0）
    时 applied 恒 False（旧判据含瞬态会把标签地板抬到 ~0.85 抹平剂量响应）。"""
    from expos.adapters.artifacts import build_injector

    inj = build_injector("instrument_drift", {
        "mode": "resident", "sigma": 0.01, "rate_per_round": 0.0,
        "sigma_between": 0.0, "period_amp": 0.0,
    })
    rng = np.random.default_rng(0)
    ctx_kw = dict(rows=6, cols=8, is_edge=False, block_id="Q0",
                  solution_batch="R0-B0", round_id=0)
    applied_flags = []
    for i in range(48):
        _, applied, _ = inj.apply(
            0.5, WellContext(well_id=f"w{i}", row=i // 8, col=i % 8,
                             capture_index=i, **ctx_kw), rng)
        applied_flags.append(applied)
    assert not any(applied_flags), f"纯轮内 AR(1) 不得亮 applied 标签: {sum(applied_flags)}/48"
