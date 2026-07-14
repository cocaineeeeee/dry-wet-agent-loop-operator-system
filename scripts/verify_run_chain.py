#!/usr/bin/env python3
"""verify_run_chain — M17 gate-12 acceptance tool: recompute / re-audit a run's
whole decision chain from the EVENT STREAM ALONE (third-party, read-only).

Design lineage: /Data1/ericyang/r4_os_references/INDEX_M18_LINEAGE.md
(OpenLineage lifecycle pairing + facet-style required-field validation §1,
Marquez content-addressed node version + sorted-set canonicalization + first
divergence §2/§3). Explicit not-copy red line (INDEX §6): NO service, NO DB, NO
global state — a pure-function + file-state audit of one run's ``events.jsonl``
(and ``checkpoint.json`` only as the reconciliation object, never the truth).

The truth is the append-only ``events.jsonl``; ``checkpoint.json`` is a lagging
cursor (store writes the event first, the checkpoint second), so the chain we
rebuild from events is authoritative and the checkpoint is reconciled AGAINST it.

Three layers (INDEX §1 skeleton):

  Layer 1 — lifecycle pairing: exactly one ``run_start``; at least one
    ``run_stop`` (resume can append a second terminal); the LAST terminal carries
    a known ``exit_status``; seq monotonicity is enforced by the store reader;
    any ``resume`` event carries a legal ``from_round``.
  Layer 2 — payload completeness: reuse ``RunStore.validate_event_payloads``
    (per-kind required-key registry) — never re-implemented here.
  Layer 3 — fingerprint threading: per round rebuild
    proposal(prior_proposal) -> promotion(promotion_decision) -> wet observations
    -> adjudication(claim_decision) -> ledger(knowledge_updated) and assert the
    knowledge fingerprint threads verbatim across all of them; then reconcile the
    end state against ``checkpoint.json`` (completed_rounds / claim_ledger /
    certification_state / fingerprints). A LEGAL-QUIET round (empty-proposal or
    zero-promotion, letter red_to_blue/101 §3) is a first-class chain node whose
    ``wet_leg_skipped{reason}`` is its evidence: the truncated chain is accepted,
    but a silent skip (promoted==[] with no wet_leg_skipped) or a self-contradicting
    skip (a skipped round that still adjudicates a claim) is still BROKEN. See the
    ``_SKIP_*`` semantic table below.

CLI:
    python scripts/verify_run_chain.py <run_dir>            # exit 0 ok / 1 broken
    python scripts/verify_run_chain.py <run_dir> --json     # machine summary
    python scripts/verify_run_chain.py --diff <run_a> <run_b>[ --json]

Exit codes: 0 = chain complete, 1 = broken chain (first breakpoint printed),
2 = usage / unreadable run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse the store's read + payload-validation path (read-only). validate_event_payloads
# is imported (not re-implemented) per the gate-12 discipline; read_events gives us the
# store's own torn-tail tolerance + seq-monotonicity enforcement for free.
from expos.kernel.store import RunStore, StoreError

# ---- lifecycle / status vocabulary -----------------------------------------
_KNOWN_EXIT_STATUS = frozenset({"success", "abort", "fail"})

# ---- legal-quiet vocabulary (letter red_to_blue/101 §3) ---------------------
# A legal-quiet round is a first-class chain node, NOT a broken chain: the agent
# either proposed nothing runnable (empty-proposal round) or the promotion gate
# admitted nobody (zero-promotion round). In both, the wet leg is LOUDLY skipped
# and ``wet_leg_skipped{reason}`` IS that round's chain evidence. mcl emits exactly
# these two reasons; anything else in a skip is malformed. Semantic table:
#
#   (a) empty-proposal round  — reason == ``no_candidate_proposed``
#       necessary set: knowledge_updated + wet_leg_skipped{no_candidate_proposed}
#       + AT MOST one prior_proposal whose candidates == [] (the reask-exhaustion
#         shape emits agent_generation_failed and NO prior_proposal instead).
#       forbidden (contradiction): any promotion_decision or claim_decision.
#   (b) zero-promotion round   — reason == ``no_candidate_promoted``
#       necessary set: knowledge_updated + prior_proposal + promotion_decision
#       whose promoted == [] + wet_leg_skipped{no_candidate_promoted}.
#       forbidden (contradiction): any claim_decision.
#   strict: a promoted==[] round MISSING its wet_leg_skipped is a BROKEN chain
#   (a silent zero-promotion round), as is a skip round that also adjudicates.
_SKIP_NO_PROPOSAL = "no_candidate_proposed"
_SKIP_NO_PROMOTION = "no_candidate_promoted"
_KNOWN_SKIP_REASONS = frozenset({_SKIP_NO_PROPOSAL, _SKIP_NO_PROMOTION})


# ============================================================ result objects

@dataclass
class VerifyResult:
    """Structured verdict. ``ok`` True => chain complete; otherwise ``layer`` /
    ``code`` / ``message`` pinpoint the FIRST breakpoint."""

    ok: bool
    layer: int | None = None
    code: str | None = None
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "layer": self.layer,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "summary": self.summary,
        }


class UsageError(Exception):
    """Bad argument / unreadable run (exit 2, distinct from a broken chain)."""


# ============================================================ canonical fingerprint

def _canonical(value: Any) -> Any:
    """Recursively canonicalize: dict keys sorted (json.dumps sort_keys handles
    it), lists kept in order. Set-like fields are sorted by the node builders
    BEFORE reaching here (INDEX §3: only sort fields whose order is not load
    bearing — proposal ``candidates`` order IS load bearing and is left alone)."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _fingerprint(content: Any) -> str:
    """sha256 over the canonical JSON of ``content`` (Marquez content-addressed
    version, INDEX §2): identical content -> byte-identical fingerprint; any
    field change -> a different fingerprint. sort_keys makes dict-key write order
    irrelevant; set-like lists are pre-sorted by the caller."""
    blob = json.dumps(_canonical(content), sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sorted_promoted(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """promoted[]/denied[] are set-like (INDEX §3): sort by cand_id so caller-side
    ordering never leaks into the node fingerprint. Rank stays legible inside each
    entry's ``basis.acquisition_rank``, so sorting by cand_id loses no evidence."""
    return sorted(entries, key=lambda e: str(e.get("cand_id", "")))


# ============================================================ node builders (diff)

def _knowledge_node_content(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": payload.get("fingerprint"),
        "n_hypotheses": payload.get("n_hypotheses"),
        "n_claims": payload.get("n_claims"),
    }


def _proposal_node_content(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content", {}) or {}
    return {
        "knowledge_fingerprint": content.get("knowledge_fingerprint"),
        # basis is a set of claim ids -> sort; candidate ORDER is the G1 signal -> keep.
        "basis": sorted(content.get("basis", []) or []),
        "candidates": list(content.get("candidates", []) or []),
    }


def _promotion_node_content(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "knowledge_fingerprint": payload.get("knowledge_fingerprint"),
        "policy": payload.get("policy"),
        "promoted": _sorted_promoted(payload.get("promoted", []) or []),
        "denied": _sorted_promoted(payload.get("denied", []) or []),
    }


def _claim_node_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Content-address the ADJUDICATION on its REPRODUCIBLE decision content only.

    The finding that shapes this (documented in the gate-12 report): a run's per
    observation ``obs_id`` is a random UUID, and the observation ``content_fingerprint``
    is itself salted by that UUID — so BOTH ``input_observation_ids`` and
    ``observation_fingerprints`` differ between two byte-otherwise-identical same
    seed runs. Folding them into the node fingerprint would make every same-seed
    diff falsely 'flipped'. Per the Marquez content-addressing lesson (INDEX §2)
    we exclude the volatile identifiers and keep only reproducible decision content
    plus the stable observation COUNT; the wet-derived ``statistic.value`` is the
    real face discriminator (K2 sign flip)."""
    stat = payload.get("statistic", {}) or {}
    return {
        "claim_id": payload.get("claim_id"),
        "claim_version": payload.get("claim_version"),
        "decision_status": payload.get("decision_status"),
        "decision_fn_id": payload.get("decision_fn_id"),
        # count is reproducible; the individual obs_ids are random UUIDs (excluded).
        "n_input_observations": len(payload.get("input_observation_ids", []) or []),
        "consumed_knowledge_fingerprint": payload.get("consumed_knowledge_fingerprint"),
        # the wet-derived effect is the face discriminator (K2 sign flip) -> in fp.
        "statistic_value": stat.get("value"),
        "statistic_name": stat.get("name"),
    }


def _skip_node_content(payload: dict[str, Any]) -> dict[str, Any]:
    """A legal-quiet round's wet_leg_skipped is a first-class chain node: its
    ``reason`` is the load-bearing content (INDEX §2), so an otherwise-identical
    'one static, one active' pair of runs diverges here (empty/zero-promotion node
    vs the active round's claim_decision node)."""
    return {"reason": payload.get("reason")}


@dataclass
class ChainNode:
    round_index: int
    node_type: str
    discriminator: str  # "" for singletons, claim_id for claim_decision
    fingerprint: str
    summary: str

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.round_index, self.node_type, self.discriminator)


# ============================================================ stream loading

def load_events(run_dir: Path) -> tuple[list[dict[str, Any]], RunStore]:
    """Third-party read of the event stream (read-only, no lock, no create). Raises
    UsageError for a missing dir / absent events.jsonl; StoreError (seq gap / mid
    corruption) is left to the caller to fold into a layer-1 breakpoint."""
    if not run_dir.is_dir():
        raise UsageError(f"not a run directory: {run_dir}")
    if not (run_dir / "events.jsonl").exists():
        raise UsageError(f"no events.jsonl in run directory: {run_dir}")
    store = RunStore(run_dir, create=False)
    events = store.read_events()
    return events, store


# ============================================================ layer checks

def _check_lifecycle(events: list[dict[str, Any]]) -> VerifyResult | None:
    """Layer 1: run_start/run_stop pairing + resume legality. Returns a failing
    VerifyResult, or None when the layer passes."""
    starts = [e for e in events if e["kind"] == "run_start"]
    stops = [e for e in events if e["kind"] == "run_stop"]
    if len(starts) != 1:
        return VerifyResult(False, 1, "run_start_cardinality",
                            f"expected exactly 1 run_start, found {len(starts)}")
    if len(stops) < 1:
        return VerifyResult(False, 1, "run_stop_missing",
                            "no run_stop event (absence == crash; chain not closed)")
    last_stop = stops[-1]
    status = (last_stop.get("payload") or {}).get("exit_status")
    if status not in _KNOWN_EXIT_STATUS:
        return VerifyResult(False, 1, "run_stop_status",
                            f"final run_stop has unknown exit_status={status!r}")
    # the terminal event of a closed stream must be the run_stop.
    if events[-1]["kind"] != "run_stop":
        return VerifyResult(False, 1, "stream_not_closed",
                            f"stream does not end on run_stop (ends on {events[-1]['kind']!r})")
    # resume legality: from_round must be a positive int within the completed span.
    n_rounds = sum(1 for e in events if e["kind"] == "knowledge_updated")
    for e in events:
        if e["kind"] != "resume":
            continue
        fr = (e.get("payload") or {}).get("from_round")
        if not isinstance(fr, int) or fr < 1 or fr > n_rounds:
            return VerifyResult(False, 1, "resume_illegal",
                                f"resume from_round={fr!r} outside [1, {n_rounds}]")
    return None


def _check_payloads(events: list[dict[str, Any]], store: RunStore) -> VerifyResult | None:
    """Layer 2: per-kind required-key completeness — REUSE the store's validator."""
    violations = store.validate_event_payloads(events)
    if violations:
        first = violations[0]
        return VerifyResult(False, 2, "payload_violation",
                            f"payload violation at seq={first.get('seq')} "
                            f"kind={first.get('kind')}: {first.get('problem')} "
                            f"{first.get('keys', '')}".strip(),
                            detail={"violations": violations})
    return None


def _round_index_of_knowledge(events: list[dict[str, Any]]) -> list[str]:
    """knowledge_updated carries no round_id -> the r-th (seq order) IS round r's
    fingerprint (one compile per round, first event of the round)."""
    return [e["payload"]["fingerprint"]
            for e in events if e["kind"] == "knowledge_updated"]


def _by_round(events: list[dict[str, Any]], kind: str) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for e in events:
        if e["kind"] != kind:
            continue
        rid = (e.get("payload") or {}).get("round_id")
        out.setdefault(rid, []).append(e)
    return out


def _check_fingerprint_chain(
    events: list[dict[str, Any]], store: RunStore, run_dir: Path
) -> VerifyResult | None:
    """Layer 3: thread the per-round decision chain on the knowledge fingerprint,
    then reconcile the end state against checkpoint.json."""
    know_fps = _round_index_of_knowledge(events)
    n_rounds = len(know_fps)
    if n_rounds == 0:
        return VerifyResult(False, 3, "no_rounds",
                            "no knowledge_updated events — chain has no rounds")

    proposals = _by_round(events, "decision")
    # keep only prior_proposal decisions (append_decision emits kind=='decision').
    prop_by_round = {
        rid: [e for e in evs if (e.get("payload") or {}).get("kind") == "prior_proposal"]
        for rid, evs in proposals.items()
    }
    promo_by_round = _by_round(events, "promotion_decision")
    claim_by_round = _by_round(events, "claim_decision")
    skip_by_round = _by_round(events, "wet_leg_skipped")

    # every round-tagged decision/skip event must reference a round that actually
    # emitted a knowledge_updated (catches a deleted/missing knowledge event).
    for kind, grouped in (("promotion_decision", promo_by_round),
                          ("claim_decision", claim_by_round),
                          ("prior_proposal", prop_by_round),
                          ("wet_leg_skipped", skip_by_round)):
        for rid in grouped:
            if rid is None or rid < 0 or rid >= n_rounds:
                return VerifyResult(
                    False, 3, "knowledge_missing_for_round",
                    f"{kind} references round {rid} with no matching knowledge_updated "
                    f"(saw {n_rounds} knowledge_updated events)",
                    detail={"round_id": rid, "n_knowledge_updated": n_rounds})

    for r in range(n_rounds):
        fpr = know_fps[r]
        props = prop_by_round.get(r, [])
        promos = promo_by_round.get(r, [])
        claims = claim_by_round.get(r, [])
        skips = skip_by_round.get(r, [])

        # ---- legal-quiet classification from the wet_leg_skipped evidence ----
        if len(skips) > 1:
            return VerifyResult(False, 3, "skip_cardinality",
                                f"round {r}: {len(skips)} wet_leg_skipped events "
                                "(a legal-quiet round emits exactly one)",
                                detail={"round": r})
        reasons = {(s.get("payload") or {}).get("reason") for s in skips}
        unknown = reasons - _KNOWN_SKIP_REASONS
        if unknown:
            return VerifyResult(False, 3, "skip_unknown_reason",
                                f"round {r}: wet_leg_skipped carries unknown reason(s) "
                                f"{sorted(str(x) for x in unknown)}",
                                detail={"round": r,
                                        "reasons": sorted(str(x) for x in reasons)})
        quiet_no_proposal = _SKIP_NO_PROPOSAL in reasons
        quiet_no_promotion = _SKIP_NO_PROMOTION in reasons

        # ============================================ (a) empty-proposal quiet round
        # legal chain: knowledge_updated -> [prior_proposal(candidates==[]) OR
        # agent_generation_failed] -> wet_leg_skipped{no_candidate_proposed}. The round
        # returns BEFORE the dry/promotion leg, so a promotion_decision/claim_decision
        # here is a contradiction (BROKEN).
        if quiet_no_proposal:
            if len(props) > 1:
                return VerifyResult(False, 3, "proposal_cardinality",
                                    f"round {r} (empty-proposal quiet): expected 0 or 1 "
                                    f"prior_proposal, found {len(props)}", detail={"round": r})
            if props:
                content = props[0]["payload"].get("content") or {}
                pf = content.get("knowledge_fingerprint")
                if pf != fpr:
                    return VerifyResult(False, 3, "proposal_fp_mismatch",
                                        f"round {r}: prior_proposal knowledge_fingerprint {pf!r} "
                                        f"!= round knowledge_updated {fpr!r}",
                                        detail={"round": r, "proposal_fp": pf, "knowledge_fp": fpr})
                if content.get("candidates"):
                    return VerifyResult(False, 3, "quiet_proposal_not_empty",
                                        f"round {r}: wet_leg_skipped{{no_candidate_proposed}} but the "
                                        "prior_proposal carries candidates (not an empty-proposal round)",
                                        detail={"round": r, "candidates": content.get("candidates")})
            if promos:
                return VerifyResult(False, 3, "quiet_proposal_has_promotion",
                                    f"round {r}: no_candidate_proposed quiet round also emitted a "
                                    "promotion_decision (the empty-proposal path never reaches the "
                                    "promotion gate)", detail={"round": r})
            if claims:
                return VerifyResult(False, 3, "quiet_round_has_claim",
                                    f"round {r}: no_candidate_proposed quiet round also emitted a "
                                    "claim_decision (no wet observations were adjudicated)",
                                    detail={"round": r})
            continue

        # ---- proposal must exist for the round and carry the round's fingerprint ----
        if len(props) != 1:
            return VerifyResult(False, 3, "proposal_cardinality",
                                f"round {r}: expected exactly 1 prior_proposal, found {len(props)}",
                                detail={"round": r})
        pf = (props[0]["payload"].get("content") or {}).get("knowledge_fingerprint")
        if pf != fpr:
            return VerifyResult(False, 3, "proposal_fp_mismatch",
                                f"round {r}: prior_proposal knowledge_fingerprint {pf!r} "
                                f"!= round knowledge_updated {fpr!r}",
                                detail={"round": r, "proposal_fp": pf, "knowledge_fp": fpr})
        # ---- promotion must exist for the round and carry the round's fingerprint ----
        if len(promos) != 1:
            return VerifyResult(False, 3, "promotion_cardinality",
                                f"round {r}: expected exactly 1 promotion_decision, found {len(promos)}",
                                detail={"round": r})
        mf = promos[0]["payload"].get("knowledge_fingerprint")
        if mf != fpr:
            return VerifyResult(False, 3, "promotion_fp_mismatch",
                                f"round {r}: promotion_decision knowledge_fingerprint {mf!r} "
                                f"!= round knowledge_updated {fpr!r}",
                                detail={"round": r, "promotion_fp": mf, "knowledge_fp": fpr})
        promoted = promos[0]["payload"].get("promoted") or []

        # ============================================ (b) zero-promotion quiet round
        # legal chain: ... -> promotion_decision(promoted==[]) ->
        # wet_leg_skipped{no_candidate_promoted}. A promoted==[] round MUST loudly skip
        # the wet leg (a silent one is BROKEN) and must NOT adjudicate any claim.
        if not promoted:
            if not quiet_no_promotion:
                return VerifyResult(False, 3, "zero_promotion_without_skip",
                                    f"round {r}: promotion_decision promoted==[] but no "
                                    "wet_leg_skipped{no_candidate_promoted} evidence (a silent "
                                    "zero-promotion round is a broken chain)", detail={"round": r})
            if claims:
                return VerifyResult(False, 3, "quiet_round_has_claim",
                                    f"round {r}: zero-promotion quiet round also emitted a "
                                    "claim_decision (the wet leg was skipped)", detail={"round": r})
            continue

        # ---- normal round: a non-empty promotion must NOT carry a zero-promotion skip ----
        if quiet_no_promotion:
            return VerifyResult(False, 3, "promotion_skip_contradiction",
                                f"round {r}: wet_leg_skipped{{no_candidate_promoted}} but the "
                                f"promotion_decision promoted {len(promoted)} candidate(s)",
                                detail={"round": r})
        # every claim_decision of the round must have consumed exactly this fingerprint.
        for cd in claims:
            cf = cd["payload"].get("consumed_knowledge_fingerprint")
            if cf != fpr:
                return VerifyResult(False, 3, "claim_fp_mismatch",
                                    f"round {r}: claim_decision consumed_knowledge_fingerprint "
                                    f"{cf!r} != round knowledge_updated {fpr!r}",
                                    detail={"round": r, "consumed_fp": cf, "knowledge_fp": fpr})

    return _reconcile_checkpoint(events, store, run_dir, know_fps, claim_by_round)


def _reconcile_checkpoint(
    events: list[dict[str, Any]],
    store: RunStore,
    run_dir: Path,
    know_fps: list[str],
    claim_by_round: dict[int, list[dict[str, Any]]],
) -> VerifyResult | None:
    """End-state reconciliation: the chain we rebuilt from events must agree with
    checkpoint.json (the lagging cursor). completed_rounds, claim_ledger fingerprints
    and certification_state are reconciled against the stream."""
    ckpt = store.read_checkpoint()
    if ckpt is None:
        # a completed run always checkpoints each round; its absence with a success
        # run_stop is itself a break.
        stops = [e for e in events if e["kind"] == "run_stop"]
        if stops and (stops[-1].get("payload") or {}).get("exit_status") == "success":
            return VerifyResult(False, 3, "checkpoint_missing",
                                "run_stop=success but no checkpoint.json to reconcile")
        return None

    n_rounds = len(know_fps)
    completed = ckpt.get("completed_rounds")
    n_ckpt_events = sum(1 for e in events if e["kind"] == "checkpoint")
    stops = [e for e in events if e["kind"] == "run_stop"]
    stop_completed = (stops[-1].get("payload") or {}).get("completed_rounds")

    if completed != n_rounds:
        return VerifyResult(False, 3, "completed_rounds_mismatch",
                            f"checkpoint.completed_rounds={completed} != "
                            f"{n_rounds} knowledge_updated rounds in stream",
                            detail={"checkpoint": completed, "stream_rounds": n_rounds})
    if n_ckpt_events != n_rounds:
        return VerifyResult(False, 3, "checkpoint_event_count_mismatch",
                            f"{n_ckpt_events} checkpoint events != {n_rounds} rounds")
    # run_stop=success records the final round count; reconcile when present.
    final_status = (stops[-1].get("payload") or {}).get("exit_status")
    if final_status == "success" and stop_completed is not None and stop_completed != completed:
        return VerifyResult(False, 3, "run_stop_rounds_mismatch",
                            f"run_stop.completed_rounds={stop_completed} != "
                            f"checkpoint.completed_rounds={completed}")

    # claim_ledger fingerprint reconciliation: every claim_decision (round r,
    # claim_id, version) must have a ledger record with matching version AND a
    # provenance consumed_knowledge_fingerprint equal to the stream fingerprint.
    # Tampering ANY fingerprint char inside the checkpoint ledger breaks this.
    ledger = ckpt.get("claim_ledger") or []
    ledger_index: dict[tuple[str, int], dict[str, Any]] = {}
    for rec in ledger:
        ledger_index[(rec.get("claim_id"), rec.get("version"))] = rec
    for r, cds in claim_by_round.items():
        for cd in cds:
            p = cd["payload"]
            key = (p.get("claim_id"), p.get("claim_version"))
            rec = ledger_index.get(key)
            if rec is None:
                return VerifyResult(False, 3, "ledger_record_missing",
                                    f"round {r}: claim_decision {key} has no matching "
                                    "record in checkpoint.claim_ledger",
                                    detail={"round": r, "claim_key": list(key)})
            led_fp = (((rec.get("provenance") or {}).get("usage") or {})
                      .get("consumed_knowledge_fingerprint"))
            if led_fp != p.get("consumed_knowledge_fingerprint"):
                return VerifyResult(False, 3, "ledger_fp_mismatch",
                                    f"round {r}: checkpoint ledger fingerprint {led_fp!r} for "
                                    f"{key} != claim_decision consumed "
                                    f"{p.get('consumed_knowledge_fingerprint')!r}",
                                    detail={"round": r, "claim_key": list(key),
                                            "ledger_fp": led_fp,
                                            "stream_fp": p.get("consumed_knowledge_fingerprint")})

    # certification_state: its claim ids must be a subset of the adjudicated targets.
    cert_state = ckpt.get("certification_state")
    if isinstance(cert_state, dict):
        targets = {cd["payload"].get("claim_id")
                   for cds in claim_by_round.values() for cd in cds}
        stray = set(cert_state.keys()) - targets
        if stray:
            return VerifyResult(False, 3, "certification_state_stray",
                                f"certification_state holds claim ids never adjudicated: "
                                f"{sorted(stray)}", detail={"stray": sorted(stray)})
    return None


# ============================================================ top-level verify

def verify_run(run_dir: Path) -> VerifyResult:
    """Run all three layers; return the FIRST failing layer's VerifyResult, or an
    ok result with a summary."""
    try:
        events, store = load_events(run_dir)
    except StoreError as exc:
        # seq gap / mid-stream corruption surfaced by the store reader is a
        # layer-1 stream-integrity breakpoint (not a crash of the auditor).
        return VerifyResult(False, 1, "stream_integrity", str(exc))
    if not events:
        return VerifyResult(False, 1, "empty_stream", "events.jsonl has no events")

    for check in (
        lambda: _check_lifecycle(events),
        lambda: _check_payloads(events, store),
        lambda: _check_fingerprint_chain(events, store, run_dir),
    ):
        result = check()
        if result is not None:
            return result

    n_rounds = sum(1 for e in events if e["kind"] == "knowledge_updated")
    summary = {
        "n_events": len(events),
        "n_rounds": n_rounds,
        "n_promotion_decisions": sum(1 for e in events if e["kind"] == "promotion_decision"),
        "n_claim_decisions": sum(1 for e in events if e["kind"] == "claim_decision"),
        "knowledge_fingerprints": _round_index_of_knowledge(events),
        "final_exit_status": [e for e in events if e["kind"] == "run_stop"][-1]["payload"].get("exit_status"),
    }
    return VerifyResult(True, None, None, "chain complete", summary=summary)


# ============================================================ diff

def build_chain(run_dir: Path) -> list[ChainNode]:
    """Rebuild the ordered decision-chain node list (knowledge -> proposal ->
    promotion -> claim_decision* per round) with a canonical content fingerprint
    per node (Marquez content-addressed version, INDEX §2)."""
    events, _store = load_events(run_dir)
    know = [e for e in events if e["kind"] == "knowledge_updated"]
    prop_by_round = _by_round(events, "decision")
    promo_by_round = _by_round(events, "promotion_decision")
    claim_by_round = _by_round(events, "claim_decision")
    skip_by_round = _by_round(events, "wet_leg_skipped")

    nodes: list[ChainNode] = []
    for r in range(len(know)):
        kc = _knowledge_node_content(know[r]["payload"])
        nodes.append(ChainNode(r, "knowledge_updated", "", _fingerprint(kc),
                               f"fp={kc['fingerprint']}"))
        props = [e for e in prop_by_round.get(r, [])
                 if (e.get("payload") or {}).get("kind") == "prior_proposal"]
        for e in props:
            pc = _proposal_node_content(e["payload"])
            nodes.append(ChainNode(r, "prior_proposal", "", _fingerprint(pc),
                                   f"candidates={pc['candidates']}"))
        for e in promo_by_round.get(r, []):
            mc = _promotion_node_content(e["payload"])
            promoted = [p.get("cand_id") for p in mc["promoted"]]
            nodes.append(ChainNode(r, "promotion_decision", "", _fingerprint(mc),
                                   f"promoted={promoted}"))
        # legal-quiet skip node: its reason enters the fingerprint so a static round
        # diverges from an active round (one skips, one adjudicates).
        for e in skip_by_round.get(r, []):
            sc = _skip_node_content(e["payload"])
            nodes.append(ChainNode(r, "wet_leg_skipped", "", _fingerprint(sc),
                                   f"skipped reason={sc['reason']}"))
        for e in sorted(claim_by_round.get(r, []),
                        key=lambda x: str(x["payload"].get("claim_id"))):
            cc = _claim_node_content(e["payload"])
            nodes.append(ChainNode(r, "claim_decision", str(cc["claim_id"]),
                                   _fingerprint(cc),
                                   f"{cc['claim_id']} status={cc['decision_status']} "
                                   f"effect={cc['statistic_value']}"))
    return nodes


@dataclass
class DiffResult:
    diverged: bool
    first_divergence: dict[str, Any] | None = None
    n_nodes_a: int = 0
    n_nodes_b: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "diverged": self.diverged,
            "first_divergence": self.first_divergence,
            "n_nodes_a": self.n_nodes_a,
            "n_nodes_b": self.n_nodes_b,
        }


def diff_decision_chain(run_a: Path, run_b: Path) -> DiffResult:
    """Compare two runs' decision chains node-by-node on the canonical content
    fingerprint (INDEX §2): equal fingerprint => that node is consistent, unequal
    => flipped. Report the FIRST divergence (round + node type + both summaries).
    A structural mismatch (missing/extra node) is itself the first divergence."""
    a = build_chain(run_a)
    b = build_chain(run_b)
    n = min(len(a), len(b))
    for i in range(n):
        na, nb = a[i], b[i]
        if na.key != nb.key:
            return DiffResult(True, {
                "reason": "structural",
                "round": na.round_index,
                "node_a": {"type": na.node_type, "discriminator": na.discriminator,
                           "summary": na.summary},
                "node_b": {"type": nb.node_type, "discriminator": nb.discriminator,
                           "summary": nb.summary},
            }, len(a), len(b))
        if na.fingerprint != nb.fingerprint:
            return DiffResult(True, {
                "reason": "content",
                "round": na.round_index,
                "node_type": na.node_type,
                "discriminator": na.discriminator,
                "a_fingerprint": na.fingerprint,
                "b_fingerprint": nb.fingerprint,
                "a_summary": na.summary,
                "b_summary": nb.summary,
            }, len(a), len(b))
    if len(a) != len(b):
        longer, idx = (a, n) if len(a) > len(b) else (b, n)
        extra = longer[idx]
        return DiffResult(True, {
            "reason": "length",
            "round": extra.round_index,
            "extra_side": "a" if len(a) > len(b) else "b",
            "extra_node": {"type": extra.node_type, "summary": extra.summary},
        }, len(a), len(b))
    return DiffResult(False, None, len(a), len(b))


# ============================================================ CLI

def _print_verify(result: VerifyResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    if result.ok:
        s = result.summary
        print("CHAIN COMPLETE")
        print(f"  events={s['n_events']} rounds={s['n_rounds']} "
              f"promotions={s['n_promotion_decisions']} "
              f"claim_decisions={s['n_claim_decisions']}")
        print(f"  exit_status={s['final_exit_status']}")
        print(f"  knowledge_fingerprints={s['knowledge_fingerprints']}")
    else:
        print("CHAIN BROKEN")
        print(f"  first breakpoint: layer {result.layer} [{result.code}]")
        print(f"  {result.message}")


def _print_diff(result: DiffResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    if not result.diverged:
        print("CHAINS CONSISTENT (zero divergence)")
        print(f"  nodes={result.n_nodes_a}")
        return
    d = result.first_divergence or {}
    print("CHAINS DIVERGE")
    print(f"  first divergence at round {d.get('round')} "
          f"({d.get('reason')})")
    if d.get("reason") == "content":
        print(f"  node_type={d.get('node_type')} discriminator={d.get('discriminator')!r}")
        print(f"  A: {d.get('a_summary')}  (fp={d.get('a_fingerprint', '')[:16]})")
        print(f"  B: {d.get('b_summary')}  (fp={d.get('b_fingerprint', '')[:16]})")
    else:
        print(f"  detail: {json.dumps(d, ensure_ascii=False)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_run_chain",
        description="Recompute/re-audit a run's decision chain from the event stream.")
    parser.add_argument("run_dir", nargs="?", help="run directory to verify")
    parser.add_argument("--diff", nargs=2, metavar=("RUN_A", "RUN_B"),
                        help="diff two runs' decision chains")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.diff:
            result = diff_decision_chain(Path(args.diff[0]), Path(args.diff[1]))
            _print_diff(result, args.json)
            return 1 if result.diverged else 0
        if not args.run_dir:
            parser.error("either <run_dir> or --diff RUN_A RUN_B is required")
        vresult = verify_run(Path(args.run_dir))
        _print_verify(vresult, args.json)
        return 0 if vresult.ok else 1
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
