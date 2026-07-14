"""M16 W6 provenance three-tuple (letter 051): uri / sha256 / engine survive
ingestion into ObservationObject.raw_ref + InstrumentMeta.

Discriminative test: a RawResult carrying provenance no longer has it dropped by
raw_to_observations (the historical loss point — RawDataRef(kind=) hardcode).
"""

from __future__ import annotations

from expos.adapters.base import RawResult
from expos.adapters.ingest import raw_to_observations
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


def _exp() -> ExperimentObject:
    return ExperimentObject(
        exp_id="exp_prov",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="polarity", metric="polarity_proxy"),
        design_space=DesignSpace(
            name="solvents",
            variables=[VariableDef(name="solvent", kind="categorical",
                                   choices=["water", "hexane"])],
        ),
        candidates=[Candidate(cand_id="cand_water", params={"solvent": "water"})],
        layout=LayoutAssignment(
            rows=1, cols=1, seed=0,
            wells=[WellAssignment(well_id="A1", row=0, col=0, cand_id="cand_water")],
        ),
        budget=Budget(wells_total=1, rounds_total=1),
        execution_req=ExecutionReq(adapter="pyscf_dry"),
        provenance=DesignProvenance(generator="test"),
    )


def test_provenance_survives_ingestion():
    exp = _exp()
    raw = RawResult(
        well_id="A1", cand_id="cand_water", metric="polarity_proxy", value=1.85,
        uri="file:///jobs/A1/result.json",
        sha256="deadbeef" * 8,
        engine="pyscf",
    )
    (obs,) = raw_to_observations(exp, [raw], raw_kind="dry")

    # uri + sha256 land on raw_ref (previously dropped by RawDataRef(kind=)).
    assert obs.raw_ref.uri == "file:///jobs/A1/result.json"
    assert obs.raw_ref.sha256 == "deadbeef" * 8
    assert obs.raw_ref.kind == "dry"
    # engine lands on instrument_meta (previously homeless).
    assert obs.instrument_meta.engine == "pyscf"


def test_provenance_absent_defaults_are_clean():
    """Legacy sim path (no provenance) still ingests: uri="", sha/engine None."""
    exp = _exp()
    raw = RawResult(well_id="A1", cand_id="cand_water", metric="polarity_proxy",
                    value=1.85)
    (obs,) = raw_to_observations(exp, [raw], raw_kind="sim")
    assert obs.raw_ref.uri == ""
    assert obs.raw_ref.sha256 is None
    assert obs.instrument_meta.engine is None
