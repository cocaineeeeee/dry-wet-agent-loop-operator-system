"""Parent -> children generation pool for the M25 generative-construct domain.

Given a parent design, expand it into a deterministic pool of children by applying
the five auditable operators over an explicit parameter grid. Every child is a
``ConstructDesign`` carrying its PROV ``EditProvenance`` and a dry proxy preview.
Pure and deterministic: the same (parent, grid, seed) yields a byte-identical pool.

This is the M25 "proposer" stage of the model-competition layer (docs/BIOLOGY_
PROGRAM_2026.md §3): operators PROPOSE candidates; nothing here certifies. The dry
proxy is a preview, never a wet observation.
"""

from __future__ import annotations

from expos.adapters.dry.constructs import components_for, construct_names
from expos.adapters.dry.mutation_operators import (
    RBS_LIBRARY,
    apply_operator,
    score_components,
)
from domains.generative_construct.objects import ConstructDesign, DesignLineage


def _default_grid(parent_components: dict[str, str]) -> list[tuple[str, dict[str, object]]]:
    """The default deterministic operator x param grid for one parent.

    A compact but genuinely diverse menu across all five operators:
      * promoter_swap  -- swap to each of a few catalogue promoters (spanning the
        strength range) that differ from the parent's;
      * rbs_swap       -- swap to a strong and a weak catalogue RBS;
      * codon_optimize -- optimal and rare re-encodings (whole CDS);
      * utr5_mutation  -- a couple of deterministic point mutations (by seed);
      * cds_synonymous -- a couple of deterministic synonymous substitutions.
    """
    grid: list[tuple[str, dict[str, object]]] = []
    # promoter swaps: span strong / mid / weak catalogue promoters
    for pid in ("j23100", "j23106", "j23103"):
        if RBS_LIBRARY.get(pid) is not None and components_for(pid)["promoter"] != parent_components["promoter"]:
            grid.append(("promoter_swap", {"new_promoter_id": pid}))
    # rbs swaps: strong + weak ladder elements
    for rid in ("j23100", "j23117"):
        if components_for(rid)["rbs"] != parent_components["rbs"]:
            grid.append(("rbs_swap", {"new_rbs_id": rid}))
    # codon optimize / deoptimize (whole CDS)
    grid.append(("codon_optimize", {"target": "optimal"}))
    grid.append(("codon_optimize", {"target": "rare"}))
    # UTR point mutations (deterministic positions)
    grid.append(("utr5_mutation", {"position": 3}))
    grid.append(("utr5_mutation", {"position": 7}))
    # CDS synonymous substitutions (deterministic codon indices)
    grid.append(("cds_synonymous", {"codon_index": 2}))
    grid.append(("cds_synonymous", {"codon_index": 5}))
    return grid


def generate_children(
    parent: ConstructDesign,
    grid: list[tuple[str, dict[str, object]]] | None = None,
    seed: int = 0,
) -> list[ConstructDesign]:
    """Expand ``parent`` into a deterministic pool of child designs.

    Each grid entry ``(operator, params)`` produces one child (skipping any edit
    that a ``MutationError`` rejects -- e.g. a no-op or unavailable part -- so the
    pool never contains a mis-specified design). Children are de-duplicated by
    content-addressed id and returned in stable order."""
    from expos.adapters.dry.mutation_operators import MutationError

    if grid is None:
        grid = _default_grid(parent.components)
    seen: dict[str, ConstructDesign] = {}
    for operator, params in grid:
        try:
            child_components, prov = apply_operator(
                operator, parent.design_id, parent.components, params, seed
            )
        except MutationError:
            continue  # a rejected edit simply produces no candidate
        if prov.child_id in seen:
            continue
        seen[prov.child_id] = ConstructDesign(
            design_id=prov.child_id,
            components=child_components,
            origin=operator,
            parent_id=parent.design_id,
            proxy=score_components(child_components),
            generation=parent.generation + 1,
            edit=prov,
        )
    return list(seen.values())


def seed_design(construct_id: str) -> ConstructDesign:
    """A catalogue preset as a lineage root (generation 0, origin=seed)."""
    comp = components_for(construct_id)
    return ConstructDesign(
        design_id=construct_id,
        components=comp,
        origin="seed",
        proxy=score_components(comp),
        generation=0,
    )


def expand_lineage(
    lineage: DesignLineage, parent: ConstructDesign, seed: int = 0
) -> list[ConstructDesign]:
    """Generate one generation of children off ``parent`` and append them (and the
    parent, if new) to the append-only ``lineage``. Returns the children."""
    lineage.add(parent)
    children = generate_children(parent, seed=seed)
    for child in children:
        lineage.add(child)
    return children


def fixed_pool_seed_lineage(seed: int = 0) -> DesignLineage:
    """The fixed-pool 11-construct canary: seed all 11 catalogue presets as roots.

    A stable regression anchor -- the preset pool and their dry proxies must not
    drift (checked in tests). Roots only (no children); a caller expands whichever
    root it wants."""
    lineage = DesignLineage()
    for cid in construct_names():
        lineage.add(seed_design(cid))
    return lineage
