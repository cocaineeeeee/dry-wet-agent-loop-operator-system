"""SBOL-ecosystem technique ADAPT (M26 v0.1) -- borrow the FORM of two SBOL-utilities tools,
NOT their RDF/HTTP runtime (docs/bio_refs/02 §4 NOT-COPY: SBOL lives in the domain layer, never
in the kernel; no RDF/Semantic-Web/pysbol3 dependency, no IDT API).

Two adapted utilities, both PURE, deterministic, dependency-free:

  * ``topology_diff``   ADAPTs ``sbol_utilities/sbol_diff.py`` (which uses ``rdflib.compare.
    graph_diff`` over an RDF canonical graph). We reimplement the ADDED / REMOVED / CHANGED
    idea directly over the typed :class:`CircuitGraph` -- a structural diff of parts, units,
    interactions and per-unit kinetics. This is the M26 analogue of expos's "first bifurcation
    point between two runs" and of candidate-topology comparison in a redesign step.

  * ``complexity_score`` ADAPTs ``sbol_utilities/calculate_complexity_scores.py`` (which POSTs
    sequences to the IDT gBlocks synthesis-complexity API). We reimplement a LOCAL deterministic
    manufacturability-difficulty heuristic from part composition (part count, total synthesised
    length, sequence repeats/homology, GC-balance) -- higher = harder to synthesise. It is the
    M26 ``manufacturability`` acquisition face (docs/bio_refs/02 §2). It is DRY evidence / a
    design-cost proxy; it NEVER certifies a claim.

Honesty (BIOLOGY_PROGRAM_2026 §5): both are deterministic dry infrastructure. ``complexity_score``
is a heuristic proxy, NOT the calibrated IDT score; do not present it as a validated synthesis cost.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Mapping

from .graph import CircuitGraph


# --------------------------------------------------------------------------- topology diff


def topology_diff(a: CircuitGraph, b: CircuitGraph) -> dict:
    """Structural diff of two circuit graphs (ADAPT of ``sbol_diff``'s added/removed/changed
    triples). Returns a JSON-able dict:
        ``same_topology``      -- a.topology_digest() == b.topology_digest()
        ``same_parameters``    -- a.parameter_digest() == b.parameter_digest()
        ``parts_added/removed``      -- part_ids present in only one graph
        ``units_added/removed``      -- tu_ids present in only one graph
        ``interactions_added/removed`` -- (kind, regulator, target_tu) triples in only one
        ``kinetics_changed``   -- {tu_id: {field: [old, new]}} for units in BOTH graphs
        ``first_divergence``   -- the first (coarsest) category that differs, or None
    The diff is symmetric-labelled from a -> b (``added`` = new in b, ``removed`` = gone from b)."""
    a_parts = {p.part_id for p in a.parts}
    b_parts = {p.part_id for p in b.parts}
    a_units = {u.tu_id for u in a.units}
    b_units = {u.tu_id for u in b.units}
    a_int = {(i.kind, i.regulator, i.target_tu) for i in a.interactions}
    b_int = {(i.kind, i.regulator, i.target_tu) for i in b.interactions}

    kin_a = {u.tu_id: asdict(u.kinetics) for u in a.units}
    kin_b = {u.tu_id: asdict(u.kinetics) for u in b.units}
    kinetics_changed: dict[str, dict[str, list[float]]] = {}
    for tu in sorted(a_units & b_units):
        changed = {
            f: [kin_a[tu][f], kin_b[tu][f]]
            for f in kin_a[tu]
            if kin_a[tu][f] != kin_b[tu][f]
        }
        if changed:
            kinetics_changed[tu] = changed

    diff = {
        "same_topology": a.topology_digest() == b.topology_digest(),
        "same_parameters": a.parameter_digest() == b.parameter_digest(),
        "parts_added": sorted(b_parts - a_parts),
        "parts_removed": sorted(a_parts - b_parts),
        "units_added": sorted(b_units - a_units),
        "units_removed": sorted(a_units - b_units),
        "interactions_added": sorted(b_int - a_int),
        "interactions_removed": sorted(a_int - b_int),
        "inputs_added": sorted(set(b.inputs) - set(a.inputs)),
        "inputs_removed": sorted(set(a.inputs) - set(b.inputs)),
        "kinetics_changed": kinetics_changed,
    }
    # first (coarsest) category that differs -- the "first bifurcation point" of the redesign.
    order = [
        ("parts", diff["parts_added"] or diff["parts_removed"]),
        ("units", diff["units_added"] or diff["units_removed"]),
        ("interactions", diff["interactions_added"] or diff["interactions_removed"]),
        ("inputs", diff["inputs_added"] or diff["inputs_removed"]),
        ("kinetics", bool(diff["kinetics_changed"])),
    ]
    diff["first_divergence"] = next((name for name, differs in order if differs), None)
    return diff


# --------------------------------------------------------------------------- complexity score


def _sequence_penalty(seq: str) -> float:
    """Per-sequence synthesis-difficulty penalty in ~[0, 1+]: rewards balanced GC and
    penalises homopolymer runs and extreme GC skew (the failure modes real gene synthesis
    complexity scores flag). Deterministic; empty sequence -> 0."""
    s = seq.lower()
    n = len(s)
    if n == 0:
        return 0.0
    gc = sum(c in "gc" for c in s) / n
    gc_skew = abs(gc - 0.5) * 2.0  # 0 (balanced) .. 1 (all-AT or all-GC)
    # longest homopolymer run.
    longest = run = 1
    for i in range(1, n):
        run = run + 1 if s[i] == s[i - 1] else 1
        longest = max(longest, run)
    homopolymer = min(longest / 8.0, 1.0)  # runs >= 8 nt are a red flag
    return 0.5 * gc_skew + 0.5 * homopolymer


def complexity_score(graph: CircuitGraph) -> float:
    """Local deterministic manufacturability-DIFFICULTY score (ADAPT of the IDT
    complexity-score FORM; higher = harder/costlier to synthesise). Combines part count,
    total synthesised sequence length, and the per-part sequence penalties. Bounded, monotone
    in circuit size. DRY evidence / a ``manufacturability`` acquisition proxy -- never a claim."""
    parts = graph.parts
    if not parts:
        return 0.0
    total_len = sum(len(p.sequence) for p in parts)
    seq_penalty = sum(_sequence_penalty(p.sequence) for p in parts) / len(parts)
    size_term = min(len(parts) / 12.0, 1.0)          # more parts -> harder assembly
    length_term = min(total_len / 2000.0, 1.0)       # more bp -> costlier synthesis
    return round(0.4 * size_term + 0.3 * length_term + 0.3 * seq_penalty, 6)


def manufacturability_ranking(graphs: Mapping[str, CircuitGraph]) -> list[tuple[str, float]]:
    """(circuit_id, complexity_score) ascending -- the acquisition-face view: cheapest /
    easiest-to-build circuits first. Deterministic tie-break by circuit_id."""
    return sorted(
        ((cid, complexity_score(g)) for cid, g in graphs.items()),
        key=lambda kv: (kv[1], kv[0]),
    )
