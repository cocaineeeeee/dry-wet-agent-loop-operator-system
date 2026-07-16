"""Provider for the M25 generative-construct domain (v0.1 complete).

Deterministic construct-design proposer over public genetic parts. It seeds
catalogue presets, expands parents into a five-operator child pool, previews dry
proxies, and runs diversity-aware acquisition. NOT yet a full ``DomainProvider``
subclass -- the seam to ``expos.adapters.base.DomainProvider`` (design_space
enumeration, ComputeTarget emission, mcl candidate-source hook) is listed in
``docs/bio_seams/M25.md`` for the integration owner. No kernel/ledger
specialization lives here; the wet truth surface lives in ``wet_truth`` (the wet
side), never imported by the dry proposer path.
"""

from __future__ import annotations

from expos.adapters.dry.constructs import (
    CONSTRUCT_DESCRIPTORS,
    components_for,
    construct_names,
)
from expos.adapters.dry.mutation_operators import apply_operator, score_components
from domains.generative_construct.acquisition import Selection, select
from domains.generative_construct.generation import (
    generate_children,
    seed_design,
)
from domains.generative_construct.objects import ConstructDesign, DesignLineage


class GenerativeConstructProvider:
    """Deterministic construct-design proposer over public genetic parts."""

    name = "generative_construct"
    generator_id = "generative_construct/v0.1"

    # -- seeding --------------------------------------------------------------

    def seed_designs(self) -> list[ConstructDesign]:
        """The fixed-pool 11 catalogue presets as lineage roots (proxy-scored)."""
        return [seed_design(cid) for cid in construct_names()]

    #: The wet screen's measurable-window high edge on the DESIGN coordinate (the
    #: published promoter strength). Presets above it realise past the truth-face
    #: peak (mu=0.85) where the response turns over -- a design-side screening
    #: choice, NOT a truth leak (the value is the public catalogue coordinate).
    WINDOW_HIGH_COORD = 0.75

    def aligned_pool(self) -> list[ConstructDesign]:
        """Presets that realise WITHIN the wet measurable window (design coord <=
        ``WINDOW_HIGH_COORD``). On the positive face the surface is monotone-rising
        across this window, so the dry proxy (which correlates with the design
        coordinate) predicts the wet ranking -- the ALIGNED/positive acceptance
        face where the M25 claim is SUPPORTED."""
        return [
            seed_design(cid)
            for cid in construct_names()
            if CONSTRUCT_DESCRIPTORS[cid]["coord"] <= self.WINDOW_HIGH_COORD
        ]

    # -- proposing ------------------------------------------------------------

    def propose_children(
        self, parent: ConstructDesign, seed: int = 0
    ) -> list[ConstructDesign]:
        """A parent -> N children pool over all five auditable operators."""
        return generate_children(parent, seed=seed)

    def build_lineage(self, seed_id: str, generations: int = 1, seed: int = 0) -> DesignLineage:
        """Seed one root and expand ``generations`` of children.

        Each generation expands the current-best (by dry proxy) child -- a simple
        deterministic proxy-greedy walk (the wet-informed walk is the e2e's job)."""
        lineage = DesignLineage()
        parent = seed_design(seed_id)
        lineage.add(parent)
        for _g in range(generations):
            children = self.propose_children(parent, seed=seed)
            for c in children:
                lineage.add(c)
            best = max(children, key=lambda d: (d.proxy or 0.0, d.design_id), default=None)
            if best is None:
                break
            parent = best
        return lineage

    # -- acquisition ----------------------------------------------------------

    def acquire(
        self,
        pool: list[ConstructDesign],
        k: int,
        strategy: str = "value_diversity",
        lam: float = 0.5,
    ) -> list[Selection]:
        """Diversity-aware, observation-independent batch selection (feeds B's
        policy). Delegates to ``acquisition.select``."""
        return select(pool, k, strategy=strategy, lam=lam)

    # -- discriminative faces -------------------------------------------------

    def discriminative_pool(self, seed: int = 0) -> list[ConstructDesign]:
        """A pool engineered to contain the 'dry ranking overturned by wet' case.

        Two curated designs plus the strong/weak preset anchors:
          * ``overturn_high_dry`` -- a STRONG RBS + optimal CDS grafted onto a
            transcriptionally WEAK (but GC-rich) promoter: the dry proxy ranks it
            HIGH (good sequence features) but wet expression is LOW (the weak
            promoter barely transcribes -- a driver the dry proxy is blind to).
          * ``overturn_low_dry``  -- a strong promoter carrying a WEAK RBS + rare
            CDS: dry ranks it LOW, wet expression is comparatively HIGH.
        On the positive wet face these two INVERT the dry ranking: the dry-max of
        the pool (``overturn_high_dry``) is NOT the wet winner (``overturn_low_dry``
        beats it), so the claim "the dry-top design is the wet winner" is REJECTED
        (checked in the e2e / tests). A weak preset anchor keeps an aligned
        reference in the pool; the strong ``j23100`` preset is deliberately EXCLUDED
        so the engineered overturn design stays the strict dry-max."""
        # A: weak promoter (j23103) + j23100's ideal RBS + optimal CDS
        a_comp, a_prov = apply_operator(
            "promoter_swap", "j23100", components_for("j23100"), {"new_promoter_id": "j23103"}, seed
        )
        # B: j23100 strong promoter + weak RBS (j23117) then rare CDS
        b_rbs, _ = apply_operator(
            "rbs_swap", "j23100", components_for("j23100"), {"new_rbs_id": "j23117"}, seed
        )
        b_comp, b_prov = apply_operator(
            "codon_optimize", a_prov.parent_id, b_rbs, {"target": "rare"}, seed
        )
        pool = [
            ConstructDesign(
                design_id="overturn_high_dry",
                components=a_comp,
                origin="promoter_swap",
                parent_id="j23100",
                proxy=score_components(a_comp),
                generation=1,
                edit=a_prov,
            ),
            ConstructDesign(
                design_id="overturn_low_dry",
                components=b_comp,
                origin="codon_optimize",
                parent_id="j23100",
                proxy=score_components(b_comp),
                generation=1,
                edit=b_prov,
            ),
            seed_design("j23103"),  # aligned weak anchor (strong preset excluded on purpose)
        ]
        return pool
