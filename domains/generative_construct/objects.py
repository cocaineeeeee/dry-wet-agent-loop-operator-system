"""Typed domain objects for the M25 generative-construct domain (v0.1 complete).

A ``ConstructDesign`` is one construct in a generative lineage, carrying its
``{sequence, promoter, rbs, cds}`` components (the shape the M24 dry leg consumes)
plus provenance about how it was generated. ``DesignLineage`` is the APPEND-ONLY
parent->child tree with a PROV-shaped activity log -- the single source of truth
for "what has been designed and how". A design NEVER reuses a parent's identity or
any observation id (M24-B technical-replicate red line: lineage is over designs).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from expos.adapters.dry.mutation_operators import EditProvenance


@dataclass(frozen=True)
class ConstructDesign:
    """One construct design in a generative lineage.

    ``origin`` is ``"seed"`` for a catalogue preset, else the operator name that
    generated it (e.g. ``"promoter_swap"``). ``proxy`` is the deterministic dry
    design-coordinate score (NOT a wet truth value). ``generation`` is the round
    index (0 = seed). ``edit`` carries the PROV EditProvenance for generated
    designs (``None`` for seeds)."""

    design_id: str
    components: dict[str, str]
    origin: str
    parent_id: str | None = None
    proxy: float | None = None
    generation: int = 0
    edit: EditProvenance | None = None


@dataclass
class DesignLineage:
    """Append-only parent->child lineage of construct designs (Team M25 design/
    lineage). PROV-shaped: ``nodes`` are entities, ``activities`` are the edits
    that generated them (append-only), ``edges`` are (parent, child).

    Append-only discipline: ``add`` refuses to overwrite an existing node with a
    DIFFERENT one (a design id is content-addressed, so a collision means the same
    design -- an idempotent no-op; a genuine rewrite is a bug and fails loudly)."""

    nodes: dict[str, ConstructDesign] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (parent_id, child_id)
    activities: list[dict[str, object]] = field(default_factory=list)  # PROV log

    def add(self, design: ConstructDesign) -> ConstructDesign:
        existing = self.nodes.get(design.design_id)
        if existing is not None:
            if existing != design:
                raise ValueError(
                    f"append-only violation: design {design.design_id!r} already in "
                    "lineage with different content (ids are content-addressed)"
                )
            return existing  # idempotent
        self.nodes[design.design_id] = design
        if design.parent_id is not None:
            self.edges.append((design.parent_id, design.design_id))
        if design.edit is not None:
            self.activities.append(design.edit.as_activity())
        return design

    def children_of(self, design_id: str) -> list[ConstructDesign]:
        return [self.nodes[c] for (p, c) in self.edges if p == design_id]

    def ancestry(self, design_id: str) -> list[str]:
        """The id chain from ``design_id`` up to its seed root (inclusive)."""
        chain = [design_id]
        cur = self.nodes.get(design_id)
        while cur is not None and cur.parent_id is not None:
            chain.append(cur.parent_id)
            cur = self.nodes.get(cur.parent_id)
        return chain

    def best(self, by: str = "proxy") -> ConstructDesign | None:
        """Best design by dry ``proxy`` (a PREVIEW ranking, not a wet verdict)."""
        scored = [d for d in self.nodes.values() if d.proxy is not None]
        return max(scored, key=lambda d: d.proxy) if scored else None
