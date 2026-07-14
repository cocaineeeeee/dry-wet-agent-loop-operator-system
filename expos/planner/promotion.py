"""Dry->Wet promotion policy (M16 W7) — the sixth planner-injection element.

Given the dry leg's per-candidate view, a candidate risk map, the compiled
knowledge fingerprint and a wet-cost budget, decide WHICH dry candidates earn a
wet well — and record WHY every denied candidate was denied. No silent edge
(G4 "recorded evidence decision", docs/M16_MIN_LOOP.md §0): both the promoted
set and every denial land in the ``promotion_decision`` event.

Two policies:
  * ``NullPromotion`` — the default sixth element for every existing arm. Its
    ``decide()`` returns ``None``: no promotion mechanism is engaged and no event
    is emitted, so a non-M16 run is byte-for-byte unchanged (the first five
    injection elements are untouched, this one is inert — the regression is that
    trivial). Mirrors the ``learning_weight_assigned`` surface discipline: base
    policies never set the surface -> zero mode-branch.
  * ``EvidenceGatedPromotion`` — the M16 / ``--loop mcl`` gate. A CONJUNCTIVE
    four-channel gate (convergence AND window AND rank AND risk), never a
    weighted scalar.

``decide()`` is a PURE function: no I/O, no clock, no randomness, and — because
inputs are canonically re-sorted internally — no dependence on caller-side list
order. Identical inputs yield an identical ``PromotionDecision``, so a replay
reconstructs the same set (the G1 determinism substrate, applied to promotion).
Emission and loop wiring live elsewhere (``emit_promotion_decision`` + the W9
``mcl`` integration); ``decide()`` never touches the store.

Layering (public red line EXP007): ``planner/`` may import ``kernel/``; this
module imports no adapters (the dry/wet leg specifics reach ``decide()`` only as
domain-agnostic value objects the W9 wiring fills in), and no ``loop``/``agent``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from expos.errors import ExposError

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime coupling
    from expos.kernel.store import RunStore


class PromotionError(ExposError):
    pass


#: promotion_decision payload version — pv is born with the event (REF-1
#: governance at birth); bumping it is an intended, registered schema break.
PV = 1

# ---- deny_reason enumeration (design point 8 + letter 061 §5) ---------------
# A denied candidate always carries exactly one of these. gate_* are the four
# channel gates; budget_truncated is the wet-cost ceiling cut; dry_failed is the
# scheduler failure channel (a FAILED/TIMEOUT dry leg is not evidence).
DENY_DRY_FAILED = "dry_failed"
DENY_GATE_CONVERGENCE = "gate_convergence"
DENY_GATE_WINDOW = "gate_window"
DENY_GATE_RISK = "gate_risk"
DENY_GATE_RANK = "gate_rank"
DENY_BUDGET_TRUNCATED = "budget_truncated"

DENY_REASONS: frozenset[str] = frozenset({
    DENY_DRY_FAILED,
    DENY_GATE_CONVERGENCE,
    DENY_GATE_WINDOW,
    DENY_GATE_RISK,
    DENY_GATE_RANK,
    DENY_BUDGET_TRUNCATED,
})

#: acquisition_rank sentinel for a candidate denied BEFORE it reached ranking
#: (dry_failed / convergence / window / risk gates fire per-candidate, upstream
#: of the deterministic ordering). -1.0 reads as "never ranked".
RANK_UNRANKED = -1.0


# ============================================================ value objects

@dataclass(frozen=True)
class WetCostEstimate:
    """Per-candidate wet-leg cost estimate (W5 handoff cost model): consumable
    cost ~= ``n_transfers`` (one tip per stock per well), instrument-time cost
    ~= ``duration_s`` (deck overhead + per-aspirate model, from the ot_protocol
    ledger). Modifier 2 (letter 055): this rides verbatim into the
    ``promotion_decision`` payload so an audit can reconstruct WHY truncation
    landed at k — the cost inputs to the budget cut ARE decision evidence."""

    n_transfers: int
    duration_s: float


@dataclass(frozen=True)
class DryCandidateView:
    """One dry candidate offered for promotion. Domain-agnostic: the W9 ``mcl``
    wiring builds these from the PySCF dry leg (``InstrumentProvenance.converged``
    formal bit + the dipole->polarity window test), the response-surface
    acquisition, and the wet cost model. ``decide()`` never imports adapters.

    Fields:
      * ``converged`` — the formal provenance bit (engine=pyscf; a non-converged
        SCF is not evidence). Feeds the BINARY convergence gate. See the
        modifier-4 note on ``_evaluate`` for why "edge-converged" (scf_cycles
        pinned at max) is deliberately NOT modelled here and is left to typed
        evidence (VNext ③) — the binary gate is a minimization trade-off, not a
        designed-in claim that convergence is truly two-valued.
      * ``in_window`` — the polarity estimate lies inside the mixable window.
      * ``acquisition`` — response-surface posterior acquisition (e.g. UCB);
        HIGHER is better. Drives the deterministic top-k ordering.
      * ``wet_cost`` — per-candidate cost estimate (modifier 2).
      * ``failure_detail`` — the scheduler ``failure_detail()`` record when the
        dry leg reached a FAILED/TIMEOUT terminal state; ``None`` otherwise.
        Presence => ``dry_failed`` (design point 8).
    """

    cand_id: str
    converged: bool
    in_window: bool
    acquisition: float
    wet_cost: WetCostEstimate
    failure_detail: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChannelBasis:
    """Per-candidate four-channel gate basis (design point 3, legislated per
    letter 046 / 061). Each channel is judged by its OWN predicate and the gate
    is the conjunction ``convergence AND window AND rank AND risk`` — the four
    values are NEVER folded into a single weighted scalar.

    A mutant that sums the channels and thresholds once is killed by
    ``test_m_basis_scalar_fold_must_be_red``: a candidate that fails one hard
    gate (e.g. convergence=0) but scores very high on another (acquisition) would
    wrongly promote under a scalar fold, whereas the conjunctive gate denies it.

    Channel semantics (all recorded verbatim in the event payload):
      * ``convergence`` — 1.0 if the dry provenance converged else 0.0.
      * ``window`` — 1.0 if the polarity estimate is in-window else 0.0.
      * ``acquisition_rank`` — 0-based rank after the deterministic ordering
        (LOWER is better); the top-k gate reads this. ``RANK_UNRANKED`` (-1.0)
        for a candidate denied before ranking.
      * ``risk`` — candidate artifact-risk (from the risk map); the risk gate
        reads this.
    """

    convergence: float
    window: float
    acquisition_rank: float
    risk: float


@dataclass(frozen=True)
class PromotedCandidate:
    """A candidate that passed every gate and fit under budget."""

    cand_id: str
    basis: ChannelBasis
    wet_cost: WetCostEstimate


@dataclass(frozen=True)
class DeniedCandidate:
    """A denied candidate WITH its channel basis, deny_reason and cost estimate
    (design point 5: denied candidates leave a trail, no silent edge; modifier 2:
    the cost estimate rides along so a budget truncation is auditable)."""

    cand_id: str
    basis: ChannelBasis
    deny_reason: str
    wet_cost: WetCostEstimate


@dataclass(frozen=True)
class PromotionDecision:
    """The pure output of ``decide()``.

    ``knowledge_fingerprint`` is the G1 witness: the exact compiled-knowledge
    state this decision consumed (design point 6). Recording it makes the
    decision replay-auditable and lets the W8 discriminator assert that freezing
    knowledge freezes the decision, while a reverse claim (which re-fingerprints
    the knowledge and re-steers the dry view) predictably changes it.
    """

    pv: int
    knowledge_fingerprint: str
    policy: str
    promoted: tuple[PromotedCandidate, ...]
    denied: tuple[DeniedCandidate, ...]


@dataclass(frozen=True)
class PromotionBudget:
    """Wet-cost budget bounding the promoted set (design point 7). Two
    INDEPENDENT cuts, each with its own deny_reason so the audit stays legible:

      * ``top_k`` — a hard candidate-count cap (the top-k channel gate,
        deny_reason ``gate_rank``). ``None`` => no rank cap.
      * ``max_transfers_total`` / ``max_duration_s_total`` — cumulative wet-cost
        ceilings walked in rank order (deny_reason ``budget_truncated``).
        ``None`` => that ceiling is off.

    ``risk_threshold`` gates the risk channel (deny_reason ``gate_risk``): a
    candidate whose risk exceeds it is denied regardless of acquisition.
    """

    top_k: int | None = None
    max_transfers_total: int | None = None
    max_duration_s_total: float | None = None
    risk_threshold: float = 1.0


# ============================================================ decision core

def _basis(cand: DryCandidateView, risk: float, rank: float) -> ChannelBasis:
    """Honest per-candidate basis: each channel reflects the candidate's actual
    input value, so the recorded basis says which channel(s) failed."""
    return ChannelBasis(
        convergence=1.0 if cand.converged else 0.0,
        window=1.0 if cand.in_window else 0.0,
        acquisition_rank=float(rank),
        risk=float(risk),
    )


def decide(
    dry_view: list[DryCandidateView],
    risk_map: dict[str, float] | None,
    knowledge_fingerprint: str,
    budget: PromotionBudget,
) -> PromotionDecision:
    """Pure Dry->Wet promotion gate.

    Order of judgement (per candidate, absolute gates first — these fire before
    any ranking so their outcome cannot depend on the peer set):
      1. ``dry_failed`` — ``failure_detail`` present (a FAILED/TIMEOUT dry leg is
         not evidence; design point 8). Dominates all other reasons.
      2. ``gate_convergence`` — ``converged`` is False.
      3. ``gate_window`` — ``in_window`` is False.
      4. ``gate_risk`` — candidate risk > ``budget.risk_threshold``.

    Survivors are ordered DETERMINISTICALLY by ``(acquisition DESC, cand_id
    ASC)`` (modifier 1, letter 055 — the load-bearing tie-break). Then two budget
    cuts in rank order:
      5. ``gate_rank`` — rank >= ``budget.top_k``.
      6. ``budget_truncated`` — cumulative ``n_transfers`` / ``duration_s`` would
         exceed a ceiling. Greedy-by-rank: once a ceiling is hit every remaining
         candidate is truncated (no cheaper later candidate sneaks in — that
         would make the cut depend on cost noise; determinism wins).

    ``knowledge_fingerprint`` is explicitly consumed: the gate refuses to decide
    without a knowledge witness (loud-fail, no silent default — the G1 hook), and
    the witness is recorded on the decision.
    """
    if not knowledge_fingerprint:
        raise PromotionError(
            "decide() requires a non-empty knowledge_fingerprint (G1 hook): the "
            "promotion gate must record the compiled-knowledge state it consumed"
        )
    rmap = risk_map or {}

    survivors: list[tuple[DryCandidateView, float]] = []
    denied: list[DeniedCandidate] = []

    # ---- absolute per-candidate gates (order-independent) -------------------
    for cand in dry_view:
        risk = float(rmap.get(cand.cand_id, 0.0))
        if cand.failure_detail is not None:
            reason = DENY_DRY_FAILED
        elif not cand.converged:
            reason = DENY_GATE_CONVERGENCE
        elif not cand.in_window:
            reason = DENY_GATE_WINDOW
        elif risk > budget.risk_threshold:
            reason = DENY_GATE_RISK
        else:
            survivors.append((cand, risk))
            continue
        denied.append(DeniedCandidate(
            cand.cand_id, _basis(cand, risk, RANK_UNRANKED), reason, cand.wet_cost,
        ))

    # ---- deterministic ordering (modifier 1: R3 P0 same-shape tie-break) ----
    # acquisition DESC, cand_id ASC as the total-order secondary key. cand_id is
    # unique so the order is total: two candidates with byte-identical
    # acquisition resolve by cand_id, NEVER by enumeration/insertion order.
    survivors.sort(key=lambda cr: (-cr[0].acquisition, cr[0].cand_id))

    # ---- rank + budget cuts (rank order) ------------------------------------
    promoted: list[PromotedCandidate] = []
    cum_transfers = 0
    cum_duration = 0.0
    truncated = False
    for rank, (cand, risk) in enumerate(survivors):
        basis = _basis(cand, risk, rank)
        if budget.top_k is not None and rank >= budget.top_k:
            denied.append(DeniedCandidate(
                cand.cand_id, basis, DENY_GATE_RANK, cand.wet_cost))
            continue
        nt = cum_transfers + cand.wet_cost.n_transfers
        du = cum_duration + cand.wet_cost.duration_s
        over = truncated or (
            (budget.max_transfers_total is not None and nt > budget.max_transfers_total)
            or (budget.max_duration_s_total is not None and du > budget.max_duration_s_total)
        )
        if over:
            truncated = True
            denied.append(DeniedCandidate(
                cand.cand_id, basis, DENY_BUDGET_TRUNCATED, cand.wet_cost))
            continue
        cum_transfers, cum_duration = nt, du
        promoted.append(PromotedCandidate(cand.cand_id, basis, cand.wet_cost))

    # denied is sorted by cand_id so the whole decision is caller-order-invariant
    # (strengthens the G1 freeze: shuffle dry_view -> byte-identical decision).
    denied.sort(key=lambda d: d.cand_id)

    return PromotionDecision(
        pv=PV,
        knowledge_fingerprint=knowledge_fingerprint,
        policy=EvidenceGatedPromotion.name,
        promoted=tuple(promoted),
        denied=tuple(denied),
    )


# ============================================================ policies

@runtime_checkable
class PromotionPolicy(Protocol):
    """The sixth planner-injection element. ``decide()`` returns ``None`` when no
    promotion mechanism is engaged (NullPromotion) or a ``PromotionDecision``
    when it is (EvidenceGatedPromotion)."""

    name: str

    def decide(
        self,
        dry_view: list[DryCandidateView],
        risk_map: dict[str, float] | None,
        knowledge_fingerprint: str,
        budget: PromotionBudget,
    ) -> PromotionDecision | None: ...


class NullPromotion:
    """Default sixth element for every existing arm: no promotion, no event,
    zero behaviour change. Returns ``None`` so the loop emits nothing (the
    surface-absent discipline of ``learning_weight_assigned`` — zero mode-branch,
    the base policy simply never sets the surface)."""

    name = "null_promotion"

    def decide(
        self,
        dry_view: list[DryCandidateView],
        risk_map: dict[str, float] | None,
        knowledge_fingerprint: str,
        budget: PromotionBudget,
    ) -> PromotionDecision | None:
        return None


class EvidenceGatedPromotion:
    """M16 / ``--loop mcl`` evidence gate. Thin policy wrapper over the pure
    ``decide()`` — always returns a ``PromotionDecision`` (even when
    ``promoted == ()``: a zero-promotion round is a legal, loudly-recorded
    result, see ``emit_promotion_decision`` modifier-3 note)."""

    name = "evidence_gated"

    def decide(
        self,
        dry_view: list[DryCandidateView],
        risk_map: dict[str, float] | None,
        knowledge_fingerprint: str,
        budget: PromotionBudget,
    ) -> PromotionDecision | None:
        return decide(dry_view, risk_map, knowledge_fingerprint, budget)


# ============================================================ emission helper

def _cost_payload(cost: WetCostEstimate) -> dict[str, Any]:
    return {"n_transfers": cost.n_transfers, "duration_s": cost.duration_s}


def _basis_payload(basis: ChannelBasis) -> dict[str, Any]:
    return {
        "convergence": basis.convergence,
        "window": basis.window,
        "acquisition_rank": basis.acquisition_rank,
        "risk": basis.risk,
    }


def emit_promotion_decision(
    store: "RunStore", round_id: int, decision: PromotionDecision
) -> dict[str, Any] | None:
    """Emit the ``promotion_decision`` event for one round's decision.

    Payload (EVENT_SCHEMA.md §1): ``{pv, round_id, knowledge_fingerprint,
    promoted[], denied[]}``. Required keys (store.EVENT_PAYLOAD_REQUIRED):
    ``round_id`` / ``knowledge_fingerprint`` / ``promoted`` / ``denied``.

    Emission discipline (this helper is provided now; the emission POINT is
    **wired by W9** at G5 — it is NOT hard-wired into ``run_loop`` in this batch,
    mirroring ``kernel.knowledge.emit_knowledge_updated``):

      * A NullPromotion round yields ``decide() is None`` -> this helper is not
        called, no event (base policies never set the surface -> zero
        mode-branch, mirrors ``learning_weight_assigned``).
      * An EvidenceGatedPromotion round with ``promoted == ()`` STILL emits
        (modifier 3): a zero-promotion round is LEGAL-QUIET and legal-quiet must
        be loudly recorded. The ABSENCE of the event is the suspicious thing —
        not an empty ``promoted`` list. An activity/monitor gate that watches the
        wet leg must read "``promotion_decision`` exists with ``promoted == []``"
        as legal, and only a MISSING event as dead (FB3 / I-F1 legal-quiet-vs-
        dead lesson).
      * Resume rebuild does NOT re-emit (I4): the W9 wiring replays the persisted
        decision at the resume seam, it does not re-run ``decide`` there — the
        same resume-silence discipline ``learning_weight_assigned`` uses.
    """
    promoted = [
        {"cand_id": p.cand_id, "basis": _basis_payload(p.basis),
         "wet_cost": _cost_payload(p.wet_cost)}
        for p in decision.promoted
    ]
    denied = [
        {"cand_id": d.cand_id, "basis": _basis_payload(d.basis),
         "deny_reason": d.deny_reason, "wet_cost": _cost_payload(d.wet_cost)}
        for d in decision.denied
    ]
    # Content fingerprint (Phase 4 item #1): a canonical-json sha256 over the decision
    # content (knowledge fingerprint + policy + promoted/denied), the equivalence witness the
    # resume dedup guard compares (blueprint §Convergence b: "canonical-json hash for
    # promotion_decision"). ADDITIVE non-ABI key; excludes round_id (the dedup KEY, not
    # content) and pv (transport version) so a redone round reproduces it bitwise.
    content_fingerprint = hashlib.sha256(
        json.dumps(
            {"knowledge_fingerprint": decision.knowledge_fingerprint,
             "policy": decision.policy, "promoted": promoted, "denied": denied},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return store.append_decision_face_event(
        "promotion_decision",
        {
            "pv": decision.pv,
            "round_id": round_id,
            "knowledge_fingerprint": decision.knowledge_fingerprint,
            "policy": decision.policy,
            "promoted": promoted,
            "denied": denied,
            "content_fingerprint": content_fingerprint,
        },
        dedup_key=(round_id,),
        content_fingerprint=content_fingerprint,
    )
