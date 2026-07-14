"""ProtocolSpec -- the declarative protocol object (VNext (2) provenance facet).

Q1 resolution (docs/RESEARCH_OS_VNEXT.md Part IV): Protocol enters the system as
a **provenance facet** -- its sha256 fingerprint rides in
``DesignProvenance.protocol_fingerprint`` -- and is NOT a new first-class kernel
object. Promotion to a first-class object is gated on the hard rule below.

    PROMOTION_RULE: a ProtocolSpec is promoted from provenance facet to a
    first-class kernel object only once >= 2 INDEPENDENT consumers read it as an
    authoritative object (not merely as a fingerprint). The consumer registry
    below is the trigger's bookkeeping: when it lists two independent entries the
    promotion is due. Precedent: the mechanism-activity event earned kernel
    status by exactly this ">=2 consumers" gate.

The spec is a deterministic template:

- ``inputs``           -- names the parameter space and binds the concrete
                          candidate/control values to screen this round.
- ``steps``            -- the ordered op list. M16 minimum op set is exactly
                          {``dry_compute``, ``wet_assay``}; each step is
                          ``{op, params, target}``.
- ``expected_outputs`` -- the metric names the loop expects back.
- ``metadata``         -- free-form provenance (never affects execution).

``expos.protocol.compiler.compile`` turns a spec into two build targets
(DryJobPlan / WetProtocolPlan) plus the fingerprint anchor. Validation here is
LOUD: an empty step list, an unknown op, an empty ``expected_outputs`` or an
empty candidate set each raise rather than silently degrade.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The M16 minimum op set. dry_compute -> a PySCF single-point job (W3);
#: wet_assay -> an Opentrons liquid-handling + plate-read protocol (W4).
ALLOWED_OPS = frozenset({"dry_compute", "wet_assay"})

#: Q1 promotion-rule bookkeeping. Register a consumer here (name -> one-line
#: role) the moment it starts reading a ProtocolSpec as an authoritative object.
#: When >= 2 INDEPENDENT entries are present, promotion from provenance facet to
#: first-class kernel object is DUE (see PROMOTION_RULE in the module docstring).
#: Fingerprint-only consumers (W3/W4 stamping DesignProvenance) do NOT count --
#: they consume the anchor, not the object.
PROTOCOL_CONSUMER_REGISTRY: dict[str, str] = {
    # "consumer_name": "what it reads the ProtocolSpec AS (authoritative object)",
    # -- empty at M16 W2: only the fingerprint facet exists so far.
}


def promotion_due() -> bool:
    """True once >= 2 independent authoritative consumers are registered."""
    return len(PROTOCOL_CONSUMER_REGISTRY) >= 2


class ProtocolModel(BaseModel):
    # extra=forbid: an unknown key is a loud error, never a silent drop.
    model_config = ConfigDict(extra="forbid")


class ProtocolStep(ProtocolModel):
    """One ordered protocol step: an op applied with params, producing target."""

    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    target: str

    @model_validator(mode="after")
    def _known_op_and_target(self) -> "ProtocolStep":
        # GUARD (unknown-op): delete this branch and a spec with a bogus op
        # compiles silently -> test_unknown_op_rejected goes red.
        if self.op not in ALLOWED_OPS:
            raise ValueError(
                f"unknown protocol op {self.op!r}; "
                f"allowed ops are {sorted(ALLOWED_OPS)}"
            )
        # GUARD (empty-target): every step must name where its output lands.
        if not self.target:
            raise ValueError(f"protocol step (op={self.op!r}) has an empty target")
        return self


class CandidateBinding(ProtocolModel):
    """A concrete candidate (or control) bound into the parameter space."""

    cand_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    is_control: bool = False
    control_id: str | None = None

    @model_validator(mode="after")
    def _control_consistency(self) -> "CandidateBinding":
        # A control MUST carry a control_id; a plain candidate MUST NOT. This
        # mirrors the kernel ObservationObject cand/control xor invariant.
        if self.is_control and not self.control_id:
            raise ValueError(f"control candidate {self.cand_id!r} needs a control_id")
        if not self.is_control and self.control_id is not None:
            raise ValueError(
                f"non-control candidate {self.cand_id!r} must not set control_id"
            )
        return self


class ProtocolInputs(ProtocolModel):
    """Parameter-space reference + the concrete bindings to screen this round."""

    space_name: str
    candidates: list[CandidateBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _nonempty(self) -> "ProtocolInputs":
        # GUARD (empty-inputs): a protocol with nothing to screen is a no-op and
        # is rejected loudly.
        if not self.candidates:
            raise ValueError(
                f"protocol inputs for space {self.space_name!r} bind no candidates"
            )
        return self


class ProtocolSpec(ProtocolModel):
    """Declarative protocol object -- the ProtocolObject of M16 W2.

    Deterministic identity: two specs with equal field content have equal
    :func:`canonical_json` regardless of dict-key insertion order, hence equal
    fingerprints (see compiler.protocol_fingerprint).
    """

    name: str
    version: str = "0"
    inputs: ProtocolInputs
    steps: list[ProtocolStep]
    expected_outputs: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _loud_shape(self) -> "ProtocolSpec":
        if not self.name:
            raise ValueError("protocol spec has an empty name")
        # GUARD (missing-steps): delete this and an empty protocol compiles to
        # two empty plans -> test_missing_steps_rejected goes red.
        if not self.steps:
            raise ValueError(f"protocol spec {self.name!r} has no steps")
        # GUARD (empty-outputs): a protocol that expects nothing back cannot be
        # adjudicated downstream -> test_empty_outputs_rejected goes red.
        if not self.expected_outputs:
            raise ValueError(
                f"protocol spec {self.name!r} has empty expected_outputs"
            )
        return self

    def ops(self) -> list[str]:
        return [s.op for s in self.steps]

    def step_for(self, op: str) -> ProtocolStep | None:
        """First step with the given op, or None. (M16: at most one of each.)"""
        for s in self.steps:
            if s.op == op:
                return s
        return None


def canonical_json(spec: ProtocolSpec | dict[str, Any]) -> str:
    """Deterministic JSON serialisation -- the fingerprint's stable substrate.

    ``sort_keys=True`` is load-bearing: it makes serialisation independent of
    dict-key insertion order so equal specs hash identically. Swapping this for
    ``str(dict)`` (insertion-order dependent) breaks determinism -- that is the
    documented kill for the determinism guard (see tests/test_protocol_w2.py).
    Compact separators strip whitespace variance; ``ensure_ascii=False`` keeps
    the byte-for-byte payload stable across locales.
    """
    data = spec.model_dump(mode="json") if isinstance(spec, ProtocolSpec) else spec
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
