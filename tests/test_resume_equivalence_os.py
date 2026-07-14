"""E2E3-F1 regression: os-family resume must be no-crash equivalent (I4).

Root cause (fixed in expos/planner/policy.py): ``reconcile_redo_rounds`` rolls
back materialized views of redone rounds but, by design, keeps the append-only
event log. A crashed attempt therefore leaves stale ``action_consumed`` events
for the round about to be redone; ``TrustAwarePlanner`` read the *full* event
log to decide "already consumed" and silently skipped the closed-loop
remediation actions (REMEASURE/DISAMBIGUATION) the redo was supposed to
re-issue. Observed drift: redo-round candidates
``{arbiter:endogenous, bo, sobol}`` collapsed to ``{bo, sobol}`` and
``best_trusted`` shifted -- an I4 break that hit only the os / os-soft arms
(the stateless BaselinePlanner arms were immune).

This is a C7-style "crash-point x os-arm" equivalence matrix using *faithful*
crash injection (whole-checkpoint rollback + torn tail -- an unfaithful
"only rewind completed_rounds" variant fabricates inconsistent budget/planner
state and false-reports other invariants). Each crashed run is asserted
bit-for-bit equivalent to a no-crash reference (observation counts, best_trusted,
model snapshots). ``test_redo_round_replays_remediation_actions`` is the
discriminative anchor: it asserts the redo round re-issues the arbiter
remediation candidates -- remove the read-side filter in ``_pending_actions``
and this assertion goes red (arbiter count collapses to 0).
"""

import json
from collections import Counter

import pytest

from expos.kernel.objects import TrustLevel
from expos.kernel.store import RunStore
from expos.loop import run_loop

from tests.test_loop_e2e import CRYSTAL

ROUNDS = 3
SEED = 81
ARMS = ("os", "os-soft")
CRASH_POINTS = (1, 2)  # crash after completing 1 or 2 rounds -> redo that round


# ------------------------------------------------------------ faithful crash

def _faithful_crash(run_dir, snap_bytes):
    """Simulate a faithful single-writer crash: atomically restore the entire
    checkpoint.json from an earlier internally-consistent snapshot (models
    "obs/exp already on disk, write_checkpoint not yet done") and append a
    torn (half-written) tail line to events.jsonl."""
    ck = run_dir / "checkpoint.json"
    tmp = ck.with_suffix(".json.crashtmp")
    tmp.write_bytes(snap_bytes)
    tmp.replace(ck)
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"seq": 99999, "kind": "status_transi')  # unparsable torn tail


def _build_arm(base, arm):
    """Reference (no-crash) run + one faithfully-crashed resume per crash point."""
    ref = base / f"{arm}_ref"
    run_loop(CRYSTAL, mode=arm, rounds=ROUNDS, seed=SEED, out_dir=ref)
    crashed = {}
    for ca in CRASH_POINTS:
        out = base / f"{arm}_crash{ca}"
        # run to a consistent checkpoint at completed_rounds == ca
        run_loop(CRYSTAL, mode=arm, rounds=ca, seed=SEED, out_dir=out)
        snap = (out / "checkpoint.json").read_bytes()
        # run one more round (its obs/exp/events land on disk)...
        run_loop(CRYSTAL, mode=arm, rounds=ca + 1, seed=SEED, out_dir=out, resume=True)
        # ...but the crash rolls the checkpoint back to the earlier snapshot
        _faithful_crash(out, snap)
        # resume redoes round `ca` and finishes
        run_loop(CRYSTAL, mode=arm, rounds=ROUNDS, seed=SEED, out_dir=out, resume=True)
        crashed[ca] = out
    return ref, crashed


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    base = tmp_path_factory.mktemp("resume_equiv")
    return {arm: _build_arm(base, arm) for arm in ARMS}


# ------------------------------------------------------------ helpers

def _best_trusted(run_dir):
    s = RunStore(run_dir, create=False)
    vals = [(o.result.value, o.round_id)
            for o in s.list_observations(trust=TrustLevel.TRUSTED)
            if o.result.value is not None and not o.is_control]
    return max(vals) if vals else None


def _source_dist(run_dir, round_id):
    s = RunStore(run_dir, create=False)
    exp = [e for e in s.list_experiments() if e.round_id == round_id][0]
    return Counter(c.source for c in exp.candidates)


def _counts(run_dir):
    s = RunStore(run_dir, create=False)
    return {t: len(s.list_observations(trust=t))
            for t in (None, TrustLevel.TRUSTED, TrustLevel.SUSPECT, TrustLevel.FAILED)}


# ------------------------------------------------------------ equivalence matrix

@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("crash_after", CRASH_POINTS)
def test_observation_counts_equivalent(matrix, arm, crash_after):
    ref, crashed = matrix[arm]
    assert _counts(crashed[crash_after]) == _counts(ref)


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("crash_after", CRASH_POINTS)
def test_best_trusted_equivalent(matrix, arm, crash_after):
    ref, crashed = matrix[arm]
    assert _best_trusted(crashed[crash_after]) == _best_trusted(ref)


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("crash_after", CRASH_POINTS)
def test_model_snapshots_bitwise_equivalent(matrix, arm, crash_after):
    ref, crashed = matrix[arm]
    for r in range(ROUNDS):
        a = json.loads((crashed[crash_after] / "models" / f"snapshot_r{r}.json").read_text())
        b = json.loads((ref / "models" / f"snapshot_r{r}.json").read_text())
        assert a["snapshot"] == b["snapshot"], f"{arm} crash@{crash_after} snapshot_r{r}"
        assert a["n_train"] == b["n_train"], f"{arm} crash@{crash_after} n_train r{r}"


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("crash_after", CRASH_POINTS)
def test_redo_round_replays_remediation_actions(matrix, arm, crash_after):
    """Discriminative anchor. The redo round is `crash_after` (0-indexed round
    id == crash_after). Its arbiter remediation candidates must survive the
    crash. Pre-fix, stale action_consumed events suppress them and the
    distribution collapses to {bo, sobol} with arbiter:endogenous == 0."""
    ref, crashed = matrix[arm]
    redo_round = crash_after
    ref_dist = _source_dist(ref, redo_round)
    crash_dist = _source_dist(crashed[crash_after], redo_round)
    # the reference redo round is expected to carry endogenous remediation
    # candidates in this scenario -- guard the anchor is meaningful
    assert ref_dist.get("arbiter:endogenous", 0) > 0, (
        f"{arm} ref round{redo_round} has no remediation candidates -- "
        "scenario no longer exercises the bug"
    )
    assert crash_dist == ref_dist, (
        f"{arm} crash@{crash_after} redo-round source dist drifted: "
        f"crash={dict(crash_dist)} ref={dict(ref_dist)}"
    )


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("crash_after", CRASH_POINTS)
def test_redo_reissues_action_with_reused_stable_uid(matrix, arm, crash_after):
    """Stable-uid reuse across redo (red-team G4 supplement).

    An endogenous action's ``item_uid`` is ``endogenous:ACTION:cand_id`` and is
    cand_id-derived, hence stable across a redo (expos/planner/arbiter.py). The
    crashed-before attempt therefore left a stale ``action_consumed`` record
    under the *same* uid the redo re-generates -- a plain "have I ever consumed
    this uid?" check would wrongly skip it. The round-scoped filter must treat
    that stale record (round_id >= from_round, before the reconcile marker) as
    superseded so the redo re-consumes the action. Assert this concretely: the
    same endogenous uid is consumed both before the reconcile marker (stale) and
    after it (redo)."""
    _ref, crashed = matrix[arm]
    s = RunStore(crashed[crash_after], create=False)
    recon_seq = s.read_events("redo_reconciliation")[-1]["seq"]
    consumed = [e for e in s.read_events("action_consumed")
                if e["payload"]["round_id"] == crash_after
                and e["payload"]["source"] == "endogenous"]
    pre = {e["payload"]["item_uid"] for e in consumed if e["seq"] < recon_seq}
    post = {e["payload"]["item_uid"] for e in consumed if e["seq"] > recon_seq}
    reused = pre & post
    assert reused, (
        f"{arm} crash@{crash_after}: expected an endogenous uid consumed both "
        f"pre-crash and in the redo (stable reuse across rollback); "
        f"pre={pre} post={post}"
    )
    assert all(u.startswith("endogenous:") for u in reused)


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("crash_after", CRASH_POINTS)
def test_reconciliation_logged_and_events_readable(matrix, arm, crash_after):
    """The crash path is loud (redo_reconciliation logged) and the audit log
    stays append-only and readable (seq contiguous, torn tail healed)."""
    _ref, crashed = matrix[arm]
    s = RunStore(crashed[crash_after], create=False)
    recon = s.read_events("redo_reconciliation")
    assert recon and recon[-1]["payload"]["from_round"] == crash_after
    seqs = [e["seq"] for e in s.read_events()]
    assert seqs == list(range(len(seqs)))
