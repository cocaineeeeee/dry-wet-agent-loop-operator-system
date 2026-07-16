"""Analysis backend protocol + the evidence-strength band rule (v0.1).

The band function maps a statistic to an ORDINAL strength band name (``none < weak <
moderate < strong < very_strong``) — the same ordered vocabulary the kernel's
strength-monotonicity gate reads (``expos.kernel.claims.EvidenceStrength``), but expressed
as a plain string so this leaf package never imports the kernel. The bridge translates the
string to the kernel enum at the single crossing point.

The banding rule encodes the replication red line directly: evidence backed only by
technical replicates (n_biological < required) is capped at ``weak`` no matter how large
|z| is — technical replicates never masquerade as independent biological evidence
(charter §4).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from analysis_backends.objects import AssayDataset, EvidenceObservation
from hypotheses.objects import Hypothesis


#: ordered band names — the shared, kernel-mirroring vocabulary (strings, no kernel import).
STRENGTH_BANDS = ("none", "weak", "moderate", "strong", "very_strong")


def evidence_strength_band(
    *,
    trusted: bool,
    abs_z: float,
    n_biological_replicates: int,
    min_biological_replicates: int = 2,
    decisive_abs_z: float = 2.0,
) -> str:
    """Deterministic map from a statistic to an ordinal evidence-strength band.

    Untrusted or under-powered (|z| below decisive) evidence is ``none`` (→ the ledger
    will make it INSUFFICIENT). Evidence lacking the required INDEPENDENT biological
    replicates is capped at ``weak`` (technical-only evidence cannot buy strong support).
    Otherwise the band rises with |z|. Pure function, no randomness."""
    if not trusted or abs_z < decisive_abs_z:
        return "none"
    if n_biological_replicates < min_biological_replicates:
        # decisive z but no independent biological backing → capped weak (red line).
        return "weak"
    if abs_z >= 6.0 and n_biological_replicates >= 3:
        return "very_strong"
    if abs_z >= 4.0:
        return "strong"
    return "moderate"


@runtime_checkable
class AnalysisBackend(Protocol):
    """An analysis backend consumes a hypothesis' plan + an assay dataset and produces an
    :class:`EvidenceObservation` — a statistic + provenance, NEVER a claim. It has no ledger
    handle. Default implementations must be deterministic (charter DoD #10)."""

    def analyse(self, hypothesis: Hypothesis, dataset: AssayDataset) -> EvidenceObservation: ...
