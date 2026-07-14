"""Typed domain objects for the M25 generative-construct domain (v0.1 skeleton)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConstructDesign:
    """One construct design in a generative lineage.

    A design is defined by its {sequence, promoter, rbs, cds} components (the same shape
    the M24 dry leg consumes) plus provenance about how it was generated. ``origin`` is
    ``"seed"`` for a catalogue preset or an operator name (e.g. ``"promoter_swap"``) for
    a generated child. ``proxy`` is the deterministic design-coordinate score (NOT a wet
    truth value).
    """

    design_id: str
    components: dict[str, str]
    origin: str
    parent_id: str | None = None
    proxy: float | None = None


@dataclass
class DesignLineage:
    """Append-only parent->child lineage of construct designs (Team M25 design/lineage).

    Deliberately minimal: a node table + directed edges. The provenance a real run would
    stamp (operator fingerprint, generation index) is carried on each ``ConstructDesign``;
    this container only records the tree so a proposer can reason over what has been tried.
    """

    nodes: dict[str, ConstructDesign] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (parent_id, child_id)

    def add(self, design: ConstructDesign) -> ConstructDesign:
        self.nodes[design.design_id] = design
        if design.parent_id is not None:
            self.edges.append((design.parent_id, design.design_id))
        return design

    def children_of(self, design_id: str) -> list[ConstructDesign]:
        return [self.nodes[c] for (p, c) in self.edges if p == design_id]

    def best(self) -> ConstructDesign | None:
        scored = [d for d in self.nodes.values() if d.proxy is not None]
        return max(scored, key=lambda d: d.proxy) if scored else None
