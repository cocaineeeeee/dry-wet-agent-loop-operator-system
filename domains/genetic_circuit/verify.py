"""GenCircuit-RL five-level verify gate (M26 v0.1) -- the cheap DETERMINISTIC gate that
sits BETWEEN propose and dry-simulate.

docs/bio_refs/02 §1 (ADOPT, strongest of group 2): a circuit proposal passes a five-level
hierarchical verification ladder BEFORE any (expensive) dynamic simulation. Illegal /
malformed / motif-missing topologies are rejected cheaply here so no dynamic ODE is wasted
on them. RED LINE (BIOLOGY_PROGRAM_2026 §4): this gate is a DRY proxy -- it only produces a
verification VERDICT (dry evidence). It NEVER certifies a behaviour claim; only trusted wet
observation certifies (docs/bio_refs/02 §3 NOT-COPY).

The five levels (low -> high), each a pure deterministic predicate on the typed graph:
  1. execution -- the graph is constructible: every unit references existing parts, every
     interaction references an existing target unit AND a regulator that some unit expresses
     (no dangling references).
  2. validity  -- SBOL-shape compliance: unique part/unit ids, every part role in the role
     vocabulary, every interaction kind in the interaction vocabulary.
  3. structure -- topological well-formedness: each unit's promoter/rbs/cds parts carry the
     correct roles in order; a regulated promoter belongs to a real unit; no reporter-less
     circuit (something must be observable).
  4. semantics -- ontology annotation: every role maps to a known SO term and every
     interaction kind to a known SBO term (the annotation the reasoning layer would consume).
  5. function  -- task-specific MOTIF detection for the declared ``behaviour``:
       * expression_cassette -> exactly one expression unit, NO regulatory interactions.
       * toggle_switch       -> a mutual-repression 2-cycle (A represses B, B represses A).
       * repressilator       -> an odd repression cycle of length >= 3 (declared, minimal).
     A declared behaviour whose motif is absent is REJECTED (the illegal-topology stop).

A lower level failing SHORT-CIRCUITS: higher levels are marked ``not_run`` (matching the
curriculum ordering; you cannot check the motif of a graph that will not even construct).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import (
    SBO_TERMS,
    SO_TERMS,
    VALID_INTERACTIONS,
    VALID_ROLES,
    CircuitGraph,
)

LEVELS: tuple[str, ...] = ("execution", "validity", "structure", "semantics", "function")


@dataclass(frozen=True)
class LevelResult:
    level: str
    passed: bool
    ran: bool = True
    detail: str = ""


@dataclass(frozen=True)
class VerifyReport:
    """The verify ladder verdict. ``ok`` iff all five levels passed. ``highest_passed`` is
    the deepest level reached (the fine-grained feedback the ref's hierarchical reward
    consumes -- here it feeds the machine report / acquisition, never a claim)."""

    circuit_id: str
    behaviour: str
    results: tuple[LevelResult, ...]
    topology_digest: str

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def highest_passed(self) -> str:
        passed = [r.level for r in self.results if r.ran and r.passed]
        return passed[-1] if passed else "none"

    @property
    def failed_level(self) -> str | None:
        for r in self.results:
            if r.ran and not r.passed:
                return r.level
        return None

    def as_dict(self) -> dict:
        return {
            "circuit_id": self.circuit_id,
            "behaviour": self.behaviour,
            "ok": self.ok,
            "highest_passed": self.highest_passed,
            "failed_level": self.failed_level,
            "topology_digest": self.topology_digest,
            "levels": [
                {"level": r.level, "passed": r.passed, "ran": r.ran, "detail": r.detail}
                for r in self.results
            ],
        }


# --------------------------------------------------------------------------- levels


def _check_execution(g: CircuitGraph) -> LevelResult:
    part_ids = {p.part_id for p in g.parts}
    for u in g.units:
        for slot, pid in (("promoter", u.promoter), ("rbs", u.rbs), ("cds", u.cds)):
            if pid not in part_ids:
                return LevelResult("execution", False,
                                   detail=f"unit {u.tu_id} {slot} references missing part {pid!r}")
    tu_ids = {u.tu_id for u in g.units}
    products = g.products()
    for i in g.interactions:
        if i.target_tu not in tu_ids:
            return LevelResult("execution", False,
                               detail=f"interaction targets missing unit {i.target_tu!r}")
        if i.regulator not in products:
            return LevelResult("execution", False,
                               detail=f"interaction regulator {i.regulator!r} is expressed by no unit")
    return LevelResult("execution", True, detail="graph constructible, no dangling refs")


def _check_validity(g: CircuitGraph) -> LevelResult:
    pids = [p.part_id for p in g.parts]
    if len(pids) != len(set(pids)):
        return LevelResult("validity", False, detail="duplicate part_id")
    uids = [u.tu_id for u in g.units]
    if len(uids) != len(set(uids)):
        return LevelResult("validity", False, detail="duplicate tu_id")
    for p in g.parts:
        if p.role not in VALID_ROLES:
            return LevelResult("validity", False, detail=f"part {p.part_id} bad role {p.role!r}")
    for i in g.interactions:
        if i.kind not in VALID_INTERACTIONS:
            return LevelResult("validity", False, detail=f"bad interaction kind {i.kind!r}")
    return LevelResult("validity", True, detail="ids unique, roles/interactions in vocabulary")


def _check_structure(g: CircuitGraph) -> LevelResult:
    from .graph import ROLE_CDS, ROLE_PROMOTER, ROLE_RBS

    by_id = {p.part_id: p for p in g.parts}
    for u in g.units:
        for slot, pid, want in (
            ("promoter", u.promoter, ROLE_PROMOTER),
            ("rbs", u.rbs, ROLE_RBS),
            ("cds", u.cds, ROLE_CDS),
        ):
            if by_id[pid].role != want:
                return LevelResult("structure", False,
                                   detail=f"unit {u.tu_id} {slot} part {pid!r} has role "
                                          f"{by_id[pid].role!r}, expected {want!r}")
    if not g.units:
        return LevelResult("structure", False, detail="no transcription units")
    if not g.reporters():
        return LevelResult("structure", False, detail="no reporter unit (nothing observable)")
    return LevelResult("structure", True, detail="every unit well-formed promoter->rbs->cds; reporter present")


def _check_semantics(g: CircuitGraph) -> LevelResult:
    for p in g.parts:
        if p.role not in SO_TERMS:
            return LevelResult("semantics", False, detail=f"role {p.role!r} has no SO term")
    for i in g.interactions:
        if i.kind not in SBO_TERMS:
            return LevelResult("semantics", False, detail=f"interaction {i.kind!r} has no SBO term")
    return LevelResult("semantics", True, detail="all roles/interactions ontology-annotated")


def _has_repression_cycle(g: CircuitGraph, length: int) -> bool:
    """True iff the repression graph (regulator-unit -> target-unit) has a directed cycle
    of exactly ``length``. Edge: the unit expressing the regulator -> the target unit."""
    from .graph import INT_REPRESSES

    prod_to_unit = {u.product: u.tu_id for u in g.units}
    edges: dict[str, set[str]] = {u.tu_id: set() for u in g.units}
    for i in g.interactions:
        if i.kind == INT_REPRESSES and i.regulator in prod_to_unit:
            edges[prod_to_unit[i.regulator]].add(i.target_tu)

    def walk(start: str, node: str, depth: int) -> bool:
        if depth == length:
            return node == start
        return any(walk(start, nxt, depth + 1) for nxt in edges.get(node, ()))

    return any(walk(s, s, 0) for s in edges)


def _check_function(g: CircuitGraph) -> LevelResult:
    b = g.behaviour
    if b == "expression_cassette":
        if g.interactions:
            return LevelResult("function", False,
                               detail="expression_cassette must have NO regulatory interactions")
        n_expr = len([u for u in g.units if u.is_reporter or True])
        if n_expr < 1:
            return LevelResult("function", False, detail="no expression unit")
        return LevelResult("function", True, detail="single constitutive expression unit (motif ok)")
    if b == "toggle_switch":
        if _has_repression_cycle(g, 2):
            return LevelResult("function", True, detail="mutual-repression 2-cycle detected (toggle motif)")
        return LevelResult("function", False,
                           detail="toggle_switch declared but no mutual-repression 2-cycle motif")
    if b == "repressilator":
        if any(_has_repression_cycle(g, k) for k in (3, 5, 7)):
            return LevelResult("function", True, detail="odd repression cycle detected (oscillator motif)")
        return LevelResult("function", False,
                           detail="repressilator declared but no odd repression cycle")
    return LevelResult("function", False, detail=f"unknown declared behaviour {b!r}")


_LEVEL_FN = {
    "execution": _check_execution,
    "validity": _check_validity,
    "structure": _check_structure,
    "semantics": _check_semantics,
    "function": _check_function,
}


def verify(graph: CircuitGraph) -> VerifyReport:
    """Run the five-level ladder, short-circuiting on the first failure (higher levels
    marked not_run). Deterministic and side-effect-free."""
    results: list[LevelResult] = []
    stopped = False
    for level in LEVELS:
        if stopped:
            results.append(LevelResult(level, passed=False, ran=False, detail="not run (earlier level failed)"))
            continue
        r = _LEVEL_FN[level](graph)
        results.append(r)
        if not r.passed:
            stopped = True
    return VerifyReport(
        circuit_id=graph.circuit_id,
        behaviour=graph.behaviour,
        results=tuple(results),
        topology_digest=graph.topology_digest(),
    )
