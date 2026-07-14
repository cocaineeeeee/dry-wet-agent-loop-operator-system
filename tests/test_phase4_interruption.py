"""Phase 4 — six-killpoint interruption matrix + exactly-once crash/recovery (items #1/#2/#5).

The matrix drives a REAL two-legged MCL run (real out-of-process PySCF dry jobs + the
in-process plate-reader wet leg, flipped truth face so certification actually accumulates a
non-trivial e-product and reaches a decisive rejection at round 1), interrupts it at each of
the six pinned killpoints I1..I6, resumes, and asserts the three exactly-once invariants:

  (a) NO duplicate decision-face events (claim_decision / knowledge_updated / promotion_decision
      each appear once per dedup key);
  (b) the ledger effective statuses + the accumulated certification state (e-product) + the
      claim_decision statistic sequence are BITWISE equal to an uninterrupted run (item #2:
      no double-multiplied e-value, no double-applied ClaimDelta);
  (c) the decision surface (DECISION_FACE_KINDS_V1) is bitwise equal to the uninterrupted run.

Killpoints (pinned to expos/mcl.py seams, injected via the no-op-by-default ``interrupt_hook``
callable — never an env flag): I1 dry-run-done/pre-ingest, I2 wet-judged/pre-certify, I3
decide-done/pre-apply, I4 apply-done/pre-emit, I5 emit-done/pre-checkpoint (the double-emission
bullseye), I6 checkpoint-done/pre-next-round. The injected hook raises ``_SimulatedCrash`` so
run_mcl_loop re-raises WITHOUT a terminal run_stop (absence == crash), the exact torn state a
kill -9 leaves. Plus: dedup-guard discriminative unit tests, the knowledge_updated round_id
write-strict/read-tolerant discriminative test, forked-resume refusal, and the wet non-replay
invariant (incomplete issuance refused; no second wet issuance on a clean consume-resume).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from expos.kernel.claims import ClaimRecord, Ledger
from expos.kernel.store import (
    DECISION_FACE_KINDS_V1,
    NondeterminismError,
    RunStore,
)
from expos.mcl import (
    ForkedResumeError,
    WetReplayError,
    _SimulatedCrash,
    run_mcl_loop,
)
from expos.planner.certification import AggregatedCertification
from expos.qc.certification_stats import AggregationConfig, ClaimHead

_DOMAIN = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"

_POLAR = "c_polar_responds_higher"
_NONPOLAR = "c_nonpolar_responds_higher"
_FOCAL = "cand_ethanol"
_REFERENCE = "cand_acetonitrile"

# The flipped (nonpolar-high) truth face: the seed "polar responds higher" claim is
# CONTRADICTED, so certification progresses insufficient(round 0) -> rejected(round 1) and the
# cross-round e-product accumulates — a non-trivial exactly-once target for item #2.
_TRUTH = "nonpolar_high"


def _agg_cert() -> AggregatedCertification:
    return AggregatedCertification(
        [
            ClaimHead(
                claim_id=_POLAR,
                statement="polar solvents give a higher plate-reader response",
                favorable_direction="higher",
                focal_group=(_FOCAL,),
                reference_group=(_REFERENCE,),
            )
        ],
        config=AggregationConfig(run_fingerprint="phase4_matrix"),
    )


# --------------------------------------------------------------- projections / invariants


def _events(run_dir: Path, kind: str | None = None) -> list[dict]:
    return RunStore(run_dir, create=False).read_events(kind)


def _claim_decisions(run_dir: Path) -> list[dict]:
    return [e["payload"] for e in _events(run_dir, "claim_decision")]


def _ledger_effective(run_dir: Path) -> dict[str, str]:
    ckpt = RunStore(run_dir, create=False).read_checkpoint() or {}
    ledger = Ledger(
        claims=tuple(ClaimRecord.model_validate(r) for r in ckpt.get("claim_ledger", []))
    )
    return {k: v.value for k, v in ledger.effective_statuses().items()}


def _cert_state(run_dir: Path):
    return (RunStore(run_dir, create=False).read_checkpoint() or {}).get(
        "certification_state"
    )


def _claim_seq(run_dir: Path) -> list[tuple]:
    return sorted(
        (c["round_id"], c["decision_status"], c["statistic"]["value"])
        for c in _claim_decisions(run_dir)
    )


def _decision_surface(run_dir: Path) -> list[tuple]:
    """Project the run's DECISION face over DECISION_FACE_KINDS_V1, dropping every
    non-deterministic field (seq/ts). Order-preserving so a resumed run whose redone events
    land in a different physical position would be caught."""
    face: list[tuple] = []
    for ev in _events(run_dir):
        k, p = ev["kind"], ev["payload"]
        if k not in DECISION_FACE_KINDS_V1:
            continue
        if k == "knowledge_updated":
            face.append((k, p["round_id"], p["fingerprint"]))
        elif k == "promotion_decision":
            face.append((k, p["round_id"], p["knowledge_fingerprint"],
                         tuple(x["cand_id"] for x in p["promoted"]),
                         tuple(x["cand_id"] for x in p["denied"])))
        elif k == "decision":
            c = p["content"]
            face.append((k, p["round_id"], tuple(c.get("candidates", ())),
                         tuple(c.get("basis", ())), c.get("knowledge_fingerprint")))
        elif k == "run_stop":
            face.append((k, p.get("exit_status")))
    return face


def _dedup_key_counts(run_dir: Path) -> dict[tuple, int]:
    """Count decision-face events per dedup key (kind, *key). Every value must be 1 — a
    resumed/redone round must not double-emit any guarded decision-face event."""
    counts: dict[tuple, int] = {}
    for ev in _events(run_dir):
        entry = RunStore._decision_face_key_fp(ev["kind"], ev["payload"])
        if entry is None:
            continue
        full_key = (ev["kind"], *entry[0])
        counts[full_key] = counts.get(full_key, 0) + 1
    return counts


def _wet_issued_by_round(run_dir: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    for e in _events(run_dir, "wet_leg_issued"):
        rid = e["payload"]["round_id"]
        counts[rid] = counts.get(rid, 0) + 1
    return counts


def _crash_at(target_point: str, target_round: int):
    """A crash-injection hook (default seam is no-op): raises _SimulatedCrash exactly once at
    the target killpoint + round to simulate a hard crash there."""
    def hook(point: str, round_id: int) -> None:
        if point == target_point and round_id == target_round:
            raise _SimulatedCrash(f"injected crash at {point} round {round_id}")
    return hook


# =============================================================== the uninterrupted baseline


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> Path:
    """One clean, uninterrupted two-round flipped-face run, shared across the matrix so the
    interrupted runs each compare against a single reference (keeps runtime sane)."""
    run_dir = tmp_path_factory.mktemp("phase4_baseline") / "run"
    run_mcl_loop(
        _DOMAIN, rounds=2, seed=7, out_dir=run_dir,
        certification=_agg_cert(), truth_profile=_TRUTH,
    )
    return run_dir


# =============================================================== the six-killpoint matrix

# (killpoint, crash_round). I1-I5 crash inside round 1 (round 0 completed+checkpointed first,
# giving a resume base); I6 crashes after round 0's checkpoint so the resume runs round 1 fresh
# (I6 is the clean-boundary case: the crashed round is fully done, only run_stop is missing).
_KILLPOINTS = [
    pytest.param("I1", 1, id="I1_dry_run_done_pre_ingest"),
    pytest.param("I2", 1, id="I2_wet_judged_pre_certify"),
    pytest.param("I3", 1, id="I3_decide_done_pre_apply"),
    pytest.param("I4", 1, id="I4_apply_done_pre_emit"),
    pytest.param("I5", 1, id="I5_emit_done_pre_checkpoint"),
    pytest.param("I6", 0, id="I6_checkpoint_done_pre_next_round"),
]


@pytest.mark.parametrize("killpoint, crash_round", _KILLPOINTS)
def test_interruption_matrix(tmp_path, baseline, killpoint, crash_round):
    """Interrupt at ``killpoint``, resume, assert the three exactly-once invariants against the
    uninterrupted baseline. KILL: drop the store decision-face dedup guard and I5 double-emits a
    claim_decision (invariant a red); rebuild the ledger from post-round instead of pre-round
    state and the e-product double-multiplies (invariant b red)."""
    part = tmp_path / "part"

    # crash at the pinned killpoint (hard crash: no run_stop emitted).
    with pytest.raises(_SimulatedCrash):
        run_mcl_loop(
            _DOMAIN, rounds=2, seed=7, out_dir=part,
            certification=_agg_cert(), truth_profile=_TRUTH,
            interrupt_hook=_crash_at(killpoint, crash_round),
        )

    # resume: event-log-as-truth (consume the torn round's persisted results / re-run cleanly).
    run_mcl_loop(
        _DOMAIN, rounds=2, seed=7, out_dir=part,
        certification=_agg_cert(), truth_profile=_TRUTH, resume=True,
    )

    # (a) NO duplicate decision-face events — every dedup key appears exactly once.
    assert all(n == 1 for n in _dedup_key_counts(part).values()), _dedup_key_counts(part)
    # both rounds' decision faces are present (the resume actually completed the run).
    assert len(_claim_decisions(part)) == len(_claim_decisions(baseline)) == 2

    # (b) exactly-once state: ledger, accumulated e-process, claim statistic sequence — bitwise.
    assert _ledger_effective(part) == _ledger_effective(baseline)
    assert _cert_state(part) == _cert_state(baseline)  # e-product not double-multiplied (item #2)
    assert _claim_seq(part) == _claim_seq(baseline)

    # (c) decision surface bitwise equal.
    assert _decision_surface(part) == _decision_surface(baseline)

    # wet non-replay: the wet leg for any round was issued at most ONCE (a consumed torn round
    # must not re-issue its already-issued wet commands).
    assert all(n == 1 for n in _wet_issued_by_round(part).values()), _wet_issued_by_round(part)


# =============================================================== dedup guard (discriminative)


def _kn_payload(round_id: int, fp: str) -> dict:
    return {"pv": 1, "round_id": round_id, "fingerprint": fp,
            "n_hypotheses": 1, "n_claims": 1}


def test_dedup_same_key_same_fingerprint_is_idempotent_skip(tmp_path):
    """Same dedup key + SAME content fingerprint => idempotent skip (the resumed round
    reproduced the decision). KILL: if the guard appended anyway, read_events would show 2
    knowledge_updated for round 0 and the second call would not return None."""
    store = RunStore(tmp_path / "run")
    first = store.append_decision_face_event(
        "knowledge_updated", _kn_payload(0, "fp_a"),
        dedup_key=(0,), content_fingerprint="fp_a",
    )
    second = store.append_decision_face_event(
        "knowledge_updated", _kn_payload(0, "fp_a"),
        dedup_key=(0,), content_fingerprint="fp_a",
    )
    assert first is not None and second is None  # second is the idempotent skip
    assert len(store.read_events("knowledge_updated")) == 1


def test_dedup_same_key_different_fingerprint_raises_loudly(tmp_path):
    """Same dedup key + DIFFERENT content fingerprint => loud NondeterminismError (a redo that
    did NOT reproduce the decision bitwise — never silently pick one). KILL: if the guard
    degraded this to a skip, a forked/nondeterministic redo would pass silently."""
    store = RunStore(tmp_path / "run")
    store.append_decision_face_event(
        "knowledge_updated", _kn_payload(0, "fp_a"),
        dedup_key=(0,), content_fingerprint="fp_a",
    )
    with pytest.raises(NondeterminismError):
        store.append_decision_face_event(
            "knowledge_updated", _kn_payload(0, "fp_b"),
            dedup_key=(0,), content_fingerprint="fp_b",
        )


def test_dedup_index_rebuilds_from_log_across_handles(tmp_path):
    """A fresh RunStore handle (the resume-in-a-new-process shape) rebuilds the dedup index
    from the event log alone: the same key skips, a divergent one raises."""
    run_dir = tmp_path / "run"
    RunStore(run_dir).append_decision_face_event(
        "claim_decision", {"round_id": 0, "claim_id": "c1",
                           "provenance_fingerprint": "sha:1"},
        dedup_key=(0, "c1"), content_fingerprint="sha:1",
    )
    reopened = RunStore(run_dir, create=False)  # index not built in memory -> rebuild from log
    assert reopened.append_decision_face_event(
        "claim_decision", {"round_id": 0, "claim_id": "c1",
                           "provenance_fingerprint": "sha:1"},
        dedup_key=(0, "c1"), content_fingerprint="sha:1",
    ) is None
    with pytest.raises(NondeterminismError):
        reopened.append_decision_face_event(
            "claim_decision", {"round_id": 0, "claim_id": "c1",
                               "provenance_fingerprint": "sha:2"},
            dedup_key=(0, "c1"), content_fingerprint="sha:2",
        )


# =============================================================== additive-key read tolerance


def test_knowledge_updated_round_id_write_strict_read_tolerant(tmp_path):
    """Item #5 discipline: historical run logs are immutable evidence, so an additive required
    key (round_id on knowledge_updated) is WRITE-strict but READ-tolerant. A legacy-shaped event
    (no round_id) validates on read; the strict (write-era) gate flags it. KILL: removing the
    write-strict branch (always tolerant) makes the strict assertion green — the discipline is
    lost; removing the read-tolerance breaks signed-off logs (the tolerant assertion)."""
    store = RunStore(tmp_path / "run")
    legacy = {"seq": 0, "kind": "knowledge_updated",
              "payload": {"fingerprint": "x", "n_hypotheses": 1, "n_claims": 1}}  # no round_id

    assert store.validate_event_payloads([legacy]) == []  # READ: legacy tolerated
    strict = store.validate_event_payloads([legacy], legacy_tolerant=False)
    assert strict and strict[0]["keys"] == ["round_id"]  # WRITE: new emissions must carry it

    # a pre-existing required key is NEVER weakened, even on the tolerant read path.
    missing_fp = {"seq": 1, "kind": "knowledge_updated",
                  "payload": {"round_id": 0, "n_hypotheses": 1, "n_claims": 1}}
    assert store.validate_event_payloads([missing_fp])  # fingerprint still required on read


# =============================================================== forked-resume detection


def _rewrite_event_line(run_dir: Path, seq: int, mutate) -> None:
    """Rewrite the events.jsonl line at ``seq`` by applying ``mutate(record)`` (keeping valid
    JSON) — simulating a tampered / forked-history byte after the checkpoint was written."""
    p = run_dir / "events.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        rec = json.loads(line)
        if rec.get("seq") == seq:
            mutate(rec)
        out.append(json.dumps(rec, ensure_ascii=False))
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_forked_resume_refused_on_tampered_event(tmp_path):
    """Tamper one logged event after the checkpoint was written; resume must refuse loudly
    rather than silently adopt the diverged branch. KILL: drop the (last_event_seq,
    last_event_sha256) anchor check and the tampered log resumes as if authentic."""
    run_dir = tmp_path / "run"
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=run_dir,
                 certification=_agg_cert(), truth_profile=_TRUTH)

    ckpt = RunStore(run_dir, create=False).read_checkpoint()
    last_seq = ckpt["last_event_seq"]
    assert last_seq is not None and ckpt["last_event_sha256"]  # anchor was recorded

    # tamper the anchored event's payload (still valid JSON, seq unchanged so read_events parses
    # it) -> its content hash no longer matches the checkpoint anchor.
    _rewrite_event_line(run_dir, last_seq,
                        lambda rec: rec["payload"].__setitem__("completed_rounds", 999))

    with pytest.raises(ForkedResumeError):
        run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=run_dir,
                     certification=_agg_cert(), truth_profile=_TRUTH, resume=True)


# =============================================================== wet non-replay invariant


def test_wet_non_replay_refuses_incomplete_persisted_issuance(tmp_path):
    """The wet leg is a rewindable(False) segment: if round N's wet leg was issued but its
    persisted results are incomplete, already-issued wet commands must not be replayed and the
    logged results cannot be consumed -> loud WetReplayError. KILL: drop the n_wells
    completeness check and resume would silently consume a truncated wet result set."""
    part = tmp_path / "part"
    # crash at I2 (round 1 wet issued + persisted, before certify).
    with pytest.raises(_SimulatedCrash):
        run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=part,
                     certification=_agg_cert(), truth_profile=_TRUTH,
                     interrupt_hook=_crash_at("I2", 1))

    # delete ONE persisted round-1 wet observation while the wet_leg_issued(n_wells) marker
    # stays -> the persisted results are now incomplete.
    store = RunStore(part, create=False)
    r1_wet = [o for o in store.list_observations(round_id=1) if o.raw_ref.kind == "wet"]
    assert r1_wet, "round 1 wet leg should have issued + persisted observations at I2"
    (part / "observations" / f"{r1_wet[0].obs_id}.json").unlink()

    with pytest.raises(WetReplayError):
        run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=part,
                     certification=_agg_cert(), truth_profile=_TRUTH, resume=True)
