"""End-to-end: ExperimentObject -> adapter.run -> RawResult -> ObservationObject.

Exercises the RawResult->observation shape alignment (observations enter PENDING)
and proves the adapter does not mutate the ExperimentObject.
"""

from __future__ import annotations

import pytest

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
    TrustLevel,
    VariableDef,
    WellAssignment,
)
from expos.scheduler import SubprocessBackend

from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.ingest import dry_raw_to_observations


def _make_exp() -> ExperimentObject:
    return ExperimentObject(
        exp_id="exp_test",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="polarity", metric="polarity_proxy"),
        design_space=DesignSpace(
            name="solvents",
            variables=[
                VariableDef(
                    name="solvent",
                    kind="categorical",
                    choices=["water", "hexane"],
                )
            ],
        ),
        candidates=[
            Candidate(cand_id="cand_water", params={"solvent": "water"}),
            Candidate(cand_id="cand_hexane", params={"solvent": "hexane"}),
        ],
        layout=LayoutAssignment(
            rows=1,
            cols=2,
            seed=0,
            wells=[
                WellAssignment(well_id="A1", row=0, col=0, cand_id="cand_water"),
                WellAssignment(well_id="A2", row=0, col=1, cand_id="cand_hexane"),
            ],
        ),
        budget=Budget(wells_total=2, rounds_total=1),
        execution_req=ExecutionReq(adapter="pyscf_dry"),
        provenance=DesignProvenance(generator="test"),
    )


@pytest.fixture
def adapter(tmp_path):
    return PySCFDryAdapter(jobs_root=tmp_path / "jobs", poll_interval_s=0.1)


def test_end_to_end_run_and_ingest(adapter):
    exp = _make_exp()
    before = exp.model_dump()

    result = adapter.run(exp, backend=SubprocessBackend())

    # adapter must not mutate the ExperimentObject
    assert exp.model_dump() == before

    assert result.n_jobs == 2
    assert len(result.dry_raws) == 2
    assert result.failures == {}
    # lifecycle events recorded for both jobs (accepted/spawned/terminal each)
    assert len([e for e in result.events if e.to_state == "SUCCEEDED"]) == 2

    obs, provenance = dry_raw_to_observations(exp, result.dry_raws)
    assert len(obs) == 2
    for o in obs:
        # observations always enter PENDING, unadjudicated
        assert o.trust is TrustLevel.PENDING
        assert o.qc is None and o.routing is None
        assert o.failure_attr is None and o.next_action is None
        assert o.result.metric == "polarity_proxy"
        # provenance on the FORMAL kernel positions (letter 060), not a bypass:
        # raw_ref carries uri + sha, InstrumentMeta.engine carries the engine.
        assert o.raw_ref.uri.endswith("result.json")
        assert o.raw_ref.kind == "dry"
        assert o.raw_ref.sha256
        assert o.instrument_meta.engine == "pyscf"     # formal engine position
        # sidecar keeps only the EXTRA detail and stays consistent with the
        # formal positions.
        prov = provenance[o.layout_meta.well_id]
        assert prov.engine == o.instrument_meta.engine          # sidecar == formal
        assert prov.raw_uri == o.raw_ref.uri
        assert prov.result_sha == o.raw_ref.sha256
        assert prov.basis == "sto-3g"
        assert prov.converged is True

    # water is more polar than hexane -> higher proxy value
    by_well = {o.layout_meta.well_id: o.result.value for o in obs}
    assert by_well["A1"] > by_well["A2"]


def test_projects_onto_stock_expos_ingestion(adapter):
    """DryRawResult.to_expos_raw() feeds the STOCK raw_to_observations path,
    proving shape alignment with the current expos ingestion contract AND that
    provenance survives the projection onto the formal positions (letter 060)."""
    exp = _make_exp()
    result = adapter.run(exp, backend=SubprocessBackend())

    stock_raws: list[RawResult] = [dr.to_expos_raw(i) for i, dr in enumerate(result.dry_raws)]
    # projection is no longer lossy: the formal provenance three-tuple rides along
    for raw, dr in zip(stock_raws, result.dry_raws):
        assert raw.uri == dr.raw_uri and raw.uri.endswith("result.json")
        assert raw.sha256 == dr.result_sha
        assert raw.engine == "pyscf"
    obs = raw_to_observations(exp, stock_raws, raw_kind="dry")
    assert len(obs) == 2
    assert all(o.trust is TrustLevel.PENDING for o in obs)
    # the stock path lands provenance on the same formal positions
    for o in obs:
        assert o.raw_ref.uri.endswith("result.json") and o.raw_ref.sha256
        assert o.instrument_meta.engine == "pyscf"
