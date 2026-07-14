"""Discriminating suite for the M16 W7 Dry->Wet promotion gate
(``expos/planner/promotion.py``). Seven-plus discriminators, per letters
red_to_blue/061 (six) + blue_to_red/055 (the load-bearing tie construction):

  1. test_null_promotion_is_zero_behaviour     -- NullPromotion decide()->None
  2. test_m_basis_scalar_fold_must_be_red      -- conjunctive gate, no scalar fold
  3. test_budget_truncation_leaves_a_trail     -- budget_truncated denial留痕
  4. test_every_denial_leaves_a_trail          -- all deny_reasons留痕 (no silent edge)
  5. test_g1_freeze_is_bit_identical           -- freeze knowledge -> identical decision
  6. test_g1_reverse_claim_changes_predictably -- flip claim -> candidate drops
  7. test_topk_tie_is_deterministic            -- modifier 1, R3 P0 same-shape

plus modifier-3 legal-quiet and the emission-schema guards.
"""

from __future__ import annotations

import pytest

from expos.kernel.knowledge import compile_knowledge
from expos.kernel.objects import HypothesisObject, HypothesisStatus
from expos.kernel.store import RunStore
from expos.planner.promotion import (
    DENY_BUDGET_TRUNCATED,
    DENY_DRY_FAILED,
    DENY_GATE_CONVERGENCE,
    DENY_GATE_RANK,
    DENY_GATE_RISK,
    DENY_GATE_WINDOW,
    ChannelBasis,
    DryCandidateView,
    EvidenceGatedPromotion,
    NullPromotion,
    PromotionBudget,
    PromotionError,
    WetCostEstimate,
    decide,
    emit_promotion_decision,
)

_FP = "deadbeef" * 8  # a stand-in knowledge fingerprint for the non-G1 tests
_COST = WetCostEstimate(n_transfers=2, duration_s=10.0)


def _cand(cid, *, converged=True, in_window=True, acq=1.0, cost=_COST,
          failure_detail=None):
    return DryCandidateView(
        cand_id=cid, converged=converged, in_window=in_window, acquisition=acq,
        wet_cost=cost, failure_detail=failure_detail,
    )


def _promoted_ids(decision):
    return [p.cand_id for p in decision.promoted]


def _deny_reason(decision, cid):
    return {d.cand_id: d.deny_reason for d in decision.denied}[cid]


# ============================================================ 1. NullPromotion

def test_null_promotion_is_zero_behaviour():
    """NullPromotion (every existing arm's sixth element) engages no mechanism:
    decide()->None regardless of inputs -> loop emits nothing -> non-M16 runs are
    byte-for-byte unchanged."""
    pol = NullPromotion()
    assert pol.decide([_cand("cand-a")], None, _FP,
                      PromotionBudget(top_k=1)) is None
    assert pol.decide([], {"x": 0.9}, _FP, PromotionBudget()) is None


# ============================================================ 2. M-basis scalar fold

def test_m_basis_scalar_fold_must_be_red():
    """The four channels are a CONJUNCTIVE gate, never a weighted scalar
    (design point 3, letter 046 legislation). Each candidate below fails exactly
    ONE hard channel while pegging every other channel to its most-promotable
    value (huge acquisition, in-window, zero risk). A scalar fold
    (w1*conv + w2*win + ... >= thr) would let the huge acquisition BUY BACK the
    failed gate and promote it; the conjunctive gate denies each. Asserting each
    is denied kills any scalar-fold mutant.
    """
    budget = PromotionBudget(top_k=10, risk_threshold=0.5)
    huge = 1e9
    dry = [
        _cand("no-converge", converged=False, acq=huge),  # fails convergence only
        _cand("no-window", in_window=False, acq=huge),    # fails window only
    ]
    # risk gate: risk 0.99 > threshold 0.5, everything else maximal
    risk_map = {"hi-risk": 0.99}
    dry.append(_cand("hi-risk", acq=huge))

    d = decide(dry, risk_map, _FP, budget)
    assert _promoted_ids(d) == [], "a failed hard gate must not be bought back by acquisition"
    assert _deny_reason(d, "no-converge") == DENY_GATE_CONVERGENCE
    assert _deny_reason(d, "no-window") == DENY_GATE_WINDOW
    assert _deny_reason(d, "hi-risk") == DENY_GATE_RISK
    # basis records the four channels verbatim (audit reconstructs the gate)
    conv_basis = {d0.cand_id: d0.basis for d0 in d.denied}["no-converge"]
    assert isinstance(conv_basis, ChannelBasis)
    assert conv_basis.convergence == 0.0 and conv_basis.window == 1.0


# ============================================================ 3. budget truncation

def test_budget_truncation_leaves_a_trail():
    """Cost ceiling truncates in acquisition-rank order; every truncated
    candidate is denied budget_truncated WITH its wet_cost estimate (modifier 2:
    'why did the cut land at k' is reconstructable from the event)."""
    dry = [
        _cand("a", acq=3.0, cost=WetCostEstimate(2, 10.0)),
        _cand("b", acq=2.0, cost=WetCostEstimate(2, 10.0)),
        _cand("c", acq=1.0, cost=WetCostEstimate(2, 10.0)),
    ]
    # ceiling admits the first two transfers-batches (4) but not the third (6)
    budget = PromotionBudget(max_transfers_total=4)
    d = decide(dry, None, _FP, budget)
    assert _promoted_ids(d) == ["a", "b"]
    assert _deny_reason(d, "c") == DENY_BUDGET_TRUNCATED
    denied_c = {x.cand_id: x for x in d.denied}["c"]
    assert denied_c.wet_cost.n_transfers == 2  # cost estimate rides into the trail


def test_topk_hard_cap_denies_gate_rank():
    """top_k is the hard candidate-count cap (the top-k channel gate): survivors
    beyond rank k are denied gate_rank (distinct deny_reason from cost cut)."""
    dry = [_cand("a", acq=3.0), _cand("b", acq=2.0), _cand("c", acq=1.0)]
    d = decide(dry, None, _FP, PromotionBudget(top_k=2))
    assert _promoted_ids(d) == ["a", "b"]
    assert _deny_reason(d, "c") == DENY_GATE_RANK


# ============================================================ 4. denied all留痕

def test_every_denial_leaves_a_trail():
    """No silent edge (design point 5 / G4): every deny_reason surfaces, incl.
    dry_failed which consumes the scheduler failure_detail channel (design
    point 8). promoted + denied partition the input candidate set exactly."""
    dry = [
        _cand("ok", acq=5.0),
        _cand("failed", failure_detail={"reason": "scf_diverged"}),
        _cand("no-conv", converged=False),
        _cand("no-win", in_window=False),
        _cand("risky", acq=4.0),
        _cand("ranked-out", acq=0.1),
    ]
    budget = PromotionBudget(top_k=1, risk_threshold=0.5)
    d = decide(dry, {"risky": 0.9}, _FP, budget)

    assert _promoted_ids(d) == ["ok"]
    reasons = {x.cand_id: x.deny_reason for x in d.denied}
    assert reasons["failed"] == DENY_DRY_FAILED
    assert reasons["no-conv"] == DENY_GATE_CONVERGENCE
    assert reasons["no-win"] == DENY_GATE_WINDOW
    assert reasons["risky"] == DENY_GATE_RISK
    assert reasons["ranked-out"] == DENY_GATE_RANK
    # exact partition: no candidate silently vanishes
    seen = set(_promoted_ids(d)) | set(reasons)
    assert seen == {c.cand_id for c in dry}


def test_dry_failed_dominates_other_gates():
    """A FAILED/TIMEOUT dry leg is not evidence: failure_detail dominates even a
    non-converged / out-of-window candidate (single deny_reason=dry_failed)."""
    d = decide([_cand("x", converged=False, in_window=False,
                       failure_detail={"reason": "timeout"})],
               None, _FP, PromotionBudget(top_k=5))
    assert _deny_reason(d, "x") == DENY_DRY_FAILED


# ============================================================ 5+6. G1 hooks

def _hyp(hid, refs):
    return HypothesisObject(hypothesis_id=hid, statement=f"h {hid}",
                            status=HypothesisStatus.OPEN, evidence_refs=refs)


def _dry_view_from_knowledge(view):
    """Model the dry leg being RE-STEERED by compiled knowledge (G1): a
    hypothesis whose effective status is REJECTED culls its candidate (treated as
    non-converged); otherwise the candidate is converged. This is the promotion
    analogue of the knowledge-consumption discriminator."""
    return [
        _cand(h.hypothesis_id,
              converged=(h.effective_status != HypothesisStatus.REJECTED),
              acq=1.0)
        for h in view.hypotheses
    ]


def test_g1_freeze_is_bit_identical():
    """Freeze the knowledge -> two compiles give a bit-identical fingerprint ->
    decide() gives a bit-identical PromotionDecision (frozen dataclass equality),
    and the decision records that exact fingerprint. Reordering the input
    candidates must not change the decision (caller-order-invariance)."""
    claims = [{"claim_id": "c1", "status": "supported"}]
    hyps = [_hyp("hyp-a", ["c1"]), _hyp("hyp-b", ["c1"])]
    view1 = compile_knowledge(claims, hyps)
    view2 = compile_knowledge(list(claims), list(reversed(hyps)))
    assert view1.knowledge_fingerprint == view2.knowledge_fingerprint

    budget = PromotionBudget(top_k=5)
    dv1 = _dry_view_from_knowledge(view1)
    d1 = decide(dv1, None, view1.knowledge_fingerprint, budget)
    d2 = decide(list(reversed(dv1)), None, view2.knowledge_fingerprint, budget)
    assert d1 == d2
    assert d1.knowledge_fingerprint == view1.knowledge_fingerprint


def test_g1_reverse_claim_changes_predictably():
    """Inject a CONTRARY claim (supported -> rejected) referenced by hyp-a: the
    fingerprint changes, hyp-a's effective status moves SUPPORTED -> REJECTED,
    and its candidate provably DROPS from the promoted set (deny_reason
    gate_convergence). No performative feedback -- the reverse claim re-steers the
    gate, C2 lesson."""
    hyps = [_hyp("hyp-a", ["c1"]), _hyp("hyp-b", ["c2"])]
    view_pos = compile_knowledge(
        [{"claim_id": "c1", "status": "supported"},
         {"claim_id": "c2", "status": "supported"}], hyps)
    view_neg = compile_knowledge(
        [{"claim_id": "c1", "status": "rejected"},   # <-- the contrary claim
         {"claim_id": "c2", "status": "supported"}], hyps)

    assert view_pos.knowledge_fingerprint != view_neg.knowledge_fingerprint

    budget = PromotionBudget(top_k=5)
    d_pos = decide(_dry_view_from_knowledge(view_pos), None,
                   view_pos.knowledge_fingerprint, budget)
    d_neg = decide(_dry_view_from_knowledge(view_neg), None,
                   view_neg.knowledge_fingerprint, budget)

    assert "hyp-a" in _promoted_ids(d_pos)
    assert "hyp-a" not in _promoted_ids(d_neg)
    assert _deny_reason(d_neg, "hyp-a") == DENY_GATE_CONVERGENCE
    # the recorded witness tracks the consumed knowledge state
    assert d_neg.knowledge_fingerprint == view_neg.knowledge_fingerprint


def test_decide_refuses_empty_fingerprint():
    """The G1 hook is explicit consumption: decide() loud-fails without a
    knowledge witness (no silent default)."""
    with pytest.raises(PromotionError):
        decide([_cand("a")], None, "", PromotionBudget(top_k=1))


# ============================================================ 7. tie determinism

def test_topk_tie_is_deterministic():
    """MODIFIER 1 (letter 055, load-bearing). Two candidates with BYTE-IDENTICAL
    acquisition scores compete for a single top-k slot. The winner must be fixed
    by the explicit secondary key (cand_id ascending), NEVER by enumeration /
    insertion order.

    Same-shape典故: this is the M16 promotion analogue of the project's most
    expensive historical bug — R3 P0 batch-direction inversion, whose root cause
    was a symmetric tie-break (``max(batch_shifts, key=abs)`` with
    ``shift[B0] == -shift[B1]``) falling to dict/insertion order and thus ALWAYS
    picking B0 (docs/STRESS_TEST_R3.md §1.1). A promotion gate is the same shape:
    when two candidates' acquisition scores are exactly tied, who gets the wet
    well must not depend on the order the caller happened to enumerate them.
    """
    budget = PromotionBudget(top_k=1)
    a = _cand("cand-a", acq=0.5)
    b = _cand("cand-b", acq=0.5)  # byte-identical acquisition

    d_ab = decide([a, b], None, _FP, budget)
    d_ba = decide([b, a], None, _FP, budget)  # reversed enumeration

    # order-independent AND resolves to the lexicographically-first cand_id
    assert _promoted_ids(d_ab) == _promoted_ids(d_ba) == ["cand-a"]
    assert _deny_reason(d_ab, "cand-b") == DENY_GATE_RANK
    assert _deny_reason(d_ba, "cand-b") == DENY_GATE_RANK


# ============================================================ modifier 3 + emission

def test_zero_promotion_is_legal_quiet_and_still_emits(tmp_path):
    """MODIFIER 3: promoted==[] is a LEGAL, loudly-recorded result. A
    zero-promotion round (here top_k=0 denies every survivor gate_rank) still
    produces a promotion_decision event with promoted==[]. The ABSENCE of the
    event is the suspicious thing, not an empty list (legal-quiet vs dead)."""
    d = decide([_cand("a", acq=1.0), _cand("b", acq=2.0)], None, _FP,
               PromotionBudget(top_k=0))
    assert _promoted_ids(d) == []
    assert len(d.denied) == 2  # both denied gate_rank -- loudly recorded

    store = RunStore(tmp_path / "run", create=True)
    emit_promotion_decision(store, round_id=3, decision=d)
    events = store.read_events("promotion_decision")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["promoted"] == []       # legal-quiet, present not absent
    assert len(payload["denied"]) == 2


def test_emitted_payload_satisfies_required_keys(tmp_path):
    """The promotion_decision event carries the registered required keys
    (store.EVENT_PAYLOAD_REQUIRED) so validate_event_payloads is clean, and each
    denied entry carries basis + deny_reason + wet_cost (design point 5 +
    modifier 2)."""
    d = decide([_cand("a", acq=2.0), _cand("bad", converged=False)], None, _FP,
               PromotionBudget(top_k=5))
    store = RunStore(tmp_path / "run", create=True)
    ev = emit_promotion_decision(store, round_id=0, decision=d)

    for key in ("round_id", "knowledge_fingerprint", "promoted", "denied"):
        assert key in ev["payload"]
    assert store.validate_event_payloads([ev]) == []

    denied = {x["cand_id"]: x for x in ev["payload"]["denied"]}["bad"]
    assert denied["deny_reason"] == DENY_GATE_CONVERGENCE
    assert set(denied["basis"]) == {"convergence", "window", "acquisition_rank", "risk"}
    assert set(denied["wet_cost"]) == {"n_transfers", "duration_s"}


def test_evidence_gated_policy_wraps_pure_decide():
    """EvidenceGatedPromotion.decide is the pure decide() (always a decision,
    even empty); it is the M16 injection the W9 mcl wiring swaps in for
    NullPromotion."""
    pol = EvidenceGatedPromotion()
    d = pol.decide([_cand("a", acq=1.0)], None, _FP, PromotionBudget(top_k=1))
    assert d is not None
    assert _promoted_ids(d) == ["a"]
    assert d.policy == "evidence_gated"
