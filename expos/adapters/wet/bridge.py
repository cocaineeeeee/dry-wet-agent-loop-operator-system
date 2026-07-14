"""Bridge from WetExecutionResult onto the expos ingestion contract.

Kept import-light: ``expos`` is imported lazily inside the functions so this
package's own tests run without the kernel on the path. When copied into
``expos/adapters/wet/`` these functions map wet readings onto the exact objects
the ingestion pipeline (``adapters/ingest.raw_to_observations``) expects.

Trust red line (base.py / axiom 6): the driver -- the OS-visible adapter -- never
receives the reader's hidden truth. Therefore ``to_execution_result`` returns
no truth payload. The reader's truth sidecar is harvested SEPARATELY by the
post-hoc scoring harness via :func:`sim_reader.harvest_truth` (non-OS path),
mirroring how ``SimulatorBase.true_optimum`` lives outside the decision modules.
"""

from __future__ import annotations

from typing import Any

from .driver import WetExecutionResult

#: The producing "engine" of a wet reading — the plate-reader simulator. Rides
#: the formal RawResult.engine provenance position (letter 060), symmetric to the
#: dry leg stamping engine="pyscf". Honest: the reader IS the measuring engine.
WET_ENGINE = "plate_reader_sim"


def to_raw_dicts(res: WetExecutionResult, metric: str, *, unit: str = "") -> list[dict[str, Any]]:
    """OS-visible raw records as plain dicts (no expos dependency).

    ``capture_index`` carries the reader sequence so downstream drift/temporal
    checks have an ordering; failed/dropout/canceled wells surface as value=None
    (never silently omitted). ``unit`` stamps the declared metric unit (M23
    Phase 4); default "" keeps legacy behaviour byte-exact.
    """
    out: list[dict[str, Any]] = []
    for r in res.readings:
        out.append({
            "well_id": r.well_id,
            "cand_id": r.cand_id,
            "control_id": r.control_id,
            "metric": metric,
            "value": r.value,                    # None for failed/dropout/canceled
            "unit": unit,
            "capture_index": r.seq or 0,
            "sample_id": r.sample_id,            # custody key travels with the raw
            "status": r.status,
        })
    return out


def to_execution_result(res: WetExecutionResult, exp: Any, *, unit: str = ""):
    """Map onto ``expos.adapters.base.ExecutionResult`` carrying no truth payload.

    ``exp`` is an ``ExperimentObject``; the metric is taken from
    ``exp.objective.metric`` so ``raw_to_observations`` accepts it.

    ``unit`` (M23 Phase 4, letter 125 handoff): the DECLARED metric unit stamped
    onto every reading. A unit-declaring domain's ingest gate (T4) loudly rejects
    unit-less observations on the physical path — that guard is correct; this is
    the one place the real unit gets stamped. Default "" = legacy byte-exact.
    """
    from expos.adapters.base import ExecutionResult, RawResult  # lazy

    metric = exp.objective.metric
    raws: list[RawResult] = []
    for r in res.readings:
        raws.append(RawResult(
            well_id=r.well_id,
            cand_id=r.cand_id,
            control_id=r.control_id,
            metric=metric,
            value=r.value,
            unit=unit,
            capture_index=r.seq or 0,
            # Provenance three-tuple (letter 060): a wet reading arrives over the
            # socket with NO on-disk product, so uri/sha are honestly None — the
            # reader's raw_record_id is a logical id, not a file, and fabricating
            # a uri would be a provenance lie. The engine IS known (the reader).
            uri=None,
            sha256=None,
            engine=WET_ENGINE,
        ))
    # truth stays in the reader; the adapter path carries none (fairness).
    # truth stays in the reader; the adapter path carries none (fairness).
    # truth_records intentionally omitted (defaults to None) -- this module is
    # truth-free; harvesting lives in sim_reader.harvest_truth (EXP004 producer role).
    return ExecutionResult(raw_results=raws)


