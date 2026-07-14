"""Provider STUB for the M25 generative-construct domain (v0.1 skeleton).

Intentionally a light stub, NOT yet a full ``DomainProvider`` subclass: this pass only
needs the organ to exist, seed designs, and propose deterministic children. The seam to
the real ``expos.adapters.base.DomainProvider`` interface (design_space enumeration,
ComputeTarget emission, acceptance faces) is listed in ``docs/bio_seams/M25.md`` for the
integration owner. No kernel/ledger specialization lives here.
"""

from __future__ import annotations

from expos.adapters.dry.constructs import construct_names, components_for
from expos.adapters.dry.mutation_operators import (
    PROMOTER_LIBRARY,
    promoter_swap,
    score_components,
)
from domains.generative_construct.objects import ConstructDesign, DesignLineage


class GenerativeConstructProvider:
    """Deterministic construct-design proposer over public promoter parts."""

    name = "generative_construct"

    def seed_designs(self) -> list[ConstructDesign]:
        """Catalogue presets as lineage roots (origin=seed), proxy-scored."""
        designs = []
        for cid in construct_names():
            comp = components_for(cid)
            designs.append(
                ConstructDesign(
                    design_id=cid,
                    components=comp,
                    origin="seed",
                    proxy=score_components(comp),
                )
            )
        return designs

    def propose_children(self, parent: ConstructDesign) -> list[ConstructDesign]:
        """Deterministic promoter-swap children (one per catalogue promoter != parent)."""
        children = []
        for promoter_id in PROMOTER_LIBRARY:
            if promoter_id == parent.design_id:
                continue
            m = promoter_swap(parent.design_id, promoter_id)
            children.append(
                ConstructDesign(
                    design_id=m.child_id,
                    components=m.components,
                    origin=m.operator,
                    parent_id=parent.design_id,
                    proxy=score_components(m.components),
                )
            )
        return children

    def build_lineage(self, seed_id: str) -> DesignLineage:
        """Seed one root and expand one generation of promoter-swap children."""
        lineage = DesignLineage()
        seeds = {d.design_id: d for d in self.seed_designs()}
        root = lineage.add(seeds[seed_id])
        for child in self.propose_children(root):
            lineage.add(child)
        return lineage
