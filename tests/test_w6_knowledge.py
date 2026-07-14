"""M16 W6 knowledge face: compile_knowledge / KnowledgeView / emit helper.

Discriminative tests for acceptance gate G1 (docs/M16_MIN_LOOP.md §0): the
knowledge fingerprint is a deterministic, order-insensitive function of the
compiled knowledge, and a reverse claim provably re-steers a hypothesis.
"""

from __future__ import annotations

import pytest

from expos.kernel.knowledge import (
    CLAIM_STATUS_MISSING,
    KnowledgeView,
    compile_knowledge,
    emit_knowledge_updated,
)
from expos.kernel.objects import HypothesisObject, HypothesisStatus
from expos.kernel.store import RunStore


def _claim(claim_id: str, status: str) -> dict:
    return {"claim_id": claim_id, "status": status}


def _hyp(hid: str, refs: list[str], status: HypothesisStatus = HypothesisStatus.OPEN):
    return HypothesisObject(hypothesis_id=hid, statement=f"stmt {hid}",
                            status=status, evidence_refs=refs)


# ---------------------------------------------------------------- determinism

def test_fingerprint_is_deterministic_bit_for_bit():
    claims = [_claim("c1", "supported"), _claim("c2", "rejected")]
    hyps = [_hyp("h1", ["c1"]), _hyp("h2", ["c2"])]

    v1 = compile_knowledge(claims, hyps)
    v2 = compile_knowledge(claims, hyps)

    # G1 substrate: identical inputs -> bit-for-bit identical fingerprint.
    assert v1.knowledge_fingerprint == v2.knowledge_fingerprint
    assert v1.model_dump() == v2.model_dump()


def test_fingerprint_is_input_order_insensitive():
    claims = [_claim("c1", "supported"), _claim("c2", "supported")]
    hyps = [_hyp("h1", ["c1", "c2"]), _hyp("h2", ["c2"])]

    forward = compile_knowledge(claims, hyps)
    # shuffle claim list, hypothesis list, and per-hypothesis ref order
    shuffled = compile_knowledge(
        list(reversed(claims)),
        [_hyp("h2", ["c2"]), _hyp("h1", ["c2", "c1"])],
    )
    assert forward.knowledge_fingerprint == shuffled.knowledge_fingerprint


# ------------------------------------------------ reverse-claim discriminative

def test_reverse_claim_flips_hypothesis_and_fingerprint():
    """Inject a reverse claim (supported -> rejected): the referenced
    hypothesis moves SUPPORTED -> REJECTED and the fingerprint changes."""
    hyps = [_hyp("h1", ["c1"])]

    supported = compile_knowledge([_claim("c1", "supported")], hyps)
    rejected = compile_knowledge([_claim("c1", "rejected")], hyps)

    h_sup = supported.hypotheses[0]
    h_rej = rejected.hypotheses[0]
    assert h_sup.effective_status is HypothesisStatus.SUPPORTED
    assert h_rej.effective_status is HypothesisStatus.REJECTED
    # G1: the reverse claim is provably consumed (fingerprint changed).
    assert supported.knowledge_fingerprint != rejected.knowledge_fingerprint


def test_refuting_evidence_dominates():
    hyps = [_hyp("h1", ["c1", "c2"])]
    view = compile_knowledge(
        [_claim("c1", "supported"), _claim("c2", "rejected")], hyps
    )
    assert view.hypotheses[0].effective_status is HypothesisStatus.REJECTED


def test_no_informative_evidence_stays_open():
    hyps = [_hyp("h1", ["c1", "c2"])]
    view = compile_knowledge(
        [_claim("c1", "partially_supported"), _claim("c2", "stale")], hyps
    )
    assert view.hypotheses[0].effective_status is HypothesisStatus.OPEN


def test_missing_claim_is_uninformative():
    hyps = [_hyp("h1", ["c_absent"])]
    view = compile_knowledge([_claim("c1", "supported")], hyps)
    ev = view.hypotheses[0].evidence[0]
    assert ev.status == CLAIM_STATUS_MISSING
    assert view.hypotheses[0].effective_status is HypothesisStatus.OPEN


def test_superseded_is_sticky():
    """A superseded hypothesis is never recomputed from claims."""
    hyps = [_hyp("h1", ["c1"], status=HypothesisStatus.SUPERSEDED)]
    view = compile_knowledge([_claim("c1", "supported")], hyps)
    assert view.hypotheses[0].effective_status is HypothesisStatus.SUPERSEDED


# ------------------------------------------------------------------ shape/guards

def test_counts_reported_but_unreferenced_claim_does_not_move_fingerprint():
    hyps = [_hyp("h1", ["c1"])]
    base = compile_knowledge([_claim("c1", "supported")], hyps)
    extra = compile_knowledge(
        [_claim("c1", "supported"), _claim("c_extra", "rejected")], hyps
    )
    # n_claims count reflects the extra claim ...
    assert base.n_claims == 1 and extra.n_claims == 2
    # ... but knowledge (and thus the fingerprint) is unchanged: the extra claim
    # is referenced by no hypothesis.
    assert base.knowledge_fingerprint == extra.knowledge_fingerprint


def test_view_is_frozen():
    view = compile_knowledge([_claim("c1", "supported")], [_hyp("h1", ["c1"])])
    assert isinstance(view, KnowledgeView)
    with pytest.raises(Exception):
        view.knowledge_fingerprint = "tampered"  # frozen -> raises


def test_malformed_claim_fails_loudly():
    with pytest.raises(ValueError):
        compile_knowledge([{"claim_id": "c1"}], [_hyp("h1", ["c1"])])


# ------------------------------------------------------------------ emit helper

def test_emit_knowledge_updated(tmp_path):
    store = RunStore(tmp_path / "run")
    view = compile_knowledge([_claim("c1", "supported")], [_hyp("h1", ["c1"])])

    rec = emit_knowledge_updated(store, view, round_id=0)
    assert rec["kind"] == "knowledge_updated"
    assert rec["payload"]["fingerprint"] == view.knowledge_fingerprint
    assert rec["payload"]["n_hypotheses"] == 1
    assert rec["payload"]["n_claims"] == 1

    events = store.read_events(kind="knowledge_updated")
    assert len(events) == 1
    # required-key registration honoured (no payload-structure violation).
    assert store.validate_event_payloads(events) == []
