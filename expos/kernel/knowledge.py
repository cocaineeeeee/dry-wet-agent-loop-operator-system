"""KnowledgeView compilation (M16 minimal knowledge face — no graph).

The knowledge face is a COMPILED product, never hand-authored: the agent reads
a KnowledgeView derived deterministically from (1) the claim ledger
(claims/ledger.json — see scripts/claim_compiler.py) and (2) the posed
HypothesisObjects. This is the machine substrate for acceptance gate G1
(docs/M16_MIN_LOOP.md §0):

  * Determinism: identical (claims, hypotheses) -> bit-for-bit identical
    ``knowledge_fingerprint`` (freeze the knowledge -> the second round's
    proposals must be identical).
  * Discriminative consumption: flip one referenced claim
    ``supported -> rejected`` and the affected hypothesis's effective status
    moves ``SUPPORTED -> REJECTED`` and the fingerprint changes (a reverse claim
    provably re-steers the agent — no performative feedback, C2 lesson).

Layering (public-red-line EXP007): this module lives in kernel/ and imports only
kernel objects; it consumes claims as plain dicts (the ledger's on-disk shape),
never importing the compiler or any upper package.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from expos.kernel.objects import HypothesisObject, HypothesisStatus

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime coupling
    from expos.kernel.store import RunStore


#: Knowledge-face payload version. Bumping it re-fingerprints all views (an
#: intended schema break), so it is part of the fingerprinted content.
KNOWLEDGE_PV = 1

# Ledger claim statuses (scripts/claim_compiler.py VALID_STATUS) projected onto a
# hypothesis-evidence SIGNAL. supporting/refuting are decisive; weak is
# non-decisive positive; uninformative claims (invalid probe / stale / superseded
# / absent from ledger) carry no weight.
_SIGNAL_SUPPORTING = "supporting"
_SIGNAL_REFUTING = "refuting"
_SIGNAL_WEAK = "weak"
_SIGNAL_UNINFORMATIVE = "uninformative"

#: sentinel status for a referenced claim absent from the ledger.
CLAIM_STATUS_MISSING = "MISSING"

_STATUS_TO_SIGNAL: dict[str, str] = {
    "supported": _SIGNAL_SUPPORTING,
    "rejected": _SIGNAL_REFUTING,
    "partially_supported": _SIGNAL_WEAK,
    "invalid_probe": _SIGNAL_UNINFORMATIVE,
    "superseded": _SIGNAL_UNINFORMATIVE,
    "stale": _SIGNAL_UNINFORMATIVE,
    CLAIM_STATUS_MISSING: _SIGNAL_UNINFORMATIVE,
}


class _FrozenModel(BaseModel):
    """Immutable, closed compiled-artifact base (frozen so a KnowledgeView cannot
    be edited after compilation — knowledge is a product, not hand-authored)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ClaimEvidence(_FrozenModel):
    """One claim reference resolved against the ledger."""

    claim_id: str
    status: str  # ledger status, or CLAIM_STATUS_MISSING if absent
    signal: str  # supporting / refuting / weak / uninformative


class HypothesisKnowledge(_FrozenModel):
    """Per-hypothesis compiled summary: its stored status plus the status
    EFFECTIVE-ly implied by the current claim evidence."""

    hypothesis_id: str
    statement: str
    stored_status: HypothesisStatus
    effective_status: HypothesisStatus
    evidence: tuple[ClaimEvidence, ...] = ()


class KnowledgeView(_FrozenModel):
    """Frozen compiled knowledge the agent consumes.

    ``knowledge_fingerprint`` = sha256 over the canonical serialization of
    {pv, hypotheses}. It is a pure function of the compiled knowledge (per
    hypothesis: stored + effective status + resolved evidence); the ``n_claims``
    / ``n_hypotheses`` counts are reported metadata and are deliberately NOT
    folded into the fingerprint, so a claim referenced by no hypothesis cannot
    perturb the compiled knowledge state.
    """

    pv: int = KNOWLEDGE_PV
    n_hypotheses: int
    n_claims: int
    knowledge_fingerprint: str
    hypotheses: tuple[HypothesisKnowledge, ...] = ()


def _effective_status(
    stored: HypothesisStatus, evidence: list[ClaimEvidence]
) -> HypothesisStatus:
    """Derive a hypothesis's effective status from its claim evidence.

    Rule (deterministic, conservative — refuting evidence dominates):
      * SUPERSEDED is terminal/sticky: a superseded hypothesis is never
        recomputed from claims.
      * any refuting (rejected) claim  -> REJECTED
      * else any supporting (supported) claim -> SUPPORTED
      * else (only weak/uninformative/absent) -> OPEN
    """
    if stored is HypothesisStatus.SUPERSEDED:
        return HypothesisStatus.SUPERSEDED
    signals = {e.signal for e in evidence}
    if _SIGNAL_REFUTING in signals:
        return HypothesisStatus.REJECTED
    if _SIGNAL_SUPPORTING in signals:
        return HypothesisStatus.SUPPORTED
    return HypothesisStatus.OPEN


def _index_claims(claims: list[dict[str, Any]]) -> dict[str, str]:
    """Build claim_id -> status. Loud-fail on a claim dict missing either key
    (no silent shape degradation — CONTRIBUTING §3)."""
    index: dict[str, str] = {}
    for claim in claims:
        if "claim_id" not in claim or "status" not in claim:
            raise ValueError(
                "claim dict must carry 'claim_id' and 'status' "
                f"(ledger shape); got keys {sorted(claim.keys())}"
            )
        index[claim["claim_id"]] = claim["status"]
    return index


def _canonical(payload: dict[str, Any]) -> str:
    """Canonical JSON (sort_keys + compact separators + UTF-8) — same recipe as
    expos.domain.config_fingerprint, so ordering of inputs never leaks in."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def compile_knowledge(
    claims: list[dict[str, Any]], hypotheses: list[HypothesisObject]
) -> KnowledgeView:
    """Compile the claim ledger + posed hypotheses into a frozen KnowledgeView.

    Pure function: no I/O, no clock, no randomness — identical inputs yield a
    bit-for-bit identical ``knowledge_fingerprint`` (G1 determinism substrate).
    Input ordering is canonicalized (hypotheses sorted by id, evidence by
    claim_id), so caller-side list order cannot change the fingerprint.
    """
    claim_status = _index_claims(claims)

    compiled: list[HypothesisKnowledge] = []
    for hyp in hypotheses:
        evidence: list[ClaimEvidence] = []
        for claim_id in sorted(set(hyp.evidence_refs)):
            status = claim_status.get(claim_id, CLAIM_STATUS_MISSING)
            evidence.append(
                ClaimEvidence(
                    claim_id=claim_id,
                    status=status,
                    signal=_STATUS_TO_SIGNAL.get(status, _SIGNAL_UNINFORMATIVE),
                )
            )
        compiled.append(
            HypothesisKnowledge(
                hypothesis_id=hyp.hypothesis_id,
                statement=hyp.statement,
                stored_status=hyp.status,
                effective_status=_effective_status(hyp.status, evidence),
                evidence=tuple(evidence),
            )
        )

    compiled.sort(key=lambda h: h.hypothesis_id)

    fingerprint_payload = {
        "pv": KNOWLEDGE_PV,
        "hypotheses": [
            {
                "hypothesis_id": h.hypothesis_id,
                "stored_status": h.stored_status.value,
                "effective_status": h.effective_status.value,
                "evidence": [
                    {"claim_id": e.claim_id, "status": e.status, "signal": e.signal}
                    for e in h.evidence
                ],
            }
            for h in compiled
        ],
    }
    fingerprint = hashlib.sha256(
        _canonical(fingerprint_payload).encode("utf-8")
    ).hexdigest()

    return KnowledgeView(
        n_hypotheses=len(compiled),
        n_claims=len(claim_status),
        knowledge_fingerprint=fingerprint,
        hypotheses=tuple(compiled),
    )


def emit_knowledge_updated(
    store: "RunStore", view: KnowledgeView, *, round_id: int
) -> dict[str, Any] | None:
    """Emit the ``knowledge_updated`` event for a compiled KnowledgeView.

    Payload (EVENT_SCHEMA.md §1 + §4): {pv, round_id, fingerprint, n_hypotheses,
    n_claims}. Required keys (store.EVENT_PAYLOAD_REQUIRED): round_id / fingerprint /
    n_hypotheses / n_claims. ``round_id`` (Phase 4 item #5, ADDITIVE) pins the event to its
    round for the dedup key and lets consumers key by round instead of seq order (the
    gate-12 verifier already reads it with a seq-order fallback — additive-safe).

    Routed through :meth:`RunStore.append_decision_face_event` for resume-idempotent
    exactly-once (dedup key = (round_id,), content fingerprint = the knowledge fingerprint):
    a redone round re-emitting the SAME knowledge is skipped, a DIFFERENT one raises. Returns
    the appended record, or ``None`` when an equivalent event already exists (idempotent
    skip). The emission POINT (which round) is wired by W7/W9 loop integration, not here."""
    return store.append_decision_face_event(
        "knowledge_updated",
        {
            "pv": KNOWLEDGE_PV,
            "round_id": round_id,
            "fingerprint": view.knowledge_fingerprint,
            "n_hypotheses": view.n_hypotheses,
            "n_claims": view.n_claims,
        },
        dedup_key=(round_id,),
        content_fingerprint=view.knowledge_fingerprint,
    )
