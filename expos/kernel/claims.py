"""M17 K-A — online Evidence-to-Claim path: ClaimDecision + ClaimDelta schema
and the append-only claim-ledger update path.

The offline ``scripts/claim_compiler.py`` compiles a CAMPAIGN-level ledger from
pinned artifacts. M17 adds the RUN-internal, per-round ONLINE path: at round end
a certification policy (K-C) turns aggregated wet statistics (K-B) into a list of
``ClaimDelta`` — proposed, provenance-carrying ledger mutations — which
``apply_claim_deltas`` lands under three governance red lines (letters 072/063):

  1. Never bypass offline-compiler governance. Every delta's decision_fn must be
     registered in ``DECISION_FN_REGISTRY`` (the SAME registry the offline
     compiler registers into — see ``scripts/claim_compiler.py``) and carry the
     registered version, else the delta is denied loudly. Registration + its
     source fingerprint are the single membership authority for both paths.
  2. Supersede is append-only with a BIDIRECTIONAL chain (new record stores an
     immutable ``supersedes`` back-pointer; the forward ``superseded_by`` is
     DERIVED read-side by inverting it — nanopub/PROV precedent: retraction and
     revision are events, validity is a read-side derivation, never an in-place
     status mutation) AND a strength-monotonicity gate: weak evidence must not
     retract a strong conclusion. A supersede/reject is allowed only when the
     delta's ``evidence_strength`` band is >= the target head's recorded band;
     otherwise it degrades to qualified/insufficient with an explicit
     ``deny_reason``, recorded (never silent). Mirrors the ``deny_reason``
     discipline of ``expos.planner.promotion``.
  3. ``insufficient`` NEVER mutates the effective status of its target claim (gate
     K3: absence of evidence != support). It is TYPE-level isolated — an
     insufficient ClaimDelta structurally cannot carry a new claim version (a
     validator forbids it, sciunit InsufficientDataScore precedent), so it can
     only ever land a traceable annotation record.

Derived status (the core of red line 2): a claim's effective status is a PURE
function of the append-only record set — ``effective_statuses`` / ``head`` replay
the supersede chain to derive it. No record carries a mutable status the online
path rewrites in place; supersede appends a new version and the old record is left
untouched. Replaying the same delta chain reproduces every effective status
bit-for-bit.

Determinism (gate K5): ``apply_claim_deltas`` is a PURE function — no I/O, no
clock, no randomness. Deltas within a round are applied in an explicit
deterministic order (``_delta_sort_key``; the R3 P0 lesson that tie-breaks must be
explicit), so the same batch on the same start ledger is bit-for-bit reproducible.

Provenance (gates K1/K4): every landed record carries a frozen
``ProvenanceSnapshot`` shaped on the W3C PROV derivation five-tuple — a ``usage``
slot (input observation ids + content fingerprints + consumed knowledge
fingerprint), an ``activity`` slot (decision_fn id/version + run fingerprint +
criterion version) and a ``statistic`` slot (the self-sufficient statistic/power
record). A third party can recompute the adjudication from the event stream alone
(K4); the "zero-injection self-derivation" audit (K1) checks this snapshot chain.

Layering (public red line EXP007): this module lives in ``kernel/`` and imports
only stdlib + pydantic + kernel; it never imports an upper package. The
projection to compile_knowledge is a plain list of ``{claim_id, status}`` dicts
(the ledger shape ``kernel.knowledge.compile_knowledge`` already consumes).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ConfigDict, model_validator

from expos.errors import ExposError

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime coupling
    from expos.kernel.store import RunStore


#: claim_decision payload version — pv is born with the event (REF-1 governance
#: at birth); bumping it is an intended, registered schema break.
CLAIM_DECISION_PV = 1


class ClaimLedgerError(ExposError):
    """Append-only / supersede-chain invariant break in the online ledger path.

    user_facing=False: rewriting an existing claim version in place, or a
    corrupt supersede chain, is a governance-red-line violation (a bug in the
    delta producer, never a domain error) — it must surface loudly with a
    traceback, never be silently swallowed (errors.py "bug 不许静默")."""

    user_facing = False


# ---------------------------------------------------------------- enums


class ClaimDecisionStatus(str, Enum):
    """The four online certification verdicts (M17 §0 pipeline).

    ``supported`` / ``rejected`` are decisive head-mutating verdicts;
    ``qualified`` is a weaker head-mutating verdict (conditional/partial);
    ``insufficient`` is NON-mutating (K3: small-sample / low-power rounds must not
    hard-adjudicate — absence of evidence is not support)."""

    SUPPORTED = "supported"
    REJECTED = "rejected"
    QUALIFIED = "qualified"
    INSUFFICIENT = "insufficient"


#: Statuses that propose a NEW head version (a supersede/create). ``insufficient``
#: is deliberately absent — it never mutates the effective status (red line 3).
MUTATING_STATUSES = frozenset(
    {
        ClaimDecisionStatus.SUPPORTED,
        ClaimDecisionStatus.REJECTED,
        ClaimDecisionStatus.QUALIFIED,
    }
)


class EvidenceStrength(str, Enum):
    """Ordinal evidence-strength BAND for the strength-monotonicity gate.

    The ORDER (none < weak < moderate < strong < very_strong) is the load-bearing
    part: a delta may only supersede/reject a head whose recorded band is <= the
    delta's band. Bands are the discrete Jeffreys-style projection of the
    continuous ``evidence_factor`` (a Bayes-factor-type quantity, cross-repo
    convergence S-C); the continuous value rides in the statistic snapshot as
    provenance-only side-info. The concrete grading (which BF/power maps to which
    band) is the K-B power-table's job; K-A only fixes the ordered vocabulary and
    the gate that reads it."""

    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


_STRENGTH_RANK: dict[EvidenceStrength, int] = {
    EvidenceStrength.NONE: 0,
    EvidenceStrength.WEAK: 1,
    EvidenceStrength.MODERATE: 2,
    EvidenceStrength.STRONG: 3,
    EvidenceStrength.VERY_STRONG: 4,
}


def strength_rank(strength: EvidenceStrength) -> int:
    """Ordinal rank of an evidence-strength band (higher = stronger)."""
    return _STRENGTH_RANK[strength]


# ---- deny_reason enumeration (mirrors planner.promotion deny discipline) -----
# A denied/degraded delta always carries exactly one of these. No silent edge:
# every gate outcome is a legible, recorded reason.
DENY_UNREGISTERED_DECISION_FN = "unregistered_decision_fn"
DENY_DECISION_FN_VERSION_MISMATCH = "decision_fn_version_mismatch"
DENY_WEAK_CANNOT_RETRACT_STRONG = "weak_cannot_retract_strong"
DENY_APPEND_ONLY_VIOLATION = "append_only_violation"

DENY_REASONS: frozenset[str] = frozenset(
    {
        DENY_UNREGISTERED_DECISION_FN,
        DENY_DECISION_FN_VERSION_MISMATCH,
        DENY_WEAK_CANNOT_RETRACT_STRONG,
        DENY_APPEND_ONLY_VIOLATION,
    }
)


# ---------------------------------------------------------------- decision_fn registry


@dataclass(frozen=True)
class RegisteredDecisionFn:
    """One registered decision function: its stable id, version and a source
    fingerprint (sha256 over the callable source). Governance reads id + version;
    the fingerprint is the tamper-evidence audit hook. ``fn`` is retained so the
    online certification policy (K-C) can dispatch by id — the ONLINE gate itself
    only checks membership + version, never runs ``fn``."""

    fn_id: str
    version: str
    source_fingerprint: str
    fn: Callable[..., Any]


#: The SINGLE membership authority for legal decision functions, shared by the
#: offline compiler (scripts/claim_compiler.py registers ``paired_significance_
#: verdict`` here) and the online path. "Not bypassing offline governance" means:
#: an online delta's decision_fn must live in THIS dict with a matching version.
DECISION_FN_REGISTRY: dict[str, RegisteredDecisionFn] = {}


def _source_fingerprint(fn: Callable[..., Any]) -> str:
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        # C-extensions / dynamically built callables have no source — fall back
        # to the qualified name so an entry still carries a stable fingerprint.
        src = fn.__qualname__
    return "sha256:" + hashlib.sha256(src.encode("utf-8")).hexdigest()


def register_decision_fn(
    fn_id: str, version: str
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a decision function into the shared ``DECISION_FN_REGISTRY``.

    Decorator form, used by BOTH the offline compiler and the online path:

        @register_decision_fn("my_verdict", "1")
        def my_verdict(...): ...

    Re-registering the same id overwrites (module import is idempotent). The
    version string is the governance version a ``ClaimDelta`` must declare."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        DECISION_FN_REGISTRY[fn_id] = RegisteredDecisionFn(
            fn_id=fn_id,
            version=version,
            source_fingerprint=_source_fingerprint(fn),
            fn=fn,
        )
        return fn

    return deco


def decision_fn_deny_reason(fn_id: str, version: str) -> str | None:
    """The decision_fn legality gate (governance red line 1). Returns the
    ``deny_reason`` to record if the delta's decision_fn is not registered or its
    declared version does not match the registered one; ``None`` if legal."""
    reg = DECISION_FN_REGISTRY.get(fn_id)
    if reg is None:
        return DENY_UNREGISTERED_DECISION_FN
    if reg.version != version:
        return DENY_DECISION_FN_VERSION_MISMATCH
    return None


#: Reference / default online certification decision fn (K-A). Honest default:
#: with no aggregator wired it certifies INSUFFICIENT (absence of evidence is not
#: support — gate K3). K-B registers the real statistical decision fn under its
#: own id + version; this only proves the registry is exercisable and gives K-C /
#: K-E a stable id to build ClaimDeltas against.
REFERENCE_CERTIFICATION_FN_ID = "reference_round_certification"
REFERENCE_CERTIFICATION_FN_VERSION = "1"


@register_decision_fn(REFERENCE_CERTIFICATION_FN_ID, REFERENCE_CERTIFICATION_FN_VERSION)
def reference_round_certification(
    *, statistic: dict[str, Any], power: dict[str, Any], criterion_version: str
) -> ClaimDecisionStatus:
    """Reference online certification decision fn (see the module-level note).

    Pure, deterministic, honest-null: returns INSUFFICIENT unconditionally. The
    real per-round statistical verdict (supported/rejected/qualified from a
    permutation test + evidence-factor band) is K-B's contribution under a new
    id."""
    return ClaimDecisionStatus.INSUFFICIENT


# ---------------------------------------------------------------- declarative gate

#: Version of the declarative legality rule table. Bumping it (a real gate change)
#: re-fingerprints ``GATE_RULES_FINGERPRINT``, so changing the gate is itself
#: auditable — the version + fingerprint ride into the claim_decision event and
#: are declarable on ``ProvenanceActivity``.
GATE_RULES_VERSION = "1"

GATE_DISPOSITION_REJECT = "reject"  # delta denied outright, no record lands
GATE_DISPOSITION_DEGRADE = "degrade"  # delta lands only as a downgraded annotation


@dataclass(frozen=True)
class GateViolation:
    """One legality-rule violation — machine-readable, stably coded."""

    code: str  # one of DENY_* (stable violation code)
    disposition: str  # GATE_DISPOSITION_REJECT | GATE_DISPOSITION_DEGRADE
    detail: str


@dataclass(frozen=True)
class GateResult:
    """The declarative gate verdict: ``conforms`` + the full violation report
    (pySHACL conforms/results shape). ``first`` surfaces the highest-precedence
    violation of a disposition the apply path acts on."""

    conforms: bool
    violations: tuple[GateViolation, ...]

    def first(self, disposition: str) -> GateViolation | None:
        for v in self.violations:
            if v.disposition == disposition:
                return v
        return None


# --- rule predicates: (delta, head) -> detail str if violated else None --------
def _rule_decision_fn_registered(
    delta: "ClaimDelta", head: "ClaimRecord | None"
) -> str | None:
    if DECISION_FN_REGISTRY.get(delta.decision_fn_id) is None:
        return f"decision_fn {delta.decision_fn_id!r} is not registered"
    return None


def _rule_decision_fn_version(
    delta: "ClaimDelta", head: "ClaimRecord | None"
) -> str | None:
    reg = DECISION_FN_REGISTRY.get(delta.decision_fn_id)
    if reg is not None and reg.version != delta.decision_fn_version:
        return (
            f"decision_fn {delta.decision_fn_id!r} version "
            f"{delta.decision_fn_version!r} != registered {reg.version!r}"
        )
    return None


def _rule_strength_monotonicity(
    delta: "ClaimDelta", head: "ClaimRecord | None"
) -> str | None:
    if (
        delta.status in MUTATING_STATUSES
        and head is not None
        and strength_rank(delta.evidence_strength)
        < strength_rank(head.evidence_strength)
    ):
        return (
            f"evidence band {delta.evidence_strength.value} < target head band "
            f"{head.evidence_strength.value} (weak may not retract strong)"
        )
    return None


#: The data-driven legality rule table evaluated by ``evaluate_gate`` — codes are
#: stable, ordered by precedence (registration/version reject before strength
#: degrade). Adding/altering a row is a gate change -> bump GATE_RULES_VERSION.
_GATE_PREDICATE_RULES: tuple[tuple[str, str, Callable[..., str | None]], ...] = (
    (
        DENY_UNREGISTERED_DECISION_FN,
        GATE_DISPOSITION_REJECT,
        _rule_decision_fn_registered,
    ),
    (
        DENY_DECISION_FN_VERSION_MISMATCH,
        GATE_DISPOSITION_REJECT,
        _rule_decision_fn_version,
    ),
    (
        DENY_WEAK_CANNOT_RETRACT_STRONG,
        GATE_DISPOSITION_DEGRADE,
        _rule_strength_monotonicity,
    ),
)

#: Structural rules enforced at construction/insert time rather than by predicate
#: (catalogued here so the full rule table is machine-readable in one place):
#:  * insufficient => no new claim version (ClaimDelta validator, type-level, K3);
#:  * append-only  => a version is never rewritten in place (_add_record raises
#:    ClaimLedgerError with code DENY_APPEND_ONLY_VIOLATION).
_GATE_STRUCTURAL_RULES: tuple[tuple[str, str], ...] = (
    ("insufficient_no_new_version", "ClaimDelta validator (type-level)"),
    (DENY_APPEND_ONLY_VIOLATION, "_add_record insert guard"),
)


def _compute_gate_fingerprint() -> str:
    payload = {
        "version": GATE_RULES_VERSION,
        "predicate_rules": [
            {"code": code, "disposition": disp, "source": _source_fingerprint(pred)}
            for code, disp, pred in _GATE_PREDICATE_RULES
        ],
        "structural_rules": [
            {"code": code, "enforced_by": where}
            for code, where in _GATE_STRUCTURAL_RULES
        ],
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )


#: Fingerprint over the whole rule table (version + predicate sources + structural
#: catalog). Any gate change moves it — the audit hook for "changing the gate".
GATE_RULES_FINGERPRINT = _compute_gate_fingerprint()


def evaluate_gate(delta: "ClaimDelta", head: "ClaimRecord | None") -> GateResult:
    """Evaluate the declarative legality rule table for one delta against the
    current head. Returns a machine-readable ``GateResult`` (conforms + report of
    stably-coded violations) instead of scattered imperative ifs. ``apply_claim_
    deltas`` consumes it: a REJECT violation denies the delta (no record lands), a
    DEGRADE violation lands a downgraded annotation, and ``conforms`` => the delta
    lands as its verdict dictates."""
    violations = tuple(
        GateViolation(code=code, disposition=disp, detail=detail)
        for code, disp, pred in _GATE_PREDICATE_RULES
        if (detail := pred(delta, head)) is not None
    )
    return GateResult(conforms=not violations, violations=violations)


# ---------------------------------------------------------------- frozen schema


class _FrozenModel(BaseModel):
    """Immutable, closed base — a ClaimDelta / provenance snapshot cannot be
    edited after construction (a proposed mutation is a fact, not a mutable
    scratchpad; same discipline as knowledge.KnowledgeView)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationFingerprint(_FrozenModel):
    """One input observation id paired with the content fingerprint the statistic
    consumed (K1/K4 audit: the fingerprint pins WHICH bytes of the observation
    fed the adjudication, so a re-derivation can detect substitution)."""

    obs_id: str
    content_fingerprint: str


class GroupSummary(_FrozenModel):
    """Per-group sufficient statistics (expan SampleStatistics shape) so the
    statistic is recomputable from the snapshot. All optional — K-B fills them."""

    group: str
    n: int | None = None
    mean: float | None = None
    var: float | None = None


class StatisticSnapshot(_FrozenModel):
    """The self-sufficient statistic record (cross-repo convergence S-B: results
    = named fields + one serializable self-contained record). K-A fixes the
    schema; K-B populates it. Nothing here is a gate input except via the
    ``evidence_strength`` BAND on the delta — ``achieved_power`` is display-only
    (post-hoc power is monotone with p, so it is provenance/display, never the
    sole gate), and ``evidence_factor`` is the continuous side-info behind the
    band."""

    test_method: str = ""
    statistic_name: str = ""
    statistic_value: float | None = None
    df: float | None = None
    tail: str | None = None
    p_value: float | None = None
    effect_estimate: float | None = None
    effect_se: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    achieved_power: float | None = None  # display-only (K3 note), not a sole gate
    # Continuous BF/e-value-type quantity behind the band. NOT a belief score:
    # a single-hypothesis evidence measure, never a noisy-OR aggregate over
    # correlated evidence items (rejected in REFERENCE_MAP 24.2 -- no
    # insufficient outlet, overstates confidence on same-batch observations).
    evidence_factor: float | None = None
    independence_assumed: bool | None = None  # §3 correlation account (M18 hook)
    seed: int | None = None  # deterministic replay (K5)
    per_group: tuple[GroupSummary, ...] = ()
    # ---- K-B additive fields (schema-additive, letter 080: "全 optional，K-B 填";
    # all default None so K-A / offline-compiler snapshots are unaffected) --------
    #: rounds folded into the accumulated e-process (single-round-fluke guard, K3).
    rounds_observed: int | None = None
    #: which sign of the effect supports the claim ("higher" | "lower") — polarity
    #: is recomputable from effect_estimate + this (offline ``_favorable`` parity).
    favorable_direction: str | None = None
    #: the machine-readable optional-stopping assumption behind the cross-round
    #: e-product (letter 068: assumptions are first-class chain citizens, not a
    #: bare assert) — e.g. {conditional_independence_across_rounds, basis:[...]}.
    filtration_assumption: dict[str, Any] | None = None
    #: the frozen decision-threshold set (alpha/e_threshold/w_min/r_min/floor) the
    #: verdict was computed against — pins the criterion for third-party replay (K4).
    decision_thresholds: dict[str, Any] | None = None
    #: plate-order-balance diagnostic = corr(measurement index, contrast covariate)
    #: over the round (letter 075): calibration drift along measurement order can
    #: fake a direction signal, so an imbalanced plate order is confound-suspect.
    #: Forward hook for the ③ evidence-typing temporal channel.
    plate_order_balance: float | None = None
    #: True when |plate_order_balance| exceeded the documented bound and the round
    #: was refused (degraded to insufficient) rather than adjudicated (letter 075).
    confound_suspect: bool | None = None
    # ---- M23 Phase 0 unit metadata (REF-U §Convergence(b); additive-optional, all
    # default None so K-A / offline-compiler snapshots are byte-unaffected) --------
    #: The unit string for the effect-size family. ``effect_estimate`` / ``effect_se``
    #: / ``ci_low`` / ``ci_high`` SHARE ONE unit by construction -- they are a
    #: location estimate, its standard error, and the two CI bounds of the SAME
    #: metric contrast, so all four carry the metric's unit (a difference of means
    #: keeps the summand's unit; the SE and CI bounds of that difference keep it
    #: too). Hence one field, NOT four. Units-as-schema-metadata (astropy ECSV
    #: posture): a bare string carried beside the bare floats -- the kernel performs
    #: NO arithmetic and NO conversion on it ever (REF-U reject #3: a scalar-factor
    #: conversion silently corrupts offset units like celsius). K-B's aggregator
    #: populates it later from the adjudicated metric's declared unit; None = legacy
    #: / unit not carried. No ``statistic_unit`` field: ``statistic_value`` (a t/z/F
    #: statistic) is DIMENSIONLESS by construction, so a unit there would be noise --
    #: the REF-U (b)(1) minimal set is exactly this one effect-family unit.
    effect_unit: str | None = None


class ProvenanceActivity(_FrozenModel):
    """PROV 'activity' role — WHAT generated the decision: the registered
    decision_fn (id + version), an optional run/source fingerprint and the
    statistical criterion version. This is the governance-legality subject."""

    decision_fn_id: str
    decision_fn_version: str
    criterion_version: str
    run_fingerprint: str | None = None
    #: The declarative legality rule-table version/fingerprint this decision was
    #: built against — changing the gate is auditable from the snapshot alone.
    gate_rules_version: str = GATE_RULES_VERSION
    gate_rules_fingerprint: str = GATE_RULES_FINGERPRINT


class ProvenanceUsage(_FrozenModel):
    """PROV 'usage' role — WHICH entities were used: the input observations (id +
    content fingerprint) and the consumed compiled-knowledge fingerprint (the old
    fingerprint this adjudication was computed against, K4)."""

    observations: tuple[ObservationFingerprint, ...] = ()
    consumed_knowledge_fingerprint: str


class ProvenanceSnapshot(_FrozenModel):
    """Frozen per-decision provenance — the K1/K4 audit hook, shaped on the W3C
    PROV derivation five-tuple with two distinct roles plus the statistic record:

      * ``usage`` — entities used (input observations + consumed knowledge fp);
      * ``activity`` — the generating decision_fn (id/version + criterion + run fp);
      * ``statistic`` — the self-sufficient statistic/power record.

    Serializes/deserializes round-trip losslessly so a third party can recompute
    the verdict from the event stream alone."""

    usage: ProvenanceUsage
    activity: ProvenanceActivity
    statistic: StatisticSnapshot = StatisticSnapshot()

    def fingerprint(self) -> str:
        """Canonical sha256 over the whole snapshot — the single value the K1
        discriminator asserts on, and a tamper-evident key for the provenance
        chain."""
        payload = self.model_dump(mode="json")
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
        )


class ClaimVersionContent(_FrozenModel):
    """The proposed content of a NEW claim version (present on head-mutating
    deltas; None on ``insufficient`` deltas, which propose no head)."""

    statement: str
    status: ClaimDecisionStatus


class ClaimDelta(_FrozenModel):
    """One proposed ledger mutation produced by a certification policy at round
    end. Frozen/immutable.

    Fields (this list is the K-E acceptance contract):
      * ``target_claim_id`` — the logical claim this delta adjudicates.
      * ``status`` — the decision verdict (ClaimDecisionStatus).
      * ``new_content`` — the new claim version content for a supersede, or
        ``None`` for ``insufficient`` (no head proposed; structurally forbidden by
        the validator — TYPE-level isolation of insufficient, K3).
      * ``evidence_strength`` — the delta's evidence-strength BAND, judged against
        the target head by the strength-monotonicity gate.
      * ``provenance`` — the frozen ProvenanceSnapshot (K1/K4 audit hook). The
        decision_fn id/version live in ``provenance.activity``; ``decision_fn_id``
        / ``decision_fn_version`` are convenience accessors onto it.
    """

    target_claim_id: str
    status: ClaimDecisionStatus
    new_content: ClaimVersionContent | None = None
    evidence_strength: EvidenceStrength
    provenance: ProvenanceSnapshot

    @property
    def decision_fn_id(self) -> str:
        return self.provenance.activity.decision_fn_id

    @property
    def decision_fn_version(self) -> str:
        return self.provenance.activity.decision_fn_version

    @model_validator(mode="after")
    def _content_matches_status(self) -> "ClaimDelta":
        # insufficient proposes no head (TYPE-level isolation); every mutating
        # status must carry content whose status echoes the verdict (no split-brain).
        if self.status is ClaimDecisionStatus.INSUFFICIENT:
            if self.new_content is not None:
                raise ValueError(
                    "insufficient ClaimDelta must not carry new_content "
                    "(it never proposes a head — gate K3, type-level isolation)"
                )
        else:
            if self.new_content is None:
                raise ValueError(
                    f"mutating ClaimDelta (status={self.status.value}) requires new_content"
                )
            if self.new_content.status is not self.status:
                raise ValueError(
                    "ClaimDelta.new_content.status must equal ClaimDelta.status "
                    f"({self.new_content.status.value} != {self.status.value})"
                )
        return self


class ClaimRecord(_FrozenModel):
    """One immutable claim-ledger record. Records for a claim_id accumulate
    append-only; ``version`` is unique per claim_id (the N-th record touching it).
    A record is NEVER mutated after it lands.

    A record is the effective HEAD for its claim_id iff ``not is_annotation`` and
    no other non-annotation record supersedes it. ``supersedes`` is the immutable
    backward pointer of the bidirectional chain; the forward ``superseded_by`` is
    DERIVED read-side (``superseded_by`` / ``effective_statuses``), never stored as
    a mutable field. ``is_annotation`` records (insufficient verdicts and
    strength-denied deltas) never become head and never mutate the effective
    status — they are traceable-only (K3)."""

    claim_id: str
    version: int
    status: ClaimDecisionStatus
    statement: str = ""
    evidence_strength: EvidenceStrength = EvidenceStrength.NONE
    supersedes: int | None = None
    is_annotation: bool = False
    deny_reason: str | None = None
    provenance: ProvenanceSnapshot

    @property
    def decision_fn_id(self) -> str:
        return self.provenance.activity.decision_fn_id

    @property
    def decision_fn_version(self) -> str:
        return self.provenance.activity.decision_fn_version

    @property
    def evidence(self) -> tuple[ObservationFingerprint, ...]:
        """The within-round evidence LIST retained on this claim version — each
        item an observation id + its content fingerprint. Two orthogonal axes: the
        supersede chain (``supersedes``) carries cross-round evolution, while this
        list carries within-round multi-well aggregation. It is retained verbatim,
        NEVER collapsed into an aggregated belief score (that would lose per-item
        history and break gate K4's third-party-recompute requirement — INDRA
        anti-pattern)."""
        return self.provenance.usage.observations


class Ledger(_FrozenModel):
    """The online claim ledger: an immutable, canonically-ordered set of records.

    ``canonical_json`` is the determinism witness (gate K5): identical content
    serializes bit-for-bit identically regardless of insertion order."""

    claims: tuple[ClaimRecord, ...] = ()

    def canonical_json(self) -> str:
        recs = sorted(self.claims, key=lambda r: (r.claim_id, r.version))
        return json.dumps(
            [r.model_dump(mode="json") for r in recs],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def head(self, claim_id: str) -> ClaimRecord | None:
        """The current effective head for ``claim_id`` (or None if it has no
        mutating history — e.g. only insufficient annotations, or absent)."""
        return _current_head(
            {(r.claim_id, r.version): r for r in self.claims}, claim_id
        )

    def superseded_by(self, claim_id: str, version: int) -> int | None:
        """DERIVE the forward supersede pointer (inverse of ``supersedes``): the
        version that superseded (claim_id, version), or None if it is not (yet)
        superseded. The bidirectional chain is closed read-side, no stored field."""
        for r in self.claims:
            if (
                r.claim_id == claim_id
                and not r.is_annotation
                and r.supersedes == version
            ):
                return r.version
        return None

    def effective_statuses(self) -> dict[str, ClaimDecisionStatus]:
        """DERIVE every claim's effective status by replaying the supersede chain
        (pure function of the record set — the derived-status principle end to
        end). Claims with only insufficient annotations have no head and are
        absent from the map (K3: they inject no effective status)."""
        records = {(r.claim_id, r.version): r for r in self.claims}
        out: dict[str, ClaimDecisionStatus] = {}
        for claim_id in sorted({r.claim_id for r in self.claims}):
            head = _current_head(records, claim_id)
            if head is not None:
                out[claim_id] = head.status
        return out


class DeltaOutcome(_FrozenModel):
    """The per-delta result recorded in the apply report.

    ``mutated_effective_status`` is True only when a head was created/superseded.
    ``deny_reason`` is set when a gate denied/degraded the delta (registration /
    version / strength); ``landed_record_version`` is the version of the record
    that landed (a mutating head, a degraded annotation, or an insufficient
    annotation) or None when nothing landed (a registration/version failure leaves
    no record)."""

    target_claim_id: str
    final_status: ClaimDecisionStatus
    decision_fn_id: str
    mutated_effective_status: bool
    deny_reason: str | None = None
    landed_record_version: int | None = None


class ApplyReport(_FrozenModel):
    """The applied/rejected report from one ``apply_claim_deltas`` call."""

    outcomes: tuple[DeltaOutcome, ...] = ()

    @property
    def applied(self) -> tuple[DeltaOutcome, ...]:
        """Outcomes that landed as intended (mutating heads + insufficient
        annotations) — i.e. NOT denied by a gate."""
        return tuple(o for o in self.outcomes if o.deny_reason is None)

    @property
    def rejected(self) -> tuple[DeltaOutcome, ...]:
        """Outcomes denied/degraded by a gate (each carries a deny_reason)."""
        return tuple(o for o in self.outcomes if o.deny_reason is not None)


# ---------------------------------------------------------------- ledger primitives


def _current_head(
    records: dict[tuple[str, int], ClaimRecord], claim_id: str
) -> ClaimRecord | None:
    """Derive the effective head: the non-annotation record for ``claim_id`` that
    no other non-annotation record supersedes. At most one such head exists."""
    non_annot = [
        r for (cid, _v), r in records.items() if cid == claim_id and not r.is_annotation
    ]
    superseded = {r.supersedes for r in non_annot if r.supersedes is not None}
    heads = [r for r in non_annot if r.version not in superseded]
    if len(heads) > 1:
        # Structural invariant: at most one effective head per claim_id.
        raise ClaimLedgerError(
            f"claim {claim_id!r} has {len(heads)} effective heads — supersede chain corrupt"
        )
    return heads[0] if heads else None


def _next_version(records: dict[tuple[str, int], ClaimRecord], claim_id: str) -> int:
    versions = [v for (cid, v) in records if cid == claim_id]
    return (max(versions) + 1) if versions else 1


def _add_record(records: dict[tuple[str, int], ClaimRecord], rec: ClaimRecord) -> None:
    """Append-only insert (governance red line 2). A given (claim_id, version) is
    written at most once: re-inserting identical content is an idempotent no-op,
    but overwriting an existing version with DIFFERENT content is a rewrite-in-
    place — an append-only violation that fails loudly."""
    key = (rec.claim_id, rec.version)
    existing = records.get(key)
    if existing is not None and existing != rec:
        raise ClaimLedgerError(
            f"{DENY_APPEND_ONLY_VIOLATION}: refusing to rewrite claim "
            f"{rec.claim_id!r} version {rec.version} in place — the ledger is "
            "append-only (supersede appends a new version, never edits an old one)"
        )
    records[key] = rec


def add_claim_record(ledger: Ledger, record: ClaimRecord) -> Ledger:
    """Public append-only insert primitive (used by ``apply_claim_deltas`` and
    directly testable): returns a new Ledger with ``record`` appended, or fails
    loudly if it would rewrite an existing version in place."""
    records = {(r.claim_id, r.version): r for r in ledger.claims}
    _add_record(records, record)
    return Ledger(
        claims=tuple(sorted(records.values(), key=lambda r: (r.claim_id, r.version)))
    )


def ledger_to_claim_dicts(ledger: Ledger) -> list[dict[str, str]]:
    """Project the ledger to the ``{claim_id, status}`` shape consumed by
    ``kernel.knowledge.compile_knowledge`` — only effective HEADS contribute (a
    claim with only insufficient annotations has no head and injects no signal,
    gate K3). Sorted by claim_id for a stable, order-insensitive projection."""
    return [
        {"claim_id": claim_id, "status": status.value}
        for claim_id, status in ledger.effective_statuses().items()
    ]


# ---------------------------------------------------------------- apply path


def _delta_sort_key(delta: ClaimDelta) -> tuple[str, str]:
    """Explicit deterministic ordering of deltas within a round (gate K5; the R3
    P0 lesson that tie-breaks must be explicit). Primary key is the target claim
    id; the canonical JSON of the whole delta is the total-order secondary key so
    two deltas never resolve by list/enumeration order."""
    canonical = json.dumps(
        delta.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (delta.target_claim_id, canonical)


def _apply_one(
    records: dict[tuple[str, int], ClaimRecord], delta: ClaimDelta
) -> DeltaOutcome:
    target = delta.target_claim_id
    head = _current_head(records, target)

    # ---- declarative legality gate (red lines 1 + 2) --------------------------
    gate = evaluate_gate(delta, head)

    # A REJECT violation (unregistered / version-mismatch decision_fn) denies the
    # delta outright: illegitimate provenance -> no record lands, recorded loudly.
    reject = gate.first(GATE_DISPOSITION_REJECT)
    if reject is not None:
        return DeltaOutcome(
            target_claim_id=target,
            final_status=delta.status,
            decision_fn_id=delta.decision_fn_id,
            mutated_effective_status=False,
            deny_reason=reject.code,
            landed_record_version=None,
        )

    next_v = _next_version(records, target)

    def _annotation(
        status: ClaimDecisionStatus, deny_reason: str | None
    ) -> ClaimRecord:
        return ClaimRecord(
            claim_id=target,
            version=next_v,
            status=status,
            statement=(delta.new_content.statement if delta.new_content else ""),
            evidence_strength=delta.evidence_strength,
            supersedes=None,
            is_annotation=True,
            deny_reason=deny_reason,
            provenance=delta.provenance,
        )

    # ---- red line 3: insufficient never mutates effective status --------------
    if delta.status is ClaimDecisionStatus.INSUFFICIENT:
        _add_record(records, _annotation(ClaimDecisionStatus.INSUFFICIENT, None))
        return DeltaOutcome(
            target_claim_id=target,
            final_status=ClaimDecisionStatus.INSUFFICIENT,
            decision_fn_id=delta.decision_fn_id,
            mutated_effective_status=False,
            deny_reason=None,
            landed_record_version=next_v,
        )

    # ---- red line 2: strength-monotonicity — weak may not retract strong -------
    # A DEGRADE violation lands only a downgraded, loudly-reasoned annotation; the
    # head is untouched. (Gate rule: delta band >= target head band, ordinal.)
    degrade = gate.first(GATE_DISPOSITION_DEGRADE)
    if degrade is not None:
        # Degrade: no evidence at all -> insufficient; some (but weaker) -> qualified.
        degraded = (
            ClaimDecisionStatus.INSUFFICIENT
            if delta.evidence_strength is EvidenceStrength.NONE
            else ClaimDecisionStatus.QUALIFIED
        )
        _add_record(records, _annotation(degraded, degrade.code))
        return DeltaOutcome(
            target_claim_id=target,
            final_status=degraded,
            decision_fn_id=delta.decision_fn_id,
            mutated_effective_status=False,
            deny_reason=degrade.code,
            landed_record_version=next_v,
        )

    # ---- mutating: create/supersede a head (immutable bidirectional chain) -----
    assert delta.new_content is not None  # guaranteed by ClaimDelta validator
    new_head = ClaimRecord(
        claim_id=target,
        version=next_v,
        status=delta.status,
        statement=delta.new_content.statement,
        evidence_strength=delta.evidence_strength,
        supersedes=(head.version if head is not None else None),
        is_annotation=False,
        deny_reason=None,
        provenance=delta.provenance,
    )
    _add_record(records, new_head)
    return DeltaOutcome(
        target_claim_id=target,
        final_status=delta.status,
        decision_fn_id=delta.decision_fn_id,
        mutated_effective_status=True,
        deny_reason=None,
        landed_record_version=next_v,
    )


def apply_claim_deltas(
    ledger: Ledger, deltas: list[ClaimDelta]
) -> tuple[Ledger, ApplyReport]:
    """Apply a round's ClaimDeltas to the ledger under the three governance red
    lines. PURE: no I/O, no clock, no randomness — the same ``deltas`` on the same
    ``ledger`` yield a bit-for-bit identical new ledger (gate K5). Deltas are
    applied in ``_delta_sort_key`` order (explicit, total, caller-order-invariant).
    Records already in the ledger are never mutated; effective status is derived."""
    records: dict[tuple[str, int], ClaimRecord] = {
        (r.claim_id, r.version): r for r in ledger.claims
    }
    outcomes: list[DeltaOutcome] = []
    for delta in sorted(deltas, key=_delta_sort_key):
        outcomes.append(_apply_one(records, delta))
    new_ledger = Ledger(
        claims=tuple(sorted(records.values(), key=lambda r: (r.claim_id, r.version)))
    )
    return new_ledger, ApplyReport(outcomes=tuple(outcomes))


# ---------------------------------------------------------------- emit helper


def emit_claim_decision(
    store: "RunStore",
    *,
    round_id: int,
    delta: ClaimDelta,
    final_status: ClaimDecisionStatus,
    landed_version: int | None,
    deny_reason: str | None = None,
) -> dict[str, Any] | None:
    """Emit the ``claim_decision`` event for one adjudication.

    Payload (EVENT_SCHEMA.md §1) carries the full K4 provenance so a third party
    can recompute the verdict from the event stream alone: input observation ids,
    the statistic summary, the power side-info, the consumed knowledge_fingerprint,
    the produced claim id/version, the decision status + decision_fn id, and
    ``deny_reason`` when a gate degraded/denied the delta.

    Required keys (store.EVENT_PAYLOAD_REQUIRED) = {round_id, claim_id,
    claim_version, decision_status, decision_fn_id, input_observation_ids,
    statistic, power, consumed_knowledge_fingerprint}.

    This is only the emit helper — the emission POINT (which round, after which
    apply) is wired by K-C (Certification Policy + mcl round-end hook), NOT here,
    mirroring ``kernel.knowledge.emit_knowledge_updated``. Nothing in K-A emits it.

    Routed through :meth:`RunStore.append_decision_face_event` (Phase 4 item #1) for
    resume-idempotent exactly-once: dedup key = (round_id, claim_id), content fingerprint =
    the provenance fingerprint. A redone round re-emitting the SAME adjudication is skipped
    (returns ``None``); a DIFFERENT one for the same (round, claim) raises NondeterminismError
    (a redo that did not reproduce the verdict bitwise — never silently resolved)."""
    prov = delta.provenance
    stat = prov.statistic
    return store.append_decision_face_event(
        "claim_decision",
        {
            "pv": CLAIM_DECISION_PV,
            "round_id": round_id,
            "claim_id": delta.target_claim_id,
            "claim_version": landed_version,
            "decision_status": final_status.value,
            "decision_fn_id": prov.activity.decision_fn_id,
            "decision_fn_version": prov.activity.decision_fn_version,
            "criterion_version": prov.activity.criterion_version,
            "input_observation_ids": [o.obs_id for o in prov.usage.observations],
            # non-ABI detail: obs_id -> content fingerprint (K1 substitution audit)
            "observation_fingerprints": {
                o.obs_id: o.content_fingerprint for o in prov.usage.observations
            },
            "statistic": {
                "name": stat.statistic_name,
                "value": stat.statistic_value,
                "test": stat.test_method,
                "df": stat.df,
                "tail": stat.tail,
                "p_value": stat.p_value,
                "effect_estimate": stat.effect_estimate,
                "effect_se": stat.effect_se,
                "ci": [stat.ci_low, stat.ci_high],
                "seed": stat.seed,
            },
            "power": {
                "achieved_power": stat.achieved_power,
                "evidence_factor": stat.evidence_factor,
                "evidence_strength": delta.evidence_strength.value,
                "independence_assumed": stat.independence_assumed,
            },
            "consumed_knowledge_fingerprint": prov.usage.consumed_knowledge_fingerprint,
            # non-ABI detail: the single provenance-chain audit key (K1)
            "provenance_fingerprint": prov.fingerprint(),
            # non-ABI detail: the live legality rule-table version/fingerprint the
            # apply path enforced (changing the gate is auditable from the stream)
            "gate_rules_version": GATE_RULES_VERSION,
            "gate_rules_fingerprint": GATE_RULES_FINGERPRINT,
            "deny_reason": deny_reason,
        },
        dedup_key=(round_id, delta.target_claim_id),
        content_fingerprint=prov.fingerprint(),
    )
