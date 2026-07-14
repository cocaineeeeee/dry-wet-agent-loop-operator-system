"""M24 mcl wiring batch (session B): the five mcl-side items that make the loop drive a
biological domain while keeping chemistry byte-identical.

  1. dry-leg dispatch by Contract-v3 capability (molecular_geometry -> PySCF async job;
     sequence_construct -> the SYNCHRONOUS SequenceProxy, no compute lease / no subprocess);
  2. controls dispatch (negative/positive/reference -> layout; reference carries a
     semantic_role marker; a domain with no controls lays none);
  3. plate_offset reader-fault injection surface (truth isolation preserved);
  4. store-only construct-lineage params field (distinct from parent_obs_id);
  5. replicate_kind threaded into AggregatedCertification construction (the harness one-liner
     that activates the landed technical-replicate collapse; biological => full n).

Chemistry (solvent/catalyst) is the HARD regression anchor: the dry dispatch selects the
UNCHANGED PySCF async path and every M24 item gates OFF (no controls / no normalization /
no lineage / no plate_offset), so the decision face is byte-identical (the k_c / w9 / m20
regression suites pin the actual fingerprint chain + proposal order).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import yaml

from expos.adapters.domain_provider import (
    INPUT_KIND_MOLECULAR_GEOMETRY,
    INPUT_KIND_SEQUENCE_CONSTRUCT,
    ComputeTarget,
)
from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.sequence_adapter import SequenceProxyAdapter
from expos.adapters.wet import sim_reader
from expos.adapters.wet.screen import DRY_METRIC
from expos.domain import DomainConfig, load_domain
from expos.kernel.objects import (
    Candidate,
    InstrumentMeta,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    TrustLevel,
)
from expos.mcl import (
    _DRY_METRIC_RANGE,
    _LINEAGE_PARAMS_KEY,
    _NORMALIZED_METRIC_RANGE,
    _REFERENCE_SEMANTIC_ROLE,
    _DomainBindings,
    _candidate_params,
    _domain_bindings,
    _domain_controls,
    _inject_reader_faults,
    _make_dry_leg_plan,
    _percent_of_control_normalize,
    _wet_experiment,
    run_mcl_loop,
)
from expos.planner.certification import AggregatedCertification
from expos.qc.certification_stats import AggregationConfig, ClaimHead
from expos.scheduler import LeaseManager

_REPO = Path(__file__).resolve().parents[1]
_BIO = _REPO / "domains" / "cell_free_expression_screen.yaml"
_CATALYST = _REPO / "domains" / "catalyst_screen.yaml"


# ------------------------------------------------------------------ fixtures / helpers


def _bio_cfg_with_controls(*, roles=("negative", "positive", "reference")) -> DomainConfig:
    """The real bio yaml, re-loaded with an added ``controls`` block (the schema this batch
    added). Built via ``model_validate`` on the raw dict so the additive field is exercised
    without touching the shipped yaml (A-side, do-not-modify)."""
    raw = yaml.safe_load(_BIO.read_text(encoding="utf-8"))
    raw["controls"] = [
        {"control_id": f"ctl_{r}", "role": r, "params": {}} for r in roles
    ]
    return DomainConfig.model_validate(raw)


def _rpc(host: str, port: int, req: dict, timeout: float = 5.0) -> dict:
    """One JSON-line request/response against the sim reader."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("reader closed without reply")
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _serve_reader(profile: str = "expression_high"):
    """Start an in-process reader on a free port; return (host, port, server)."""
    host = "127.0.0.1"
    s = socket.socket()
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    srv = sim_reader.serve(host, port, seed=0, noise_sd=0.0, truth_profile=profile)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.02)
    return host, port, srv


def _control_obs(control_id: str, value: float, capture: int) -> ObservationObject:
    return ObservationObject(
        obs_id=f"obs_{control_id}_{capture}",
        exp_id="exp_test",
        round_id=0,
        control_id=control_id,
        is_control=True,
        result=MeasuredResult(metric="expression_fluorescence", value=value, unit="au"),
        layout_meta=LayoutMeta(well_id=f"W{capture}", row=0, col=capture),
        instrument_meta=InstrumentMeta(capture_index=capture),
        trust=TrustLevel.TRUSTED,
    )


def _cand_obs(cand_id: str, value: float, capture: int) -> ObservationObject:
    return ObservationObject(
        obs_id=f"obs_{cand_id}_{capture}",
        exp_id="exp_test",
        round_id=0,
        cand_id=cand_id,
        result=MeasuredResult(metric="expression_fluorescence", value=value, unit="au"),
        layout_meta=LayoutMeta(well_id=f"W{capture}", row=0, col=capture),
        instrument_meta=InstrumentMeta(capture_index=capture),
        trust=TrustLevel.TRUSTED,
    )


# ============================================================ 1. dry-leg dispatch


def test_chem_dispatch_selects_pyscf_async(tmp_path):
    """molecular_geometry capability -> the UNCHANGED PySCF async-job dry path."""
    cfg = load_domain(_CATALYST)
    bindings = _domain_bindings(cfg)
    assert bindings.dry_capability == INPUT_KIND_MOLECULAR_GEOMETRY
    plan = _make_dry_leg_plan(cfg, bindings, tmp_path)
    assert isinstance(plan.adapter, PySCFDryAdapter)
    assert plan.kind == "async_job"
    assert plan.metric == DRY_METRIC
    assert plan.metric_range == _DRY_METRIC_RANGE


def test_bio_dispatch_selects_sequence_sync(tmp_path):
    """sequence_construct capability -> the SYNCHRONOUS SequenceProxy dry leg, selected by
    CAPABILITY (not cfg.adapter): the plan routes to the sync execute path."""
    cfg = load_domain(_BIO)
    bindings = _domain_bindings(cfg)
    assert bindings.dry_capability == INPUT_KIND_SEQUENCE_CONSTRUCT
    plan = _make_dry_leg_plan(cfg, bindings, tmp_path)
    assert isinstance(plan.adapter, SequenceProxyAdapter)
    assert plan.kind == "sync_execute"
    assert plan.metric == SequenceProxyAdapter.default_metric  # expression_proxy


def test_bio_dry_leg_runs_sync_no_lease_no_subprocess(tmp_path, monkeypatch):
    """A full bio round closes through the SYNC sequence dry leg: NO compute (``dry``) lease
    is taken and the PySCF subprocess backend is NEVER constructed."""
    lease_tags: list[str] = []
    orig_acquire = LeaseManager.acquire

    def spy_acquire(self, resource_id, *args, **kwargs):
        lease_tags.append(kwargs.get("tag"))
        return orig_acquire(self, resource_id, *args, **kwargs)

    monkeypatch.setattr(LeaseManager, "acquire", spy_acquire)

    class _NoSubprocess:
        def __init__(self, *a, **k):
            raise AssertionError("the sync sequence dry leg must not spawn a subprocess")

    monkeypatch.setattr("expos.mcl.SubprocessBackend", _NoSubprocess)

    summary = run_mcl_loop(
        _BIO, rounds=1, seed=7, out_dir=tmp_path / "run",
        truth_profile="expression_high",
    )
    assert summary["rounds_completed"] == 1
    assert summary["n_dry"] > 0  # the sync dry leg produced observations
    assert "dry" not in lease_tags  # no compute lease for the in-process dry leg
    assert "wet" in lease_tags  # the wet leg still takes its instrument lease


def test_chem_all_m24_items_gate_off(tmp_path):
    """The chemistry anchor: every M24 item is inert. PySCF async dispatch, no controls,
    no readout normalization, descriptor (not sequence) params."""
    cfg = load_domain(_CATALYST)
    bindings = _domain_bindings(cfg)
    assert _make_dry_leg_plan(cfg, bindings, tmp_path).kind == "async_job"
    assert _domain_controls(cfg) == []
    assert bindings.params_kind != "sequence"
    # no controls declared -> percent-of-control returns None (raw readout, byte-identical)
    assert _percent_of_control_normalize(cfg, []) is None


# ============================================================ 2. controls dispatch


def test_controls_three_roles_map_to_kinds():
    """negative/positive -> native kinds; reference -> sentinel + semantic_role marker."""
    cfg = _bio_cfg_with_controls()
    controls = _domain_controls(cfg)
    by_id = {c.control_id: c for c in controls}
    assert by_id["ctl_negative"].kind == "negative"
    assert by_id["ctl_positive"].kind == "positive"
    ref = by_id["ctl_reference"]
    assert ref.kind == "sentinel"
    assert ref.params.get("semantic_role") == _REFERENCE_SEMANTIC_ROLE
    # negative/positive carry NO semantic_role marker (only reference does)
    assert "semantic_role" not in by_id["ctl_negative"].params


def test_controls_reach_wet_experiment_layout():
    """The declared trio is laid onto the wet ExperimentObject (which the wet leg realises
    into plate wells)."""
    cfg = _bio_cfg_with_controls()
    bindings = _domain_bindings(cfg)
    wet_exp = _wet_experiment(cfg, 0, [Candidate(cand_id="cand_j23100", params={"construct": "j23100"})], bindings)
    assert {c.control_id for c in wet_exp.controls} == {"ctl_negative", "ctl_positive", "ctl_reference"}


def test_no_controls_lays_none():
    """A domain with no controls block (every chemistry domain, and the bare bio yaml) lays
    none -- the ExperimentObject.controls default, byte-identical wet plate."""
    assert _domain_controls(load_domain(_CATALYST)) == []
    assert _domain_controls(load_domain(_BIO)) == []


# ============================================================ 3. plate_offset injection


def test_plate_offset_truth_isolation():
    """Injected plate_offset reaches the truth sidecar and shifts the value, but the
    OS-visible reading carries NO plate_offset field (truth isolation)."""
    host, port, srv = _serve_reader()
    try:
        _inject_reader_faults(host, port, {"plate_offsets": {"": 0.08}})
        acq = _rpc(host, port, {"cmd": "acquire", "holder": "t", "ttl": 30.0})
        lease_id = acq["lease_id"]
        meas = _rpc(host, port, {
            "cmd": "measure", "lease_id": lease_id,
            "samples": [{"sample_id": "s1", "well_id": "A1", "polarity": 0.5}],
        })
        reading = meas["readings"][0]
        assert "plate_offset" not in reading  # OS face carries no fault truth
        truth = _rpc(host, port, {"cmd": "truth_dump"})["truth_records"]
        rec = next(r for r in truth if r["sample_id"] == "s1")
        assert rec["plate_offset"] == pytest.approx(0.08)  # truth sidecar records it
        # the corrupted value reaches the OS reading (offset folded into value)
        assert reading["value"] == pytest.approx(rec["value"], abs=1e-6)
    finally:
        srv.shutdown()
        srv.server_close()


def test_plate_offset_absent_is_no_fault():
    """Without injection, the plate_offset truth is 0.0 (no fault)."""
    host, port, srv = _serve_reader()
    try:
        acq = _rpc(host, port, {"cmd": "acquire", "holder": "t", "ttl": 30.0})
        _rpc(host, port, {
            "cmd": "measure", "lease_id": acq["lease_id"],
            "samples": [{"sample_id": "s1", "well_id": "A1", "polarity": 0.5}],
        })
        rec = _rpc(host, port, {"cmd": "truth_dump"})["truth_records"][0]
        assert rec["plate_offset"] == 0.0
    finally:
        srv.shutdown()
        srv.server_close()


# ============================================================ 4. lineage params field


def _sequence_bindings(target: ComputeTarget) -> _DomainBindings:
    return _DomainBindings(
        variable="construct",
        candidate_pool=("c1",),
        coords={"c1": 1.0},
        descriptors={"c1": {"coord": 1.0}},
        coord_name="coord",
        window=(0.0, 1.0),
        prefer_higher_default=True,
        higher_hyp_ids=(),
        lower_hyp_ids=(),
        fixed_conditions={},
        params_kind="sequence",
        dry_capability=INPUT_KIND_SEQUENCE_CONSTRUCT,
        compute_targets={"c1": target},
    )


def test_lineage_rides_new_params_field_not_parent_obs_id():
    """Construct lineage lands in the dedicated params field, NOT parent_obs_id (whose
    semantics are replicate provenance)."""
    target = ComputeTarget(
        target_id="c1",
        input_kind=INPUT_KIND_SEQUENCE_CONSTRUCT,
        payload={
            "sequence": "ATGCATGC", "promoter": "P", "rbs": "R", "cds": "ATG",
            "parent_construct": "c0", "sequence_version": "v2",
        },
        payload_schema_version="sequence_construct/1",
        adapter_capability=INPUT_KIND_SEQUENCE_CONSTRUCT,
    )
    params = _candidate_params("c1", _sequence_bindings(target))
    # dry-adapter input forwarded verbatim; screening key carries the level
    assert params["sequence"] == "ATGCATGC"
    assert params["construct"] == "c1"
    # lineage lifted into the dedicated field, and NOT left as top-level dry input
    assert params[_LINEAGE_PARAMS_KEY] == {"parent_construct": "c0", "sequence_version": "v2"}
    assert "parent_construct" not in params and "sequence_version" not in params
    # the candidate's replicate-provenance field is UNTOUCHED (distinct from design lineage)
    cand = Candidate(cand_id="cand_c1", params=params)
    assert cand.parent_obs_id is None
    assert _LINEAGE_PARAMS_KEY in cand.params


def test_lineage_absent_when_payload_has_none():
    """A construct with no lineage in its payload carries no lineage field (store-only, v1)."""
    target = ComputeTarget(
        target_id="c1",
        input_kind=INPUT_KIND_SEQUENCE_CONSTRUCT,
        payload={"sequence": "ATGC", "promoter": "P"},
        payload_schema_version="sequence_construct/1",
        adapter_capability=INPUT_KIND_SEQUENCE_CONSTRUCT,
    )
    params = _candidate_params("c1", _sequence_bindings(target))
    assert _LINEAGE_PARAMS_KEY not in params


# ============================================================ 5. replicate_kind wiring


def _head() -> ClaimHead:
    return ClaimHead(
        claim_id="c_focal_higher",
        statement="focal higher",
        favorable_direction="higher",
        focal_group=("F1",),
        reference_group=("R1",),
    )


def test_replicate_kind_technical_activates_collapse():
    """cfg.replicate_kind='technical' -> AggregatedCertification constructed with it
    (collapse active)."""
    cfg = DomainConfig.model_validate(
        {**yaml.safe_load(_BIO.read_text(encoding="utf-8")), "replicate_kind": "technical"}
    )
    cert = AggregatedCertification(
        [_head()], config=AggregationConfig(seed=0), replicate_kind=cfg.replicate_kind
    )
    assert cert._replicate_kind == "technical"


def test_replicate_kind_biological_reaches_compiler_full_n():
    """The shipped bio yaml declares 'biological' (independent evidence): the collapse does
    NOT fire, so observations reach the compiler at full n (byte-identical to legacy)."""
    cfg = load_domain(_BIO)
    assert cfg.replicate_kind == "biological"
    cert = AggregatedCertification(
        [_head()], config=AggregationConfig(seed=0), replicate_kind=cfg.replicate_kind
    )
    assert cert._replicate_kind == "biological"  # != "technical" -> no collapse (full n)


def test_replicate_kind_none_is_legacy():
    """No replicate_kind (chemistry) -> the certification does not collapse (legacy anchor)."""
    cfg = load_domain(_CATALYST)
    assert cfg.replicate_kind is None
    cert = AggregatedCertification(
        [_head()], config=AggregationConfig(seed=0), replicate_kind=cfg.replicate_kind
    )
    assert cert._replicate_kind is None


# ============================================================ 4b. readout normalization


def test_percent_of_control_normalizes_against_controls():
    """With a negative+positive control pair declared, the readout is percent-of-control
    normalized: positive -> ~100, negative -> ~0, and the QC range switches to the percent
    scale."""
    cfg = _bio_cfg_with_controls()
    wet_obs = [
        _control_obs("ctl_negative", 0.10, 0),
        _control_obs("ctl_positive", 1.10, 1),
        _cand_obs("cand_j23100", 1.10, 2),   # == positive -> 100
        _cand_obs("cand_j23103", 0.10, 3),   # == negative -> 0
        _cand_obs("cand_mid", 0.60, 4),      # halfway -> 50
    ]
    result = _percent_of_control_normalize(cfg, wet_obs)
    assert result is not None
    normalized, rng = result
    assert rng == _NORMALIZED_METRIC_RANGE
    by_id = {(o.cand_id or o.control_id): o.result.value for o in normalized}
    assert by_id["cand_j23100"] == pytest.approx(100.0)
    assert by_id["cand_j23103"] == pytest.approx(0.0)
    assert by_id["cand_mid"] == pytest.approx(50.0)


def test_percent_of_control_none_without_pair():
    """Only a reference control (no negative+positive pair) -> no percent scale, raw readout."""
    cfg = _bio_cfg_with_controls(roles=("reference",))
    assert _percent_of_control_normalize(cfg, [_cand_obs("c", 1.0, 0)]) is None
