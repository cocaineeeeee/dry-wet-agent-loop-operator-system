"""M9 扩展臂验收：os-soft（软信任降权复归）+ rcgp（模型层稳健）。

场景（默认 crystal 的嫌疑分实测双峰——<0.3 或 ≥0.9，[0.3,0.6) 带近空且随轨迹漂移，
不可作确定性断言靶）：crystal 变体 = 眩光概率提到 0.2（glare 通道 FPR≈0、逐孔独立、
score 恒 0.90——与板级统计/轨迹无关）+ 域信任阈值 suspect_high=0.95（阈值本就是
域配置，SOFT_TRUST_PROPOSAL §5——w(s) 由既有阈值张成；内核 adjudicate/枚举零改动）。
于是眩光孔 suspicion=0.90 落入 [0.3, 0.95) → QUARANTINE 带确定性非空（每轮 6~13 孔）；
原第 3 轮强边缘 strength=0.5 的 suspicion=1.0 ≥ 0.95 → TO_FAILURE_MODEL，绝不复归。

- os-soft：QUARANTINE 观测落盘 routing 不变（枚举没动）、软并入使 n_train 高于
  os 臂同 seed（且 == 自身 n_trusted + QUARANTINE 数——合并账目精确）、
  强档假最优仍被拒（best_trusted ≤ 1.0）；
- rcgp：信任盲 + RobustResponseModel 跑通、summary 完整；
- 四→五元组解包与零 mode 分支红线（与 test_loop_os.py 交叉验证）。
"""

from pathlib import Path

import pytest
import yaml

from expos.domain import load_domain
from expos.kernel.objects import Routing, TrustLevel
from expos.kernel.store import RunStore
from expos.loop import _policies_for_mode, run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"

#: 变体域信任阈值（与 fixture 写入 yaml 的值一致——断言引用同一常量防漂移）
SUSPECT_HIGH, QUARANTINE_LOW = 0.95, 0.3


def _final_n_train(out: Path) -> int:
    return RunStore(out, create=False).read_events("model_updated")[-1]["payload"]["n_train"]


def _quarantine_obs(out: Path) -> list:
    store = RunStore(out, create=False)
    return [o for o in store.list_observations(trust=TrustLevel.SUSPECT)
            if o.routing == Routing.QUARANTINE]


@pytest.fixture(scope="module")
def band_yaml(tmp_path_factory):
    """crystal 变体：眩光 prob→0.2（QUARANTINE 带确定性非空，score 恒 0.9）
    + suspect_high→0.95（0.9 落带内、强边缘 1.0 仍硬隔离）；
    原第 3 轮强边缘 0.5 档保留（假最优拒斥断言的靶）。"""
    cfg = yaml.safe_load(CRYSTAL.read_text(encoding="utf-8"))
    for item in cfg["simulator"]["artifact_scenario"]:
        if item.get("injector") == "glare":
            item["params"]["prob"] = 0.2
    cfg["trust"] = {"suspect_high": SUSPECT_HIGH, "quarantine_low": QUARANTINE_LOW}
    p = tmp_path_factory.mktemp("dom") / "crystal_band.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def soft_run(band_yaml, tmp_path_factory):
    """5 轮 crystal（眩光带变体）os-soft，seed=7。"""
    out = tmp_path_factory.mktemp("runs") / "os_soft"
    summary = run_loop(band_yaml, mode="os-soft", rounds=5, seed=7, out_dir=out)
    return out, summary


@pytest.fixture(scope="module")
def os_run(band_yaml, tmp_path_factory):
    """同 seed 同场景 os 臂对照（硬隔离 SUSPECT 出训练集）。"""
    out = tmp_path_factory.mktemp("runs") / "os"
    summary = run_loop(band_yaml, mode="os", rounds=5, seed=7, out_dir=out)
    return out, summary


# ---------------------------------------------------------------- os-soft

def test_quarantine_routing_unchanged_on_disk(soft_run):
    """软化只在聚合层内存态：落盘的 QUARANTINE 观测路由/信任一律不变（枚举没动）。"""
    out, _ = soft_run
    quar = _quarantine_obs(out)
    assert len(quar) > 0, "眩光带场景下软信任带必须非空"
    for o in quar:
        assert o.routing == Routing.QUARANTINE  # 未被改写成 TO_RESPONSE_MODEL
        assert o.trust == TrustLevel.SUSPECT     # 落盘仍是 SUSPECT
        # 嫌疑分在变体域的 QUARANTINE 带内
        assert QUARANTINE_LOW <= o.trust_confidence < SUSPECT_HIGH
    # 合成 TRUSTED 副本不落盘：任何落盘 TRUSTED 观测的嫌疑分都 < quarantine_low
    store = RunStore(out, create=False)
    for o in store.list_observations(trust=TrustLevel.TRUSTED):
        assert o.qc is None or o.qc.suspicion < QUARANTINE_LOW


def test_soft_ingests_more_than_os(soft_run, os_run):
    """软并入生效：os-soft 末轮 n_train 高于 os 臂同 seed，且合并账目精确
    （n_train == n_trusted + QUARANTINE 数；os 臂 n_train == n_trusted）。"""
    soft_out, soft_summary = soft_run
    os_out, os_summary = os_run
    n_soft, n_os = _final_n_train(soft_out), _final_n_train(os_out)
    # os 臂硬隔离：训练集 == TRUSTED（test_loop_os.py 同款不变量）
    assert n_os == os_summary["n_trusted"]
    # 软臂账目：训练集 == TRUSTED + QUARANTINE（每条软信任观测恰好一份合成副本）
    assert n_soft == soft_summary["n_trusted"] + len(_quarantine_obs(soft_out))
    assert n_soft > n_os, f"n_train soft={n_soft} 未超过 os={n_os}"


def test_soft_still_rejects_fake_optimum(soft_run):
    """强档假最优仍被拒：第 3 轮强边缘 suspicion=1.0 ≥ suspect_high → TO_FAILURE_MODEL，
    不在 QUARANTINE 集，绝不复归 → best_trusted 保持物理合理（≤1.0 真值面上限）。"""
    out, summary = soft_run
    assert summary["best_trusted"]["value"] <= 1.0
    # 强档观测确实走了 TO_FAILURE_MODEL（存在且与软信任带互斥）
    store = RunStore(out, create=False)
    hard = [o for o in store.list_observations(trust=TrustLevel.SUSPECT)
            if o.routing == Routing.TO_FAILURE_MODEL]
    assert len(hard) > 0
    for o in hard:
        assert o.routing != Routing.QUARANTINE  # 永不进软信任集


# ---------------------------------------------------------------- rcgp

def test_rcgp_runs_and_summary_complete(tmp_path):
    summary = run_loop(CRYSTAL, mode="rcgp", rounds=3, seed=7, out_dir=tmp_path / "rcgp")
    assert summary["rounds_completed"] == 3
    assert summary["n_observations"] > 0
    for k in ("domain", "n_trusted", "n_suspect", "n_failed", "best_trusted"):
        assert k in summary
    # 信任盲（NaivePolicy）：无 QC/路由 → 全部 TRUSTED
    assert summary["n_suspect"] == 0 and summary["n_failed"] == 0
    assert summary["n_trusted"] == summary["n_observations"]
    assert (tmp_path / "rcgp" / "report" / "summary.json").exists()
    # 模型快照逐轮落账（RobustResponseModel.snapshot 接线成功）
    assert len(RunStore(tmp_path / "rcgp", create=False).read_events("model_updated")) == 3


# ---------------------------------------------------------------- 五→六元组 / 零分支兼容

def test_policies_return_six_tuple_all_modes():
    """五臂均返回 (verdict, aggregation, planner, agent, model_factory, promotion)
    六元组（M16 W7 加第六注入元）。前五元逐位不变，第六元现行全臂 NullPromotion
    （decide()->None、零行为——既有五臂 e2e 逐位回归的前提）。"""
    from expos.planner.promotion import NullPromotion
    cfg = load_domain(CRYSTAL)
    for mode in ("naive", "robust", "rcgp", "os", "os-soft"):
        tup = _policies_for_mode(mode, cfg, 7)
        assert len(tup) == 6
        assert callable(tup[4])  # model_factory
        assert isinstance(tup[5], NullPromotion)  # 第六元惰性
        assert tup[5].decide([], None, "fp", None) is None


def test_loop_body_zero_mode_branch():
    """零 mode 分支红线（复核）：mode 值判定只许出现在 _policies_for_mode 内。"""
    src = (ROOT / "expos" / "loop.py").read_text(encoding="utf-8")
    body = src.split("def _policies_for_mode", 1)[1].split("\ndef ", 1)[0]
    outside = src.replace(body, "")
    for needle in ('mode == "naive"', 'mode == "os"', 'mode == "os-soft"',
                   'mode == "rcgp"', 'mode == "robust"'):
        assert needle not in outside


# ================================================================ VNext batch-1: explicit weight transport

def test_soft_admits_original_quarantine_objects_no_synthetic_copy():
    """Covert-channel revival guard: prepare() must append the ORIGINAL
    QUARANTINE observations (identity-preserved, trust/routing/confidence all
    untouched) -- re-introducing a mutated synthetic copy turns this red."""
    from expos.qc.policy import SoftTrustAggregation
    from expos.kernel.objects import TrustLevel, Routing
    agg = SoftTrustAggregation()
    trusted, quar = _mk_soft_fixture()          # helper below
    train, alpha = agg.prepare(trusted, [], quarantine=quar)
    extras = train[len(trusted):]
    assert len(extras) == len(quar)
    for orig, got in zip(quar, extras):
        assert got is orig                                     # identity, not a copy
        assert got.trust == TrustLevel.SUSPECT
        assert got.routing == Routing.QUARANTINE
        assert got.trust_confidence == orig.trust_confidence   # facet untouched


def test_soft_weight_reads_qc_suspicion_not_trust_confidence():
    """HY-1 RESCUE fix: a human reclassify to QUARANTINE stamps
    trust_confidence=1.0 (adjudication certainty). The weight must come from
    qc.suspicion (in-band 0.4 -> w≈0.667), never from the 1.0 stamp (which the
    old read slammed to the w_min=0.05 floor, inverting the human intent)."""
    from expos.qc.policy import SoftTrustAggregation
    agg = SoftTrustAggregation()
    trusted, quar = _mk_soft_fixture(suspicion=0.4, confidence=1.0)
    agg.prepare(trusted, [], quarantine=quar)
    (entry,) = agg.last_learning_weights
    expected_w = (0.6 - 0.4) / (0.6 - 0.3)
    assert entry["weight"] == pytest.approx(expected_w)
    assert entry["weight"] > 0.5           # decisively NOT the 0.05 floor
    assert entry["basis"] == "trust_mapping_v1"
    assert entry["cert_class"] is None     # reserved, unconsumed


def test_learning_weight_emission_soft_vs_naive(soft_run, band_yaml, tmp_path):
    """Emission-face discriminant on a real closed loop: the os-soft arm emits
    learning_weight_assigned whose entries are in 1:1 correspondence with the
    on-disk QUARANTINE observations; the trust-blind naive arm emits ZERO such
    events. EVENT_PAYLOAD_REQUIRED validation (validate=True) is clean."""
    soft_out, _ = soft_run
    soft_store = RunStore(soft_out, create=False)
    lw_events = soft_store.read_events("learning_weight_assigned", validate=True)
    assert len(lw_events) > 0
    # new kind passes payload-structure validation (round_id, entries present)
    assert soft_store.last_payload_violations == []
    # emitted entry obs_ids == the set of on-disk QUARANTINE observations
    quar_ids = {o.obs_id for o in _quarantine_obs(soft_out)}
    assert quar_ids, "band scenario must produce a non-empty soft-trust band"
    emitted_ids = {e["obs_id"] for ev in lw_events for e in ev["payload"]["entries"]}
    assert emitted_ids == quar_ids
    for ev in lw_events:
        assert ev["payload"]["pv"] == 1
        for e in ev["payload"]["entries"]:
            assert e["basis"] == "trust_mapping_v1"
            assert e["cert_class"] is None
            assert 0.0 < e["weight"] <= 1.0
    # naive arm: trust-blind -> no soft admission -> no emission at all
    naive_out = tmp_path / "naive"
    run_loop(band_yaml, mode="naive", rounds=5, seed=7, out_dir=naive_out)
    naive_store = RunStore(naive_out, create=False)
    assert naive_store.read_events("learning_weight_assigned") == []


def _mk_soft_fixture(suspicion: float = 0.45, confidence: float | None = None):
    """One TRUSTED + one QUARANTINE observation with a real QCReport."""
    from expos.kernel.objects import (
        ObservationObject, TrustLevel, Routing, QCReport, QCCheck,
    )
    def _obs(i, trust, routing, conf, susp):
        return ObservationObject(
            obs_id=f"obs-soft-{i}", exp_id="e1", cand_id=f"c{i}", round_id=0,
            result={"metric": "yield", "value": 0.4 + 0.01 * i},
            layout_meta={"well_id": f"A{i+1}", "row": 0, "col": i},
            material_meta={"solution_batch": "R0-B0", "prep_order": i},
            trust=trust, routing=routing, trust_confidence=conf,
            qc=QCReport(checks=[QCCheck(name="edge_effect", level="structural",
                                        passed=susp == 0.0, score=susp)],
                        flags=[], suspicion=susp),
        )
    trusted = [_obs(0, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL, 0.9, 0.0)]
    quar = [_obs(1, TrustLevel.SUSPECT, Routing.QUARANTINE,
                 confidence if confidence is not None else suspicion, suspicion)]
    return trusted, quar
