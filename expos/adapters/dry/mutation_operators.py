"""Deterministic dry-leg mutation operators for the generative-construct domain (M25).

SKELETON / v0.1 SCAFFOLD (breadth-first Biology Program pass, 2026-07-14). This is the
design-move adapter for Team M25 ``generative_construct``: it takes a *parent* construct
(the {sequence, promoter, rbs, cds} component dict used everywhere else in the biology
dry leg) and applies a **pure, deterministic** design edit to yield a *child* construct.
No stochastic sampler and NO large generative model is involved (the ESM/ProtGPT/
generative-diffusion upgrade is an explicit later seam, see ``sequences.expression_
features``); a v0.1 "mutation" here is a catalogue-constrained part swap over PUBLIC
design elements, exactly the honest-biased-proxy regime the rest of the dry leg lives in.

The child sequence is rebuilt by the SAME concatenation contract the presets obey
(``sequence == promoter + rbs + cds``, machine-checked in the M24 constructs), so a
mutated construct feeds the UNCHANGED ``SequenceProxyAdapter`` / ``expression_features``
without any adapter change (zero-adapter-change contract, mirrors ``constructs.py``).

Reuses (never re-implements): ``constructs.CONSTRUCTS`` for the public promoter catalogue
and ``sequences.expression_features`` for the design-coordinate proxy score.
"""

from __future__ import annotations

from dataclasses import dataclass

from expos.adapters.dry.constructs import CONSTRUCTS, components_for
from expos.adapters.dry.sequences import expression_features

# ---------------------------------------------------------------------------
# Public promoter catalogue -- derived deterministically from the M24 presets.
# Each preset construct contributes its promoter element; this is the (public,
# design-knowledge) part library a promoter-swap mutation draws from. No truth here.
# ---------------------------------------------------------------------------

PROMOTER_LIBRARY: dict[str, str] = {
    cid: comp["promoter"] for cid, comp in CONSTRUCTS.items()
}


@dataclass(frozen=True)
class MutatedConstruct:
    """A child construct produced by a deterministic design edit.

    ``components`` obeys the ``sequence == promoter + rbs + cds`` contract so it drops
    straight into the unchanged sequence adapter. ``operator`` / ``parent_id`` /
    ``detail`` are provenance for the lineage graph (Team M25 ``design/lineage``).
    """

    child_id: str
    parent_id: str
    operator: str
    detail: str
    components: dict[str, str]


def promoter_swap(parent_id: str, new_promoter_id: str) -> MutatedConstruct:
    """Swap the promoter of ``parent_id`` for the catalogue promoter ``new_promoter_id``.

    Pure and deterministic: same (parent, promoter) -> byte-identical child. Keeps the
    parent's rbs + cds fixed and rebuilds ``sequence`` by the concatenation contract.
    """
    parent = components_for(parent_id)
    if new_promoter_id not in PROMOTER_LIBRARY:
        raise KeyError(
            f"unknown promoter {new_promoter_id!r}; catalogue: {sorted(PROMOTER_LIBRARY)}"
        )
    new_promoter = PROMOTER_LIBRARY[new_promoter_id]
    child_components = {
        "promoter": new_promoter,
        "rbs": parent["rbs"],
        "cds": parent["cds"],
        "sequence": new_promoter + parent["rbs"] + parent["cds"],
    }
    return MutatedConstruct(
        child_id=f"{parent_id}::promoter<-{new_promoter_id}",
        parent_id=parent_id,
        operator="promoter_swap",
        detail=f"promoter {parent_id}->{new_promoter_id}",
        components=child_components,
    )


def score_components(components: dict[str, str]) -> float:
    """Deterministic ``expression_proxy`` for a (mutated or preset) construct's
    components. Design-coordinate proxy only -- NOT a truth/wet channel (see
    ``sequences.expression_features``)."""
    return expression_features(
        sequence=components["sequence"],
        promoter=components.get("promoter"),
        rbs=components.get("rbs"),
        cds=components.get("cds"),
    ).expression_proxy
