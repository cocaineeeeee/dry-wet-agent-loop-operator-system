"""Typed genetic-circuit graph (M26 v0.1) -- SBOL-compatible FORM, canonical-hash identity.

This is M26's analogue of the chemistry Z-matrix / the M24 construct sequence: the dry
input of a circuit candidate is its TYPED GRAPH. We borrow the SBOL v3 data SHAPE (typed
parts with ontology roles, transcription units, regulatory interactions) but DELIBERATELY
do NOT pull in an RDF / Semantic-Web / pysbol3 runtime (BIOLOGY_PROGRAM_2026 §1.5 暂禁:
"完整 SBOL/RDF runtime"; docs/bio_refs/02 NOT-COPY: SBOL only lives in domain/adapter, never
in kernel). The graph is a frozen dataclass tree serialisable to a plain dict.

TWO-LAYER IDENTITY (docs/bio_refs/02 §4.D architecture finding): a circuit carries
  * ``topology_digest()``  -- canonical hash of the WIRING ONLY (part roles, unit
                              structure, interaction graph). Kinetic parameters EXCLUDED.
                              This is the ``topology identity`` -- "same topology, different
                              parts / kinetics" collapses to one topology.
  * ``parameter_digest()`` -- canonical hash of topology + kinetic parameters. This is the
                              ``parameter identity`` -- distinguishes two kinetic tunings of
                              one topology (the layer only trusted wet observation certifies).
so "same topology different parts" (the GenCircuit-RL OOD axis) and "same parts different
topology" are distinguishable at the identity layer, exactly as the ref prescribes.

HONEST-BIASED PROXY note (identical semantics to the M24 construct leg): part sequences
here are PUBLIC design knowledge (iGEM/Anderson elements), never truth. The true dynamic
phenotype is NOT in the graph -- it is produced by the (dry) simulation proxy and, on the
wet side, by the hidden dynamic truth surface (timeseries_reader). A typed graph leaks no
truth.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Mapping

# --------------------------------------------------------------------------- vocab
# SBOL / Sequence-Ontology role analogues (borrowed FORM; string literals, no RDF).
ROLE_PROMOTER = "promoter"
ROLE_RBS = "ribosome_entry_site"
ROLE_CDS = "cds"
ROLE_TERMINATOR = "terminator"
VALID_ROLES: frozenset[str] = frozenset(
    {ROLE_PROMOTER, ROLE_RBS, ROLE_CDS, ROLE_TERMINATOR}
)

#: Sequence-Ontology term analogues per role (the "semantics" verify level checks these).
SO_TERMS: Mapping[str, str] = {
    ROLE_PROMOTER: "SO:0000167",
    ROLE_RBS: "SO:0000139",
    ROLE_CDS: "SO:0000316",
    ROLE_TERMINATOR: "SO:0000141",
}

# SBO interaction-kind analogues (regulator species -> target unit's promoter).
INT_REPRESSES = "represses"
INT_ACTIVATES = "activates"
VALID_INTERACTIONS: frozenset[str] = frozenset({INT_REPRESSES, INT_ACTIVATES})

#: SBO term analogues per interaction kind (the "semantics" verify level checks these).
SBO_TERMS: Mapping[str, str] = {
    INT_REPRESSES: "SBO:0000169",   # inhibition
    INT_ACTIVATES: "SBO:0000170",   # stimulation
}


# --------------------------------------------------------------------------- nodes


@dataclass(frozen=True)
class Part:
    """One typed genetic part (SBOL ``Feature``/``SubComponent`` analogue).

    ``sequence`` is optional PUBLIC design knowledge (never truth); it does not enter
    the topology digest (wiring identity is role/structure, not the exact base string)."""

    part_id: str
    role: str
    sequence: str = ""


@dataclass(frozen=True)
class Kinetics:
    """Per-transcription-unit kinetic parameters -- the PARAMETER-identity layer.

    Dimensionless Hill production/degradation form (Gardner-toggle style):
        d[product]/dt = basal + beta * prod(regulation Hill terms) - gamma * [product]
    A repression term is ``1 / (1 + (regulator/K)^n)``; an activation term is
    ``(regulator/K)^n / (1 + (regulator/K)^n)``. These are the kinetic knobs the
    "next-round parameter revision" (DoD #7) turns; EXCLUDED from ``topology_digest``."""

    beta: float = 40.0    # max regulated production rate
    basal: float = 1.0    # leak / basal production
    K: float = 1.0        # half-repression threshold (same units as product level)
    n: float = 2.0        # Hill coefficient (cooperativity; >1 needed for toggle bistability)
    gamma: float = 1.0    # first-order degradation/dilution rate


@dataclass(frozen=True)
class TranscriptionUnit:
    """An ordered promoter->rbs->cds unit that EXPRESSES one product species.

    ``product`` is the regulator/reporter protein species name that other units'
    interactions reference. ``is_reporter`` marks the observable output species (the one
    the time-series phenotype is read off)."""

    tu_id: str
    promoter: str          # part_id (role must be promoter)
    rbs: str               # part_id (role must be ribosome_entry_site)
    cds: str               # part_id (role must be cds)
    product: str           # species name this unit expresses
    kinetics: Kinetics = field(default_factory=Kinetics)
    is_reporter: bool = False


@dataclass(frozen=True)
class Interaction:
    """A regulatory edge: ``regulator`` species represses/activates ``target_tu``'s promoter."""

    kind: str              # one of VALID_INTERACTIONS
    regulator: str         # a product species name
    target_tu: str         # tu_id whose promoter is regulated


# --------------------------------------------------------------------------- graph


@dataclass(frozen=True)
class CircuitGraph:
    """A typed genetic circuit: parts + transcription units + regulatory interactions.

    Immutable (frozen). Revision (DoD #7) is done functionally via :meth:`with_kinetics`
    / :meth:`swap_part`, each returning a NEW graph with a NEW parameter/topology digest.
    ``behaviour`` names the DESIRED behaviour class ('expression_cassette' | 'toggle_switch'
    | ...) the function-level verify gate checks the motif for -- it is a design intent
    label, not a truth."""

    circuit_id: str
    parts: tuple[Part, ...]
    units: tuple[TranscriptionUnit, ...]
    interactions: tuple[Interaction, ...] = ()
    behaviour: str = "expression_cassette"

    # -- lookups -------------------------------------------------------------
    def part(self, part_id: str) -> Part | None:
        return next((p for p in self.parts if p.part_id == part_id), None)

    def unit(self, tu_id: str) -> TranscriptionUnit | None:
        return next((u for u in self.units if u.tu_id == tu_id), None)

    def products(self) -> frozenset[str]:
        return frozenset(u.product for u in self.units)

    def reporters(self) -> tuple[TranscriptionUnit, ...]:
        return tuple(u for u in self.units if u.is_reporter)

    # -- canonical identity --------------------------------------------------
    def _topology_view(self) -> dict:
        """Wiring-ONLY canonical view: part roles (sorted), unit structure by role
        (kinetics EXCLUDED), interaction graph (sorted). Part ids are kept because they
        anchor unit/interaction references, but part SEQUENCES are dropped (topology is
        structure, not the exact base string)."""
        return {
            "parts": [
                {"part_id": p.part_id, "role": p.role}
                for p in sorted(self.parts, key=lambda p: p.part_id)
            ],
            "units": [
                {
                    "tu_id": u.tu_id,
                    "promoter": u.promoter,
                    "rbs": u.rbs,
                    "cds": u.cds,
                    "product": u.product,
                    "is_reporter": u.is_reporter,
                }
                for u in sorted(self.units, key=lambda u: u.tu_id)
            ],
            "interactions": [
                {"kind": i.kind, "regulator": i.regulator, "target_tu": i.target_tu}
                for i in sorted(
                    self.interactions, key=lambda i: (i.regulator, i.target_tu, i.kind)
                )
            ],
            "behaviour": self.behaviour,
        }

    def _parameter_view(self) -> dict:
        """Topology view + per-unit kinetics (the parameter-identity layer)."""
        view = self._topology_view()
        view["kinetics"] = {
            u.tu_id: asdict(u.kinetics)
            for u in sorted(self.units, key=lambda u: u.tu_id)
        }
        return view

    def topology_digest(self) -> str:
        """sha256 of the wiring-only canonical view. Two graphs with identical topology
        but different kinetics/part-sequences share this digest (topology identity)."""
        blob = json.dumps(self._topology_view(), sort_keys=True, separators=(",", ":"))
        return "topo:sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def parameter_digest(self) -> str:
        """sha256 of topology + kinetics -- the full parameter identity (distinguishes
        two kinetic tunings of one topology; only trusted wet obs certifies this layer)."""
        blob = json.dumps(self._parameter_view(), sort_keys=True, separators=(",", ":"))
        return "param:sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # -- serialisation (rides in candidate.params; NO RDF) -------------------
    def to_payload(self) -> dict:
        """Plain-dict serialisation for ``candidate.params`` (the M20 zero-kernel-change
        contract). Round-trips through :func:`circuit_from_payload`."""
        return {
            "circuit_id": self.circuit_id,
            "behaviour": self.behaviour,
            "parts": [asdict(p) for p in self.parts],
            "units": [
                {
                    "tu_id": u.tu_id, "promoter": u.promoter, "rbs": u.rbs, "cds": u.cds,
                    "product": u.product, "is_reporter": u.is_reporter,
                    "kinetics": asdict(u.kinetics),
                }
                for u in self.units
            ],
            "interactions": [asdict(i) for i in self.interactions],
            "topology_digest": self.topology_digest(),
            "parameter_digest": self.parameter_digest(),
        }

    # -- functional revision (DoD #7: knowledge changes -> next-round redesign) ---
    def with_kinetics(self, tu_id: str, **changes: float) -> "CircuitGraph":
        """Return a NEW graph with ``tu_id``'s kinetics updated (parameter revision).
        Same topology_digest, NEW parameter_digest. Loud on unknown tu_id."""
        if self.unit(tu_id) is None:
            raise KeyError(f"unknown transcription unit {tu_id!r}")
        new_units = tuple(
            replace(u, kinetics=replace(u.kinetics, **changes)) if u.tu_id == tu_id else u
            for u in self.units
        )
        return replace(self, units=new_units)

    def swap_part(self, tu_id: str, slot: str, new_part_id: str) -> "CircuitGraph":
        """Return a NEW graph with ``tu_id``'s ``slot`` (promoter|rbs|cds) pointed at
        ``new_part_id`` (topology revision -- e.g. swap in a stronger promoter part).
        The new part must already exist in ``parts``. Loud on unknown unit/slot/part."""
        if slot not in ("promoter", "rbs", "cds"):
            raise ValueError(f"slot must be promoter|rbs|cds, got {slot!r}")
        if self.part(new_part_id) is None:
            raise KeyError(f"part {new_part_id!r} not in graph.parts")
        u = self.unit(tu_id)
        if u is None:
            raise KeyError(f"unknown transcription unit {tu_id!r}")
        new_units = tuple(
            replace(x, **{slot: new_part_id}) if x.tu_id == tu_id else x for x in self.units
        )
        return replace(self, units=new_units)


def circuit_from_payload(payload: Mapping[str, object]) -> CircuitGraph:
    """Rebuild a :class:`CircuitGraph` from :meth:`CircuitGraph.to_payload` output (the
    round-trip the dry adapter uses to read ``candidate.params``). Loud on a missing key."""
    try:
        parts = tuple(
            Part(part_id=p["part_id"], role=p["role"], sequence=p.get("sequence", ""))
            for p in payload["parts"]  # type: ignore[index]
        )
        units = tuple(
            TranscriptionUnit(
                tu_id=u["tu_id"], promoter=u["promoter"], rbs=u["rbs"], cds=u["cds"],
                product=u["product"], is_reporter=bool(u.get("is_reporter", False)),
                kinetics=Kinetics(**u.get("kinetics", {})),
            )
            for u in payload["units"]  # type: ignore[index]
        )
        interactions = tuple(
            Interaction(kind=i["kind"], regulator=i["regulator"], target_tu=i["target_tu"])
            for i in payload.get("interactions", [])  # type: ignore[union-attr]
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed circuit payload: {exc}") from exc
    return CircuitGraph(
        circuit_id=str(payload.get("circuit_id", "circuit")),
        parts=parts,
        units=units,
        interactions=interactions,
        behaviour=str(payload.get("behaviour", "expression_cassette")),
    )
