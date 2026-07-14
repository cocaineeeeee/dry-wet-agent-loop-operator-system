"""P4 + P5 — claim-ledger determinism properties (M22 property-test batch).

Both properties drive the REAL kernel ledger APIs (expos/kernel/claims.py) over
arbitrary generated ClaimDelta chains, upgrading the fixed-input K5 determinism
gate to arbitrary-input proof.

P4 — canonical_json insertion-order invariance (the K5 witness on arbitrary
input):

  * within-round: ``apply_claim_deltas`` sorts its batch by the explicit total
    ``_delta_sort_key``, so ANY permutation of the delta list yields a
    bit-for-bit identical ledger ``canonical_json``;
  * across independent claims: applying per-claim delta GROUPS (distinct
    claim_ids) as sequential rounds in any GROUP order yields identical
    canonical_json — independent claims never interact, so their application
    order cannot change the ledger bytes.

P5 — ``effective_statuses`` equals an independent replay. The kernel derives a
claim's effective status by set-differencing superseded versions
(``_current_head``); this test derives it by a structurally different FORWARD
walk of the supersede chain (root -> follow the inverted ``supersedes`` pointer to
the terminal head) and asserts agreement over generated chains that include the
degrade (weak-cannot-retract-strong) and insufficient (non-mutating annotation)
paths.

Deltas are built against the honest-null reference decision fn
(``reference_round_certification``), which is registered in the shared
``DECISION_FN_REGISTRY`` at import — so every generated delta passes the online
governance legality gate. Determinism is pinned with ``derandomize=True`` +
``database=None``.
"""
from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("hypothesis") is None:  # graceful skip w/o dev extra
    pytest.skip("hypothesis not installed (pip install -e '.[dev]')",
                allow_module_level=True)

from hypothesis import given, settings
from hypothesis import strategies as st

from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimVersionContent,
    EvidenceStrength,
    Ledger,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    REFERENCE_CERTIFICATION_FN_ID,
    REFERENCE_CERTIFICATION_FN_VERSION,
    _current_head,
    apply_claim_deltas,
)

_CLAIM_IDS = ["c0", "c1", "c2"]


@st.composite
def _delta(draw, claim_ids: list[str] = _CLAIM_IDS) -> ClaimDelta:
    """One legal ClaimDelta targeting a claim from ``claim_ids``. The decision_fn is
    the registered honest-null reference fn (passes the legality gate); status and
    evidence band are free so the generated space spans mutating / insufficient /
    degrade outcomes. Provenance is deterministic in the drawn fields (no clock, no
    randomness) so ``_delta_sort_key`` / ``canonical_json`` are stable."""
    claim_id = draw(st.sampled_from(claim_ids))
    status = draw(st.sampled_from(list(ClaimDecisionStatus)))
    strength = draw(st.sampled_from(list(EvidenceStrength)))
    statement = draw(st.text(max_size=12))
    kfp = draw(st.text(max_size=8))
    new_content = (
        None
        if status is ClaimDecisionStatus.INSUFFICIENT
        else ClaimVersionContent(statement=statement, status=status)
    )
    provenance = ProvenanceSnapshot(
        usage=ProvenanceUsage(consumed_knowledge_fingerprint=kfp),
        activity=ProvenanceActivity(
            decision_fn_id=REFERENCE_CERTIFICATION_FN_ID,
            decision_fn_version=REFERENCE_CERTIFICATION_FN_VERSION,
            criterion_version="v1",
        ),
    )
    return ClaimDelta(
        target_claim_id=claim_id,
        status=status,
        new_content=new_content,
        evidence_strength=strength,
        provenance=provenance,
    )


# ============================================================ P4 — order invariance


@settings(max_examples=300, deadline=2000, derandomize=True, database=None)
@given(deltas=st.lists(_delta(), max_size=10), data=st.data())
def test_apply_batch_permutation_invariance(deltas, data):
    """Within a single round: ANY permutation of the delta batch produces a
    bit-for-bit identical ledger (the K5 determinism witness, now over arbitrary
    input). ``apply_claim_deltas`` sorts by the explicit total ``_delta_sort_key``,
    so caller list order is never load-bearing."""
    permuted = data.draw(st.permutations(deltas))
    base, _ = apply_claim_deltas(Ledger(), list(deltas))
    other, _ = apply_claim_deltas(Ledger(), list(permuted))
    assert base.canonical_json() == other.canonical_json()


@settings(max_examples=300, deadline=3000, derandomize=True, database=None)
@given(deltas=st.lists(_delta(), max_size=12))
def test_independent_claim_group_order_invariance(deltas):
    """Across independent claims: partition the deltas into per-claim GROUPS
    (distinct claim_ids never interact — head/version derivation is per-claim) and
    apply the groups as sequential rounds. The FINAL ledger canonical_json is
    invariant to the order the groups are applied in, because each group only ever
    touches its own claim's records."""
    # Preserve within-group order; only the inter-group sequencing is permuted.
    groups: dict[str, list[ClaimDelta]] = {}
    for d in deltas:
        groups.setdefault(d.target_claim_id, []).append(d)

    def _apply_group_order(order: list[str]) -> str:
        ledger = Ledger()
        for claim_id in order:
            ledger, _ = apply_claim_deltas(ledger, groups[claim_id])
        return ledger.canonical_json()

    ids = list(groups)
    forward = _apply_group_order(ids)
    reverse = _apply_group_order(list(reversed(ids)))
    assert forward == reverse


# ============================================================ P5 — replay equivalence


def _naive_effective_statuses(
    ledger: Ledger,
) -> dict[str, ClaimDecisionStatus]:
    """Independent re-derivation of every claim's effective status, structurally
    DIFFERENT from the kernel's ``_current_head`` (which set-differences the
    superseded versions): here we FORWARD-walk the supersede chain from its root
    (the sole non-annotation record with ``supersedes is None``) along the inverted
    ``supersedes`` pointer to the terminal head. Annotation-only claims have no
    non-annotation record and inject no status (gate K3)."""
    out: dict[str, ClaimDecisionStatus] = {}
    for claim_id in {r.claim_id for r in ledger.claims}:
        heads = [
            r
            for r in ledger.claims
            if r.claim_id == claim_id and not r.is_annotation
        ]
        if not heads:
            continue  # only insufficient/degraded annotations -> no effective status
        by_version = {r.version: r for r in heads}
        forward = {
            r.supersedes: r.version for r in heads if r.supersedes is not None
        }
        roots = [r for r in heads if r.supersedes is None]
        # The append path always produces exactly one root and a linear chain: the
        # first non-annotation record created with supersedes=None; each later
        # mutating record supersedes the then-current head.
        assert len(roots) == 1, (
            f"claim {claim_id!r} has {len(roots)} chain roots — expected one "
            "(supersede chain must be a single linear chain from the apply path)"
        )
        cur = roots[0]
        seen = {cur.version}
        while cur.version in forward:
            nxt = forward[cur.version]
            assert nxt not in seen, f"claim {claim_id!r}: cycle in supersede chain"
            seen.add(nxt)
            cur = by_version[nxt]
        out[claim_id] = cur.status
    return out


@settings(max_examples=300, deadline=3000, derandomize=True, database=None)
@given(rounds=st.lists(st.lists(_delta(), max_size=4), max_size=6))
def test_effective_statuses_equals_naive_replay(rounds):
    """For a ledger built by a generated multi-round supersede/annotation chain,
    the kernel's ``effective_statuses`` (set-difference derivation) equals the
    independent forward-walk replay written above. The generated space spans
    supported/rejected/qualified heads, insufficient annotations, and the
    weak-cannot-retract-strong degrade path (weaker deltas land traceable
    annotations that never mutate the head)."""
    ledger = Ledger()
    for batch in rounds:
        ledger, _ = apply_claim_deltas(ledger, batch)

    kernel_derived = ledger.effective_statuses()
    naive = _naive_effective_statuses(ledger)
    assert kernel_derived == naive

    # Cross-check: every derived head status is exactly the head record's status,
    # and no claim with a head is dropped (structural head consistency).
    records = {(r.claim_id, r.version): r for r in ledger.claims}
    for claim_id, status in kernel_derived.items():
        head = _current_head(records, claim_id)
        assert head is not None and head.status is status
