"""M17 K-A — online claim-ledger path: ClaimDelta schema + apply_claim_deltas.

Discriminative-first: every governance guard has a test that turns red if the
guard is removed (kill-verification noted inline). Gates exercised:
  * strength-monotonicity — weak evidence must not retract a strong conclusion;
  * insufficient never mutates effective status (K3), yet lands a traceable record;
  * supersede retains the old version, closes the bidirectional chain, and moves
    the compiled knowledge_fingerprint (K2 substrate);
  * append-only — rewriting an existing version in place fails loudly;
  * decision_fn registration/version legality (governance red line 1);
  * determinism — same batch, bitwise-identical serialized ledger (K5);
  * provenance snapshot round-trip preserves the fingerprint set exactly (K4);
  * effective status is DERIVED by replay (no in-place status mutation).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from expos.kernel.claims import (
    DENY_DECISION_FN_VERSION_MISMATCH,
    DENY_UNREGISTERED_DECISION_FN,
    DENY_WEAK_CANNOT_RETRACT_STRONG,
    GATE_DISPOSITION_DEGRADE,
    GATE_DISPOSITION_REJECT,
    REFERENCE_CERTIFICATION_FN_ID,
    REFERENCE_CERTIFICATION_FN_VERSION,
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimLedgerError,
    ClaimRecord,
    ClaimVersionContent,
    EvidenceStrength,
    Ledger,
    ObservationFingerprint,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    StatisticSnapshot,
    add_claim_record,
    apply_claim_deltas,
    emit_claim_decision,
    evaluate_gate,
    ledger_to_claim_dicts,
)
from expos.kernel.knowledge import compile_knowledge
from expos.kernel.objects import HypothesisObject
from expos.kernel.store import RunStore

SUP = ClaimDecisionStatus.SUPPORTED
REJ = ClaimDecisionStatus.REJECTED
QUAL = ClaimDecisionStatus.QUALIFIED
INSUF = ClaimDecisionStatus.INSUFFICIENT

NONE = EvidenceStrength.NONE
WEAK = EvidenceStrength.WEAK
MODERATE = EvidenceStrength.MODERATE
STRONG = EvidenceStrength.STRONG
VERY_STRONG = EvidenceStrength.VERY_STRONG


# ---------------------------------------------------------------- builders


def _prov(
    *,
    decision_fn_id: str = REFERENCE_CERTIFICATION_FN_ID,
    decision_fn_version: str = REFERENCE_CERTIFICATION_FN_VERSION,
    obs: tuple[tuple[str, str], ...] = (("obs_1", "fp_1"),),
    kfp: str = "kfp_old",
    criterion: str = "crit_v1",
    **stat: object,
) -> ProvenanceSnapshot:
    return ProvenanceSnapshot(
        usage=ProvenanceUsage(
            observations=tuple(
                ObservationFingerprint(obs_id=o, content_fingerprint=f) for o, f in obs
            ),
            consumed_knowledge_fingerprint=kfp,
        ),
        activity=ProvenanceActivity(
            decision_fn_id=decision_fn_id,
            decision_fn_version=decision_fn_version,
            criterion_version=criterion,
        ),
        statistic=StatisticSnapshot(**stat),
    )


def _delta(
    target: str,
    status: ClaimDecisionStatus,
    strength: EvidenceStrength,
    *,
    statement: str = "stmt",
    **prov_kw: object,
) -> ClaimDelta:
    content = (
        None
        if status is INSUF
        else ClaimVersionContent(statement=statement, status=status)
    )
    return ClaimDelta(
        target_claim_id=target,
        status=status,
        new_content=content,
        evidence_strength=strength,
        provenance=_prov(**prov_kw),
    )


def _head(
    claim_id: str,
    status: ClaimDecisionStatus,
    strength: EvidenceStrength,
    version: int = 1,
) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        version=version,
        status=status,
        statement="seed",
        evidence_strength=strength,
        provenance=_prov(),
    )


# ---------------------------------------------------------------- strength gate


def test_weak_evidence_cannot_retract_strong_claim():
    """Weak-evidence delta trying to reject a STRONG claim -> rejected with an
    explicit deny_reason; the head is untouched.

    KILL: delete the strength-monotonicity gate in _apply_one and the reject
    supersedes the head (effective status becomes REJECTED) -> the two asserts
    below turn red."""
    seed = Ledger(claims=(_head("c1", SUP, STRONG),))
    new, report = apply_claim_deltas(seed, [_delta("c1", REJ, WEAK)])

    assert new.effective_statuses()["c1"] is SUP  # head unchanged
    assert new.head("c1").evidence_strength is STRONG
    assert len(report.rejected) == 1
    assert report.rejected[0].deny_reason == DENY_WEAK_CANNOT_RETRACT_STRONG
    assert report.rejected[0].mutated_effective_status is False
    # degraded (never silent): a traceable annotation landed, but not a head.
    ann = [r for r in new.claims if r.is_annotation]
    assert len(ann) == 1 and ann[0].deny_reason == DENY_WEAK_CANNOT_RETRACT_STRONG


def test_equal_or_stronger_evidence_may_supersede():
    """The gate allows supersede when band >= target band (ordinal). Equal band
    passes; the head moves."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    new, report = apply_claim_deltas(seed, [_delta("c1", REJ, MODERATE)])
    assert new.effective_statuses()["c1"] is REJ
    assert report.applied[0].mutated_effective_status is True


# ---------------------------------------------------------------- insufficient (K3)


def test_insufficient_does_not_mutate_but_is_traceable():
    """insufficient never changes effective status, yet lands a traceable record
    carrying full provenance.

    KILL: route insufficient into the mutating branch and effective status would
    change -> the first assert turns red."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    new, report = apply_claim_deltas(seed, [_delta("c1", INSUF, NONE)])

    assert new.effective_statuses()["c1"] is SUP  # unchanged (K3)
    ann = [r for r in new.claims if r.is_annotation]
    assert len(ann) == 1 and ann[0].status is INSUF
    assert ann[0].provenance.usage.observations[0].obs_id == "obs_1"  # traceable
    assert report.applied[0].mutated_effective_status is False
    assert report.applied[0].landed_record_version == ann[0].version


def test_insufficient_delta_is_type_isolated_from_new_content():
    """TYPE-level isolation (sciunit InsufficientDataScore precedent): an
    insufficient delta structurally cannot carry a new claim version."""
    with pytest.raises(ValidationError):
        ClaimDelta(
            target_claim_id="c1",
            status=INSUF,
            new_content=ClaimVersionContent(statement="x", status=INSUF),
            evidence_strength=NONE,
            provenance=_prov(),
        )
    # symmetric guard: a mutating delta MUST carry new_content.
    with pytest.raises(ValidationError):
        ClaimDelta(
            target_claim_id="c1",
            status=SUP,
            new_content=None,
            evidence_strength=MODERATE,
            provenance=_prov(),
        )


# ---------------------------------------------------------------- supersede (K2)


def test_supersede_retains_old_closes_chain_and_moves_fingerprint():
    """Supersede: old version retained untouched, bidirectional chain closed, and
    compile_knowledge on the new ledger yields a DIFFERENT fingerprint (K2)."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    hyp = HypothesisObject(hypothesis_id="h1", statement="s", evidence_refs=["c1"])
    before = compile_knowledge(ledger_to_claim_dicts(seed), [hyp])

    new, _ = apply_claim_deltas(seed, [_delta("c1", REJ, STRONG)])

    recs = {r.version: r for r in new.claims if r.claim_id == "c1"}
    assert set(recs) == {1, 2}  # old retained + new appended
    assert recs[1].status is SUP  # old content never rewritten (append-only)
    # bidirectional chain: forward pointer derived from the immutable back-pointer.
    assert recs[2].supersedes == 1
    assert new.superseded_by("c1", 1) == 2
    assert new.head("c1").version == 2 and new.head("c1").status is REJ

    after = compile_knowledge(ledger_to_claim_dicts(new), [hyp])
    assert before.knowledge_fingerprint != after.knowledge_fingerprint  # K2 substrate


def test_new_claim_origin_creates_head():
    seed = Ledger()
    new, report = apply_claim_deltas(seed, [_delta("c_new", SUP, MODERATE)])
    assert new.head("c_new").version == 1
    assert new.head("c_new").supersedes is None
    assert report.applied[0].mutated_effective_status is True


# ---------------------------------------------------------------- append-only


def test_append_only_rewrite_in_place_fails_loudly():
    """Rewriting an existing (claim_id, version) with different content is an
    append-only violation.

    KILL: drop the collision check in _add_record and this stops raising -> red."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    tamper = ClaimRecord(
        claim_id="c1",
        version=1,  # same key, different content
        status=REJ,
        statement="rewritten",
        evidence_strength=MODERATE,
        provenance=_prov(),
    )
    with pytest.raises(ClaimLedgerError):
        add_claim_record(seed, tamper)


# ---------------------------------------------------------------- decision_fn gate


def test_unregistered_decision_fn_is_rejected_loudly():
    """A delta whose decision_fn is not in the shared registry is denied; no
    record lands and the head is untouched.

    KILL: remove the registration gate in _apply_one and the reject supersedes
    the head -> effective status changes and landed_record_version is not None."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    new, report = apply_claim_deltas(
        seed, [_delta("c1", REJ, STRONG, decision_fn_id="ghost_fn")]
    )
    assert report.rejected[0].deny_reason == DENY_UNREGISTERED_DECISION_FN
    assert report.rejected[0].landed_record_version is None
    assert new.effective_statuses()["c1"] is SUP  # unchanged, nothing landed


def test_decision_fn_version_mismatch_is_rejected():
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    _, report = apply_claim_deltas(
        seed, [_delta("c1", REJ, STRONG, decision_fn_version="999")]
    )
    assert report.rejected[0].deny_reason == DENY_DECISION_FN_VERSION_MISMATCH


# ---------------------------------------------------------------- determinism (K5)


def test_apply_is_bitwise_deterministic_and_order_insensitive():
    seed = Ledger(claims=(_head("c1", SUP, MODERATE), _head("c2", SUP, WEAK)))
    deltas = [
        _delta("c2", REJ, STRONG, statement="a"),
        _delta("c1", QUAL, MODERATE, statement="b"),
        _delta("c1", INSUF, NONE),
    ]
    n1, _ = apply_claim_deltas(seed, deltas)
    n2, _ = apply_claim_deltas(seed, deltas)
    assert n1.canonical_json() == n2.canonical_json()  # same batch -> bitwise-identical
    # explicit deterministic sort key -> caller list order cannot change the result.
    n3, _ = apply_claim_deltas(seed, list(reversed(deltas)))
    assert n1.canonical_json() == n3.canonical_json()


# ---------------------------------------------------------------- provenance (K4)


def test_provenance_snapshot_roundtrip_preserves_fingerprint_set():
    prov = _prov(
        obs=(("obs_a", "fp_a"), ("obs_b", "fp_b")),
        kfp="kfp_123",
        statistic_name="mean_diff",
        statistic_value=2.675,
        test_method="paired_permutation",
        p_value=1e-4,
        effect_estimate=2.675,
        effect_se=0.4,
        evidence_factor=42.0,
        independence_assumed=True,
        seed=7,
    )
    back = ProvenanceSnapshot.model_validate_json(prov.model_dump_json())
    assert back == prov
    assert back.fingerprint() == prov.fingerprint()
    # fingerprint SET preserved exactly (K1 substitution-audit substrate).
    assert {(o.obs_id, o.content_fingerprint) for o in back.usage.observations} == {
        ("obs_a", "fp_a"),
        ("obs_b", "fp_b"),
    }


# ---------------------------------------------------------------- derived status


def test_effective_status_is_derived_by_replay():
    """Effective status is a pure function of the append-only record set (no
    in-place status mutation): replaying the same records reproduces it bitwise,
    and every superseded version is retained untouched."""
    seed = Ledger(claims=(_head("c1", SUP, WEAK),))
    l1, _ = apply_claim_deltas(seed, [_delta("c1", REJ, STRONG, statement="r")])
    l2, _ = apply_claim_deltas(l1, [_delta("c1", SUP, VERY_STRONG, statement="s2")])

    assert l2.effective_statuses() == {"c1": SUP}  # last stronger supersede wins
    assert sorted(r.version for r in l2.claims if r.claim_id == "c1") == [1, 2, 3]
    assert l2.head("c1").version == 3

    # derived-status principle end to end: recompute from the record set alone.
    fresh = Ledger(claims=l2.claims)
    assert fresh.effective_statuses() == l2.effective_statuses()
    assert fresh.canonical_json() == l2.canonical_json()

    # append-only: the seed version's status was never rewritten in place.
    v1 = next(r for r in l2.claims if r.claim_id == "c1" and r.version == 1)
    assert v1.status is SUP and v1.supersedes is None


# ---------------------------------------------------------------- declarative gate


def test_evaluate_gate_returns_conforms_report_with_stable_codes():
    """The legality gate is declarative: it returns (conforms, report) with stable
    violation codes and dispositions, not scattered imperative ifs."""
    head = _head("c1", SUP, STRONG)

    ok = evaluate_gate(_delta("c1", SUP, VERY_STRONG), head)
    assert ok.conforms is True and ok.violations == ()

    weak = evaluate_gate(_delta("c1", REJ, WEAK), head)
    assert weak.conforms is False
    assert weak.first(GATE_DISPOSITION_DEGRADE).code == DENY_WEAK_CANNOT_RETRACT_STRONG

    ghost = evaluate_gate(_delta("c1", REJ, VERY_STRONG, decision_fn_id="ghost"), head)
    assert ghost.first(GATE_DISPOSITION_REJECT).code == DENY_UNREGISTERED_DECISION_FN


# ---------------------------------------------------------------- emit helper (K4 keys)


def test_emit_claim_decision_honours_required_keys(tmp_path):
    store = RunStore(tmp_path / "run")
    delta = _delta("c1", REJ, STRONG, statement="r")

    rec = emit_claim_decision(
        store, round_id=1, delta=delta, final_status=REJ, landed_version=2
    )
    assert rec["kind"] == "claim_decision"
    assert rec["payload"]["claim_id"] == "c1"
    assert rec["payload"]["decision_status"] == "rejected"
    assert rec["payload"]["decision_fn_id"] == REFERENCE_CERTIFICATION_FN_ID
    assert rec["payload"]["input_observation_ids"] == ["obs_1"]

    events = store.read_events(kind="claim_decision")
    assert len(events) == 1
    # required-key registration honoured (no payload-structure violation).
    assert store.validate_event_payloads(events) == []
