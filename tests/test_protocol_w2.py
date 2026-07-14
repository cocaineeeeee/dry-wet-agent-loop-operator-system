"""M16 W2 acceptance -- ProtocolObject + two-target compiler + (2) fingerprint.

Kill-oriented discipline: each loud guard is paired with a test that goes red if
the guard is deleted (annotated inline). The determinism guard has an additional
OUT-OF-BAND kill recorded in the W2 completion report: rewriting
``spec.canonical_json`` to ``str(spec.model_dump())`` (insertion-order
dependent) makes ``test_fingerprint_is_key_order_independent`` go red. Verified
once; see the report's mutation record.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from expos.protocol.spec import (
    CandidateBinding,
    ProtocolInputs,
    ProtocolSpec,
    ProtocolStep,
    canonical_json,
)
from expos.protocol.compiler import (
    CompileError,
    DryJobPlan,
    WetProtocolPlan,
    compile,
    compiler_source_sha,
    default_solvent_screen_config,
    protocol_fingerprint,
)

# A-side authoritative schemas (the wet round-trip target).
from expos.adapters.wet.protocol_spec import ProtocolSpec as WetSpec
from expos.adapters.wet.ot_protocol import compile_and_validate
# W3 authoritative dry job schema (the dry round-trip target).
from expos.adapters.dry.spec import JobSpec


# ---------------------------------------------------------------- builders

def _candidates() -> list[CandidateBinding]:
    return [
        CandidateBinding(cand_id="c00", params={"target_polarity": 0.30,
                                                "solvent": "water"}),
        CandidateBinding(cand_id="c01", params={"target_polarity": 0.52,
                                                "solvent": "methanol"}),
        CandidateBinding(cand_id="c02", params={"target_polarity": 0.75,
                                                "solvent": "acetone"}),
        CandidateBinding(cand_id="ctl0", params={"target_polarity": 0.525},
                         is_control=True, control_id="ctl0"),
    ]


def make_spec(**overrides) -> ProtocolSpec:
    base = dict(
        name="solvent_screen_min",
        version="1",
        inputs=ProtocolInputs(space_name="solvent_polarity", candidates=_candidates()),
        steps=[
            ProtocolStep(op="dry_compute", target="dry_metric",
                         params={"basis": "sto-3g", "method": "HF",
                                 "metric": "polarity_proxy"}),
            ProtocolStep(op="wet_assay", target="wet_response",
                         params={"total_volume_ul": 200.0}),
        ],
        expected_outputs=["dry_metric", "wet_response"],
        metadata={"round": 0, "author": "W2"},
    )
    base.update(overrides)
    return ProtocolSpec(**base)


# ================================================================ spec validation

def test_valid_spec_builds():
    spec = make_spec()
    assert spec.ops() == ["dry_compute", "wet_assay"]


def test_missing_steps_rejected():
    # GUARD missing-steps: an empty step list must be rejected loudly.
    with pytest.raises(ValidationError):
        make_spec(steps=[])


def test_unknown_op_rejected():
    # GUARD unknown-op: an op outside ALLOWED_OPS must be rejected at spec build.
    with pytest.raises(ValidationError):
        ProtocolStep(op="teleport", target="x")


def test_empty_outputs_rejected():
    # GUARD empty-outputs: expected_outputs must be non-empty.
    with pytest.raises(ValidationError):
        make_spec(expected_outputs=[])


def test_empty_candidates_rejected():
    # GUARD empty-inputs: a protocol that screens nothing is a no-op.
    with pytest.raises(ValidationError):
        ProtocolInputs(space_name="x", candidates=[])


def test_control_without_id_rejected():
    with pytest.raises(ValidationError):
        CandidateBinding(cand_id="c", is_control=True)  # missing control_id


def test_empty_target_rejected():
    with pytest.raises(ValidationError):
        ProtocolStep(op="dry_compute", target="")


# ================================================================ determinism

def test_compile_is_deterministic():
    spec = make_spec()
    a = compile(spec)
    b = compile(spec)
    # Fingerprint bit-for-bit identical...
    assert a.protocol_fingerprint == b.protocol_fingerprint
    # ...and the full compiled plans identical too.
    assert a.model_dump() == b.model_dump()


def test_fingerprint_is_key_order_independent():
    # This is the test the determinism kill targets: two specs identical except
    # for metadata dict INSERTION ORDER must hash identically. canonical_json
    # sorts keys => equal; str(dict) => unequal (the documented mutation).
    s1 = make_spec(metadata={"a": 1, "b": 2, "c": 3})
    s2 = make_spec(metadata={"c": 3, "b": 2, "a": 1})
    assert canonical_json(s1) == canonical_json(s2)
    assert compile(s1).protocol_fingerprint == compile(s2).protocol_fingerprint


# ================================================================ fingerprint sensitivity

def test_fingerprint_changes_when_a_param_changes():
    base = compile(make_spec()).protocol_fingerprint
    cands = _candidates()
    cands[0] = CandidateBinding(
        cand_id="c00",
        params={"target_polarity": 0.31, "solvent": "water"},  # 0.30 -> 0.31
    )
    changed = compile(
        make_spec(inputs=ProtocolInputs(space_name="solvent_polarity",
                                        candidates=cands))
    ).protocol_fingerprint
    assert base != changed


def test_fingerprint_changes_when_step_params_change():
    base = compile(make_spec()).protocol_fingerprint
    other = compile(make_spec(steps=[
        ProtocolStep(op="dry_compute", target="dry_metric",
                     params={"basis": "6-31g", "method": "HF"}),  # basis changed
        ProtocolStep(op="wet_assay", target="wet_response",
                     params={"total_volume_ul": 200.0}),
    ])).protocol_fingerprint
    assert base != other


def test_fingerprint_folds_in_compiler_source():
    # Proves the compiler-source-sha is a real hash component: a fake source sha
    # yields a different digest than the live one. Delete that term from
    # protocol_fingerprint => these two become equal => red.
    spec = make_spec()
    live = protocol_fingerprint(spec)
    faked = protocol_fingerprint(spec, source_sha="deadbeef")
    assert live != faked
    assert live == protocol_fingerprint(spec, source_sha=compiler_source_sha())


# ================================================================ dry target

def test_dry_plan_structure_and_schema_roundtrip():
    plan = compile(make_spec()).dry_plan
    assert isinstance(plan, DryJobPlan)
    assert plan.cmd_template[:3] == ["python3", "-m", "expos.adapters.dry.worker"]
    assert "{workdir}" in plan.cmd_template
    assert "result.json" in plan.expected_products
    assert len(plan.jobs) == 4  # 3 candidates + 1 control
    # Each input card validates against the REAL W3 JobSpec schema (extra=forbid),
    # i.e. the manifest is genuinely consumable by python -m ...dry.worker.
    seen_wells = set()
    for job in plan.jobs:
        js = JobSpec.model_validate(job.input_card)
        assert js.job_id == job.job_id
        assert js.well_id == job.well_id
        seen_wells.add(js.well_id)
        # xor: exactly one of cand/control id populated.
        assert (js.cand_id is None) != (js.control_id is None)
    assert len(seen_wells) == 4  # distinct plate wells per candidate


# ================================================================ wet target

def test_wet_plan_structure():
    plan = compile(make_spec()).wet_plan
    assert isinstance(plan, WetProtocolPlan)
    assert len(plan.samples) == 4
    assert plan.total_volume_ul == 200.0
    controls = [s for s in plan.samples if s.is_control]
    assert len(controls) == 1 and controls[0].control_id == "ctl0"


def test_wet_plan_roundtrips_through_opentrons_schema():
    plan = compile(make_spec()).wet_plan
    wet_spec = plan.to_wet_protocol_spec()
    assert isinstance(wet_spec, WetSpec)
    # The real opentrons (or fallback) stack must accept the compiled protocol.
    otp = compile_and_validate(wet_spec)
    assert otp.validated
    # One custody sample per compiled binding, ids preserved.
    assert len(otp.sample_ids()) == 4
    assert "SMP-CTL-ctl0" in otp.sample_ids()


def test_wet_missing_polarity_is_loud():
    # A wet_assay candidate without target_polarity cannot be compiled.
    cands = [CandidateBinding(cand_id="c00", params={"solvent": "water"})]
    spec = make_spec(inputs=ProtocolInputs(space_name="x", candidates=cands))
    with pytest.raises(CompileError):
        compile(spec)


# ================================================================ target selection

def test_dry_only_spec_emits_no_wet_plan():
    spec = make_spec(
        steps=[ProtocolStep(op="dry_compute", target="dry_metric")],
        expected_outputs=["dry_metric"],
    )
    compiled = compile(spec)
    assert compiled.dry_plan is not None
    assert compiled.wet_plan is None


def test_wet_only_spec_emits_no_dry_plan():
    spec = make_spec(
        steps=[ProtocolStep(op="wet_assay", target="wet_response",
                            params={"total_volume_ul": 200.0})],
        expected_outputs=["wet_response"],
    )
    compiled = compile(spec)
    assert compiled.wet_plan is not None
    assert compiled.dry_plan is None


def test_domain_cfg_does_not_change_fingerprint():
    # Fingerprint pins protocol IDENTITY (spec + compiler), NOT the environment
    # binding: a different domain_cfg must not move the fingerprint.
    spec = make_spec()
    cfg = default_solvent_screen_config()
    cfg2 = default_solvent_screen_config()
    cfg2.total_volume_ul = 150.0  # different env binding
    assert (compile(spec, cfg).protocol_fingerprint
            == compile(spec, cfg2).protocol_fingerprint)
