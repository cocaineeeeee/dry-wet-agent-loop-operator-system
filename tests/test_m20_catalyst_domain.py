"""M20 second-domain (catalyst_screen) discriminative minimal set.

The point is NOT a pretty demo but the swappable-runtime proof: the SAME wet
generalization + truth surface + dry adapter serve a second domain with only the
domain tables (adapters/dry/catalysts.py) and one yaml changed. Minimal set per
the narrowing order (four discriminators, no matrix):

  (a) descriptors-injected wet generalization: the catalyst categorical level maps
      to the correct realised coordinate via an INJECTED descriptors table, AND the
      solvent default path (descriptors=None) is bit-for-bit the legacy formula;
  (b) catalyst truth face sign: `catalyst_high` gives a positive coord->response
      relation (high-coordinate ligands respond higher), obeying the only-mu-differs
      K-D law ACROSS domains (polar_high untouched);
  (c) dry leg: one real PySCF ligand job converges and lands a `reactivity_proxy`
      value through the UNCHANGED PySCFDryAdapter;
  (d) delete-the-guard-goes-red: a candidate whose level is absent from descriptors
      is REJECTED loudly (never silently skipped).
"""

from __future__ import annotations

import numpy as np
import pytest

from expos.adapters.base import AdapterError
from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.catalysts import CATALYST_DESCRIPTORS, catalyst_params
from expos.adapters.wet import sim_reader
from expos.adapters.wet.screen import (
    P_TARGET_HI,
    P_TARGET_LO,
    SOLVENT_POLARITY,
    compile_wet,
    protocol_spec_from_experiment,
    target_coord,
)
from expos.kernel.objects import (
    Budget,
    Candidate,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutAssignment,
    Objective,
    VariableDef,
    WellAssignment,
)
from expos.scheduler import SubprocessBackend

_CATALYSTS = ["pf3", "pme3", "ph3", "pcl3", "nh3"]


def _realised(coord: float) -> float:
    """The mixable-window mapping target_coord applies (coord in [0,1])."""
    return P_TARGET_LO + coord * (P_TARGET_HI - P_TARGET_LO)


def _catalyst_conditions(level: str) -> dict:
    return {
        "catalyst": level,
        "residence_time": 10.0,
        "temperature": 60.0,
        "catalyst_loading": 2.0,
    }


def _catalyst_wet_exp(levels: list[str]) -> ExperimentObject:
    return ExperimentObject(
        exp_id="m20_cat",
        round_id=0,
        domain="catalyst_screen",
        objective=Objective(name="yield", metric="catalyst_yield"),
        design_space=DesignSpace(
            name="catalyst_screen",
            variables=[
                VariableDef(name="catalyst", kind="categorical", choices=list(levels)),
            ],
        ),
        active_vars=["catalyst"],
        candidates=[
            Candidate(cand_id=f"cand_{lv}", params=_catalyst_conditions(lv))
            for lv in levels
        ],
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="test"),
    )


def _solvent_wet_exp(solvents: list[str]) -> ExperimentObject:
    return ExperimentObject(
        exp_id="m20_solv",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="response", metric="solvent_response"),
        design_space=DesignSpace(
            name="solvent_screen",
            variables=[
                VariableDef(name="solvent", kind="categorical", choices=list(solvents)),
            ],
        ),
        active_vars=["solvent"],
        candidates=[
            Candidate(cand_id=f"cand_{s}", params={"solvent": s}) for s in solvents
        ],
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="test"),
    )


# ============================================================ (a) wet generalization


def test_a_descriptors_injection_maps_catalyst_and_solvent_default_regresses():
    """Injected descriptors place each catalyst level at the correct realised
    coordinate (higher descriptor -> higher realised, monotone), while the
    solvent default path (descriptors=None) stays BIT-FOR-BIT the legacy formula."""
    exp = _catalyst_wet_exp(_CATALYSTS)
    otp = compile_wet(exp, descriptors=CATALYST_DESCRIPTORS, screen_param="catalyst")

    # one well per candidate, each at exactly the injected-coordinate window value.
    realised_by_cand = {w.cand_id: w.target_polarity for w in otp.wells}
    for lv in _CATALYSTS:
        expected = _realised(CATALYST_DESCRIPTORS[lv]["coord"])
        assert realised_by_cand[f"cand_{lv}"] == pytest.approx(expected)
        # and the generic helper agrees with the compiled deck (single source).
        assert target_coord(lv, CATALYST_DESCRIPTORS) == pytest.approx(expected)

    # monotone: ascending descriptor coordinate -> ascending realised coordinate.
    coords = [CATALYST_DESCRIPTORS[lv]["coord"] for lv in _CATALYSTS]
    realised = [realised_by_cand[f"cand_{lv}"] for lv in _CATALYSTS]
    assert np.corrcoef(coords, realised)[0, 1] == pytest.approx(1.0)

    # solvent default path: descriptors=None reproduces the exact legacy mapping.
    solvents = ["water", "hexane", "acetonitrile"]
    solv_otp = compile_wet(_solvent_wet_exp(solvents))  # defaults, no new args
    solv_by_cand = {w.cand_id: w.target_polarity for w in solv_otp.wells}
    for s in solvents:
        legacy = P_TARGET_LO + SOLVENT_POLARITY[s] * (P_TARGET_HI - P_TARGET_LO)
        assert solv_by_cand[f"cand_{s}"] == pytest.approx(legacy)


# ============================================================ (b) truth face sign


def test_b_catalyst_high_face_positive_sign_and_only_mu_differs():
    """`catalyst_high` is a positive coord->response face (high-coordinate ligands
    respond higher) and obeys the only-mu-differs law across domains: amplitude/
    sigma/baseline identical to the solvent signal face, polar_high bit-for-bit."""
    cat = sim_reader.TruthSurface.from_profile("catalyst_high")
    polar = sim_reader.TruthSurface.from_profile("polar_high")

    assert cat.mu == 0.85

    # sign: response rises STRICTLY monotonically with the realised catalyst
    # coordinate (all window points sit on the rising flank below mu=0.85), i.e.
    # a clean positive coord->response face; corr stays well positive (the flank
    # curvature keeps it below 1, which is expected, not a defect).
    coords = sorted(CATALYST_DESCRIPTORS[lv]["coord"] for lv in _CATALYSTS)
    resp = [cat.response(_realised(c)) for c in coords]
    assert all(b > a for a, b in zip(resp, resp[1:]))  # strictly increasing
    assert np.corrcoef(coords, resp)[0, 1] > 0.85  # clearly positive sign
    assert cat.response(_realised(max(coords))) > cat.response(_realised(min(coords)))

    # only-mu-differs (K-D law) holds ACROSS domains, and the solvent anchor is
    # untouched (bit-for-bit M16 regression).
    assert (cat.amplitude, cat.sigma, cat.baseline) == (
        polar.amplitude, polar.sigma, polar.baseline,
    )
    assert polar == sim_reader.TruthSurface()  # dataclass defaults == M16 surface
    assert sim_reader.TRUTH_PROFILES["polar_high"] == 0.55


def test_b2_catalyst_low_face_flips_sign_only_mu_differs():
    """`catalyst_low` is the M20 FLIPPED face (machine-debt item #1 cleared): response
    DECREASES strictly with the realised coordinate -- low-descriptor ligands respond
    highest, contradicting the seeded high-coord claim -- while amplitude/sigma/
    baseline stay identical (only-mu-differs law) and catalyst_high is untouched."""
    low = sim_reader.TruthSurface.from_profile("catalyst_low")
    high = sim_reader.TruthSurface.from_profile("catalyst_high")

    assert low.mu == 0.20

    coords = sorted(CATALYST_DESCRIPTORS[lv]["coord"] for lv in _CATALYSTS)
    resp = [low.response(_realised(c)) for c in coords]
    assert all(b < a for a, b in zip(resp, resp[1:]))  # strictly DECREASING
    assert np.corrcoef(coords, resp)[0, 1] < -0.85  # clearly negative sign

    # only-mu-differs across the flip pair; the positive face stays bit-for-bit.
    assert (low.amplitude, low.sigma, low.baseline) == (
        high.amplitude, high.sigma, high.baseline,
    )
    assert sim_reader.TRUTH_PROFILES["catalyst_high"] == 0.85


# ============================================================ (c) dry leg convergence


def test_c_dry_ligand_job_converges_and_lands_reactivity_proxy(tmp_path):
    """One real PySCF ligand single point through the UNCHANGED PySCFDryAdapter:
    the catalyst geometry rides in the explicit `geometry` param (from
    catalysts.catalyst_params), so `spec.solvent is None` and the adapter is
    bit-for-bit the solvent-domain adapter. It converges and lands a
    `reactivity_proxy` (dipole, Debye) value."""
    params = catalyst_params("ph3")
    exp = ExperimentObject(
        exp_id="m20_dry",
        round_id=0,
        domain="catalyst_screen",
        objective=Objective(name="reactivity", metric="reactivity_proxy"),
        design_space=DesignSpace(
            name="catalyst_screen",
            variables=[
                VariableDef(name="catalyst", kind="categorical", choices=["ph3"]),
            ],
        ),
        active_vars=["catalyst"],
        candidates=[Candidate(cand_id="cand_ph3", params=params)],
        layout=LayoutAssignment(
            rows=1, cols=1, seed=0,
            wells=[WellAssignment(well_id="A1", row=0, col=0, cand_id="cand_ph3")],
        ),
        budget=Budget(wells_total=1, rounds_total=1),
        execution_req=ExecutionReq(adapter="pyscf_dry"),
        provenance=DesignProvenance(generator="test"),
    )
    adapter = PySCFDryAdapter(jobs_root=tmp_path / "jobs", poll_interval_s=0.1)

    # the adapter builds a solvent-less spec off the explicit geometry (zero change).
    specs = adapter.build_specs(exp)
    assert len(specs) == 1 and specs[0].solvent is None and specs[0].geometry

    result = adapter.run(exp, backend=SubprocessBackend())
    assert result.failures == {}, result.failures
    assert len(result.dry_raws) == 1
    dry = result.dry_raws[0]
    assert dry.metric == "reactivity_proxy"
    assert dry.value > 0.0  # a real ligand dipole magnitude (Debye)


# ============================================================ (d) delete-the-guard-red


def test_d_missing_descriptor_level_is_loudly_rejected():
    """A candidate whose categorical level is absent from the injected descriptors
    is REFUSED loudly (never silently skipped or defaulted). This is the
    delete-the-guard reference: if target_coord fell back to a default, this would
    not raise and a mis-mapped candidate would slip onto the plate."""
    exp = _catalyst_wet_exp(["ph3"])
    # forge a candidate at a level the descriptors table does not cover.
    bad = Candidate(cand_id="cand_bogus", params=_catalyst_conditions("unobtainium"))
    exp = exp.model_copy(update={"candidates": [bad]})

    with pytest.raises(AdapterError, match="unobtainium"):
        protocol_spec_from_experiment(
            exp, descriptors=CATALYST_DESCRIPTORS, screen_param="catalyst"
        )
