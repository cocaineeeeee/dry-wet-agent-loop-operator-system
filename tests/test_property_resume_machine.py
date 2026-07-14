"""P12 — stateful resume-idempotency machine (M22 property-test batch).

The property-level generalisation of the six fixed killpoints (I1..I6): instead of
hand-picking crash points, a ``RuleBasedStateMachine`` searches the CRASH-POINT
PATH SPACE — arbitrary interleavings of {run a round's events, checkpoint,
crash+resume, resume-again, redo an uncheckpointed round} — and asserts the
resume-reconstruction invariants hold on every path.

SCOPE (per the batch's scope-control ruling): this drives the run at the
STORE/KERNEL level — the event log, the checkpoint, and the REAL resume-seam
reconstruction functions used by ``expos.mcl`` — NOT ``run_mcl_loop`` and NOT the
real dry/wet legs (the six-killpoint matrix already covers the full loop). The
seams exercised verbatim are:

  * ``RunStore.append_decision_face_event`` — exactly-once dedup of decision-face
    events (the ``knowledge_updated`` / ``claim_decision`` re-emission guard that
    makes a redone round idempotent);
  * ``RunStore.write_checkpoint`` / ``read_checkpoint`` — the ledger + cross-round
    certification-state snapshot round-trip;
  * ``mcl._verify_not_forked`` — forked/tampered-log refusal on resume;
  * ``mcl._ledger_from_checkpoint`` — deterministic ledger reconstruction (no event
    re-emission);
  * ``mcl._classify_resume_round`` — event-log-as-truth resume classification;
  * ``RunStore.reconcile_redo_rounds`` — crash-window orphan reconciliation.

The certification cross-round state is folded through the REAL
``qc.certification_stats.RoundState`` accumulator (a deterministic per-round
e-value), checkpointed, and restored — so the ``e_product`` exactly-once claim is
asserted against the real accumulator schema and the real checkpoint persistence.

Invariants (@invariant, every step): seq contiguity; NO duplicate decision-face
event per dedup key (the exactly-once substrate); the checkpointed ledger +
certification-state reconstruct bit-for-bit and forked-resume detection passes;
the reconstructed ledger's effective statuses are stable.

What is deliberately OUT OF REACH here (reported, not hidden): the full mcl-loop
exactly-once guarantee that a REDONE round RE-DERIVES rather than RE-FOLDS its
certification state end-to-end requires ``run_mcl_loop`` with real legs — covered
by the six-killpoint matrix. This machine proves the store/kernel SUBSTRATE of
that guarantee (dedup exactly-once on the event + bitwise checkpoint round-trip),
which is where a resume-determinism regression would first surface. See the
``consume_issued`` / ``consume_skipped`` classification paths, which are exercised
by the deterministic tests at the bottom (they need a wet-leg marker; the machine
models the crash-before-wet-issuance window, where redo is a full, dedup-guarded
re-emission).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from pathlib import Path

import pytest

if importlib.util.find_spec("hypothesis") is None:  # graceful skip w/o dev extra
    pytest.skip("hypothesis not installed (pip install -e '.[dev]')",
                allow_module_level=True)

from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

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
    apply_claim_deltas,
    emit_claim_decision,
)
from expos.kernel.store import RunStore
from expos.mcl import (
    _classify_resume_round,
    _ledger_from_checkpoint,
    _verify_not_forked,
)
from expos.qc.certification_stats import FILTRATION_ASSUMPTION, RoundState

_SCRATCH = os.environ.get("PROPTEST_SCRATCH") or tempfile.gettempdir()
_CLAIM_ID = "c_main"
_ROUND_E = 1.5  # deterministic per-round e-value folded into the e-product.


def _round_delta(round_id: int) -> ClaimDelta:
    """A DETERMINISTIC ClaimDelta for ``round_id`` (same bytes -> same provenance
    fingerprint every time it is rebuilt), so a redone round re-emits an IDENTICAL
    claim_decision that the store's dedup guard idempotent-skips. A DIFFERENT
    fingerprint for the same (round, claim) would (correctly) raise
    NondeterminismError — that divergence path is pinned by a deterministic test."""
    provenance = ProvenanceSnapshot(
        usage=ProvenanceUsage(consumed_knowledge_fingerprint=f"kfp{round_id}"),
        activity=ProvenanceActivity(
            decision_fn_id=REFERENCE_CERTIFICATION_FN_ID,
            decision_fn_version=REFERENCE_CERTIFICATION_FN_VERSION,
            criterion_version="v1",
        ),
    )
    return ClaimDelta(
        target_claim_id=_CLAIM_ID,
        status=ClaimDecisionStatus.SUPPORTED,
        new_content=ClaimVersionContent(
            statement=_CLAIM_ID, status=ClaimDecisionStatus.SUPPORTED
        ),
        evidence_strength=EvidenceStrength.STRONG,
        provenance=provenance,
    )


def _fold(prior: RoundState) -> RoundState:
    """Fold one round's evidence into the cross-round RoundState (the real
    accumulator schema): e_product *= _ROUND_E, rounds_observed += 1. A double-fold
    would move e_product to _ROUND_E**(2n) != _ROUND_E**n — the exactly-once
    discriminator."""
    return RoundState(
        claim_id=prior.claim_id,
        rounds_observed=prior.rounds_observed + 1,
        e_product=prior.e_product * _ROUND_E,
        per_round_e=prior.per_round_e + (_ROUND_E,),
        info_sum=prior.info_sum,
        weighted_effect_sum=prior.weighted_effect_sum,
        filtration_assumption=dict(FILTRATION_ASSUMPTION),
    )


class ResumeMachine(RuleBasedStateMachine):
    """Models one run's event log + checkpoint under arbitrary crash/resume paths.

    In-memory ``*_model`` fields are the harness oracle for what a correct
    reconstruction must reproduce; the ``checkpointed_*`` fields are the last
    durable snapshot (the resume source). ``emitted`` is the ground-truth set of
    (round_id, claim_id) claim_decision keys that have reached the log — the
    exactly-once oracle."""

    def __init__(self):
        super().__init__()
        self._dir = Path(tempfile.mkdtemp(prefix="p12_", dir=_SCRATCH))
        self.store = RunStore(self._dir / "run", create=True)
        self.next_round = 0            # round index to run next
        self.committed_rounds = 0      # checkpoint.completed_rounds (durable)
        self.in_flight = False         # a round's events are logged but not checkpointed
        # in-memory model (advances with the in-flight round)
        self.ledger_model = Ledger()
        self.cert_model: dict[str, dict] = {}
        # last durable snapshot (what a resume reconstructs from)
        self.checkpointed_ledger = Ledger()
        self.checkpointed_cert: dict[str, dict] = {}
        self.has_checkpoint = False
        self.emitted: set[tuple[int, str]] = set()

    def teardown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    # ---------------------------------------------------------------- rules

    @precondition(lambda self: not self.in_flight)
    @rule()
    def run_round(self):
        """Run round ``next_round``'s decision-face events + fold, WITHOUT
        checkpointing yet (the crash-before-checkpoint window). Re-running the same
        round after a resume re-emits IDENTICAL decision-face events, which the
        store dedup-guards to exactly-once; the fold advances the in-memory model
        exactly once from the (reset-to-checkpoint) base."""
        r = self.next_round
        kfp = f"kfp{r}"
        # knowledge_updated (dedup-guarded, key=(round,)): idempotent on redo.
        self.store.append_decision_face_event(
            "knowledge_updated",
            {"round_id": r, "fingerprint": kfp, "n_hypotheses": 0, "n_claims": 1},
            dedup_key=(r,),
            content_fingerprint=kfp,
        )
        # claim_decision (dedup-guarded, key=(round, claim_id)): the exactly-once
        # bullseye — a redone round re-emits the SAME delta and is skipped.
        delta = _round_delta(r)
        self.ledger_model, _ = apply_claim_deltas(self.ledger_model, [delta])
        landed = self.ledger_model.head(_CLAIM_ID)
        emit_claim_decision(
            self.store,
            round_id=r,
            delta=delta,
            final_status=delta.status,
            landed_version=(landed.version if landed else None),
        )
        self.emitted.add((r, _CLAIM_ID))
        # fold cross-round certification state (once).
        prior_json = self.cert_model.get(_CLAIM_ID)
        prior = (
            RoundState.model_validate(prior_json)
            if prior_json
            else RoundState(claim_id=_CLAIM_ID)
        )
        self.cert_model[_CLAIM_ID] = _fold(prior).model_dump(mode="json")
        self.in_flight = True

    @precondition(lambda self: self.in_flight)
    @rule()
    def checkpoint(self):
        """Durably checkpoint the in-flight round (completed_rounds = round+1),
        snapshotting the ledger + cross-round certification state. write_checkpoint
        also pins the forked-resume anchor (last_event seq + sha256)."""
        r = self.next_round
        self.store.write_checkpoint(
            {
                "completed_rounds": r + 1,
                "loop": "mcl",
                "claim_ledger": [
                    rec.model_dump(mode="json") for rec in self.ledger_model.claims
                ],
                "certification_state": self.cert_model,
            }
        )
        self.checkpointed_ledger = self.ledger_model
        self.checkpointed_cert = dict(self.cert_model)
        self.has_checkpoint = True
        self.committed_rounds = r + 1
        self.next_round = r + 1
        self.in_flight = False

    @rule()
    def crash_resume(self):
        """Hard crash + resume: rebuild a FRESH RunStore handle (new process) and
        reconstruct via the REAL mcl resume seams. An uncheckpointed in-flight round
        is discarded from the model (its events stay in the log) and will be redone
        by a later ``run_round`` as an idempotent, dedup-guarded re-emission — the
        crash-before-wet-issuance window (``_classify_resume_round`` -> None)."""
        self.store = RunStore(self._dir / "run", create=False)
        ckpt = self.store.read_checkpoint()
        if ckpt is None:
            # No durable checkpoint yet: resume is a fresh start.
            start_round = 0
            self.ledger_model = Ledger()
            self.cert_model = {}
        else:
            _verify_not_forked(self.store, ckpt)  # must not raise
            start_round = int(ckpt["completed_rounds"])
            self.ledger_model = _ledger_from_checkpoint(ckpt)
            self.cert_model = dict(ckpt.get("certification_state") or {})
            classified = _classify_resume_round(self.store, start_round)
            # The machine never emits a wet-leg marker, so classification is the
            # crash-before-wet window: re-execute the round in full.
            assert classified is None, (
                f"unexpected resume classification {classified!r} (the machine "
                "models the pre-wet-issuance window only)"
            )
            round_started = any(
                (e.get("payload") or {}).get("round_id") == start_round
                for e in self.store.read_events("knowledge_updated")
            )
            if round_started:
                self.store.reconcile_redo_rounds(start_round)
        self.next_round = start_round
        self.committed_rounds = start_round
        self.in_flight = False

    # ---------------------------------------------------------------- invariants

    @invariant()
    def seq_contiguous(self):
        seqs = [e["seq"] for e in self.store.read_events()]
        assert seqs == list(range(len(seqs))), f"non-contiguous seq: {seqs[:16]}"

    @invariant()
    def no_duplicate_decision_face(self):
        """Exactly-once substrate: no dedup-guarded decision-face event appears
        twice for the same (kind, *dedup_key) — the guard that makes each round's
        e-value/knowledge fold enter the record exactly once across crashes/redos."""
        seen: set[tuple] = set()
        for e in self.store.read_events():
            entry = self.store._decision_face_key_fp(
                e.get("kind", ""), e.get("payload") or {}
            )
            if entry is None:
                continue
            key = (e["kind"], *entry[0])
            assert key not in seen, f"duplicate decision-face event for key {key}"
            seen.add(key)
        # The set of claim_decision dedup keys in the log is EXACTLY the emitted
        # oracle set — one event per (round, claim), no more, no fewer, no matter
        # how many crashes/resumes/redos re-emitted it.
        cd_keys = {
            self.store._decision_face_key_fp("claim_decision", e["payload"])[0]
            for e in self.store.read_events("claim_decision")
        }
        assert cd_keys == self.emitted, (
            f"claim_decision keys {sorted(cd_keys)} != emitted {sorted(self.emitted)}"
        )

    @invariant()
    def checkpoint_reconstructs_bitwise(self):
        """The durable checkpoint reconstructs the ledger + certification state
        bit-for-bit, and forked-resume detection passes — the e_product exactly-once
        at rest (a double-folded state would not equal the model)."""
        if not self.has_checkpoint:
            return
        ckpt = self.store.read_checkpoint()
        assert ckpt is not None
        _verify_not_forked(self.store, ckpt)
        rebuilt = _ledger_from_checkpoint(ckpt)
        assert (
            rebuilt.canonical_json() == self.checkpointed_ledger.canonical_json()
        )
        assert ckpt.get("certification_state") == self.checkpointed_cert
        # e_product is exactly _ROUND_E ** rounds_observed (no double multiply).
        for cid, state_json in (ckpt.get("certification_state") or {}).items():
            state = RoundState.model_validate(state_json)
            assert state.e_product == pytest.approx(_ROUND_E**state.rounds_observed)
            assert len(state.per_round_e) == state.rounds_observed

    @invariant()
    def reconstructed_effective_statuses_stable(self):
        if not self.has_checkpoint:
            return
        ckpt = self.store.read_checkpoint()
        rebuilt = _ledger_from_checkpoint(ckpt)
        assert (
            rebuilt.effective_statuses()
            == self.checkpointed_ledger.effective_statuses()
        )


ResumeMachine.TestCase.settings = settings(
    max_examples=int(os.environ.get("P12_MAX_EXAMPLES", "120")),
    stateful_step_count=int(os.environ.get("P12_STEPS", "24")),
    deadline=None,
    derandomize=True,
    database=None,
)


class TestResumeMachine(ResumeMachine.TestCase):
    pass


# ============================================================ deterministic anchors


def _run_dir(tmp_path):
    return tmp_path / "run"


def test_redo_reemits_claim_decision_idempotently(tmp_path):
    """A redone round re-emitting the SAME claim_decision is skipped (exactly-once):
    the log holds one claim_decision per (round, claim), seq stays contiguous."""
    store = RunStore(_run_dir(tmp_path), create=True)
    delta = _round_delta(0)
    first = emit_claim_decision(
        store, round_id=0, delta=delta, final_status=delta.status, landed_version=1
    )
    assert first is not None
    # resume = fresh handle; the dedup index rebuilds from the log.
    store2 = RunStore(_run_dir(tmp_path), create=False)
    again = emit_claim_decision(
        store2, round_id=0, delta=_round_delta(0), final_status=delta.status,
        landed_version=1,
    )
    assert again is None  # idempotent skip
    cds = store2.read_events("claim_decision")
    assert len(cds) == 1
    seqs = [e["seq"] for e in store2.read_events()]
    assert seqs == list(range(len(seqs)))


def test_diverged_redo_raises_nondeterminism(tmp_path):
    """A redo that emits a DIFFERENT claim_decision for the same (round, claim)
    (a forked/nondeterministic history) is refused loudly — never silently
    resolved. This is the negative face of the exactly-once guard."""
    from expos.kernel.store import NondeterminismError

    store = RunStore(_run_dir(tmp_path), create=True)
    d0 = _round_delta(0)
    emit_claim_decision(
        store, round_id=0, delta=d0, final_status=d0.status, landed_version=1
    )
    # A delta with a different provenance fingerprint (different consumed kfp) for
    # the SAME (round, claim) diverges.
    diverged_prov = ProvenanceSnapshot(
        usage=ProvenanceUsage(consumed_knowledge_fingerprint="DIVERGED"),
        activity=ProvenanceActivity(
            decision_fn_id=REFERENCE_CERTIFICATION_FN_ID,
            decision_fn_version=REFERENCE_CERTIFICATION_FN_VERSION,
            criterion_version="v1",
        ),
    )
    diverged = ClaimDelta(
        target_claim_id=_CLAIM_ID,
        status=ClaimDecisionStatus.SUPPORTED,
        new_content=ClaimVersionContent(
            statement=_CLAIM_ID, status=ClaimDecisionStatus.SUPPORTED
        ),
        evidence_strength=EvidenceStrength.STRONG,
        provenance=diverged_prov,
    )
    store2 = RunStore(_run_dir(tmp_path), create=False)
    with pytest.raises(NondeterminismError):
        emit_claim_decision(
            store2, round_id=0, delta=diverged, final_status=diverged.status,
            landed_version=1,
        )


def test_classify_resume_round_paths(tmp_path):
    """Exercise the three ``_classify_resume_round`` branches the machine does not
    reach (it models only the pre-wet-issuance None path):
      * no wet marker -> None (re-execute in full);
      * wet_leg_skipped -> ("consume_skipped", []);
      * wet_leg_issued with n_wells matching the persisted wet obs (0 here) ->
        ("consume_issued", []).
    """
    # None: no wet marker for the round.
    s = RunStore(_run_dir(tmp_path) / "a", create=True)
    assert _classify_resume_round(s, 0) is None

    # consume_skipped
    s2 = RunStore(_run_dir(tmp_path) / "b", create=True)
    s2.append_event("wet_leg_skipped", {"round_id": 0})
    kind, obs = _classify_resume_round(s2, 0)
    assert kind == "consume_skipped" and obs == []

    # consume_issued with zero wells (no persisted wet observations required).
    s3 = RunStore(_run_dir(tmp_path) / "c", create=True)
    s3.append_event("wet_leg_issued", {"round_id": 0, "exp_id": "e0", "n_wells": 0})
    kind, obs = _classify_resume_round(s3, 0)
    assert kind == "consume_issued" and obs == []
