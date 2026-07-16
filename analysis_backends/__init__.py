"""M28 ``analysis_backends`` package — analysis that produces EVIDENCE, never claims (v0.1).

This is a LEAF package: it depends only on stdlib. Crucially it does NOT import the claim
ledger (``expos.kernel.claims``). That is structural, not incidental: an analysis backend
must be *incapable* of mutating a claim — its whole output type is
:class:`EvidenceObservation` (a statistic + provenance note), never a ``ClaimDelta`` and
never a ``Ledger``. Turning evidence into a proposed ledger mutation is done ONLY by
``agents.biology_discovery.ledger_bridge`` (the single crossing point), and mutating the
ledger is done ONLY by the kernel gate ``apply_claim_deltas``. This is the moat
(docs/bio_refs/04 §5): agents/LLM produce observation/evidence; the ledger certifies.

Contents:
  * :mod:`analysis_backends.objects` — :class:`EvidenceObservation`, :class:`AssayDataset`.
  * :mod:`analysis_backends.base`    — the :class:`AnalysisBackend` protocol + strength band.
  * :mod:`analysis_backends.deterministic` — a deterministic analysis backend + a labelled
    synthetic (retrospective/simulation) assay-dataset generator.
"""

from analysis_backends.base import AnalysisBackend, evidence_strength_band
from analysis_backends.deterministic import (
    DeterministicAnalysisBackend,
    make_assay_dataset,
)
from analysis_backends.objects import AssayDataset, EvidenceObservation, ReplicateMeasurement

__all__ = [
    "AnalysisBackend",
    "evidence_strength_band",
    "DeterministicAnalysisBackend",
    "make_assay_dataset",
    "AssayDataset",
    "EvidenceObservation",
    "ReplicateMeasurement",
]
