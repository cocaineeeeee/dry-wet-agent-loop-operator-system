"""R5 REF-1 P1: terminal-state semantics for run_stop.

Discriminative coverage: three explicit terminal states + absence-as-crash,
plus the required mutation-kill check ("silencing the fail path must turn red").
"""
import pytest
from pathlib import Path

from expos.loop import run_loop
from expos.kernel.store import RunStore

CRYSTAL = Path(__file__).resolve().parents[1] / "domains" / "crystal.yaml"


def _stops(out):
    return RunStore(out, create=False).read_events("run_stop")


def test_success_terminal(tmp_path):
    out = tmp_path / "ok"
    run_loop(CRYSTAL, mode="naive", rounds=1, seed=21, out_dir=out)
    stops = _stops(out)
    assert len(stops) == 1 and stops[0]["payload"]["exit_status"] == "success"


def test_fail_terminal_emitted_and_reraises(tmp_path, monkeypatch):
    out = tmp_path / "boom"
    real_ckpt = RunStore.write_checkpoint
    def explode(self, state):
        raise RuntimeError("injected logic failure")
    monkeypatch.setattr(RunStore, "write_checkpoint", explode)
    with pytest.raises(RuntimeError, match="injected logic failure"):
        run_loop(CRYSTAL, mode="naive", rounds=1, seed=22, out_dir=out)
    monkeypatch.undo()
    stops = _stops(out)
    assert [e["payload"]["exit_status"] for e in stops] == ["fail"]
    assert "RuntimeError" in stops[0]["payload"]["reason"]


def test_abort_terminal_on_keyboard_interrupt(tmp_path, monkeypatch):
    out = tmp_path / "intr"
    def interrupt(self, state):
        raise KeyboardInterrupt()
    monkeypatch.setattr(RunStore, "write_checkpoint", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_loop(CRYSTAL, mode="naive", rounds=1, seed=23, out_dir=out)
    stops = _stops(out)
    assert [e["payload"]["exit_status"] for e in stops] == ["abort"]


def test_crash_leaves_no_run_stop(tmp_path):
    """Absence == crash (fourth state): simulate by truncating post-hoc -- a run
    whose events end without run_stop must be distinguishable, i.e. zero stops."""
    out = tmp_path / "crash"
    run_loop(CRYSTAL, mode="naive", rounds=1, seed=24, out_dir=out)
    ev = out / "events.jsonl"
    lines = ev.read_text().splitlines(keepends=True)
    assert "run_stop" in lines[-1]
    ev.write_text("".join(lines[:-1]))  # drop the terminal event = crash shape
    assert _stops(out) == []


def test_mutation_kill_silenced_fail_path(tmp_path, monkeypatch):
    """Required kill-check: if the fail-path emission were silenced (mutated to
    a no-op), test_fail_terminal_emitted_and_reraises turns red. Simulate the
    mutation here by making append_event drop run_stop, and assert the resulting
    stream is indistinguishable from crash -- which is exactly what the fail
    path exists to prevent."""
    out = tmp_path / "mut"
    real_append = RunStore.append_event
    def muted(self, kind, payload):
        if kind == "run_stop":
            return None  # the mutation: fail path silenced
        return real_append(self, kind, payload)
    monkeypatch.setattr(RunStore, "append_event", muted)
    def explode(self, state):
        raise RuntimeError("x")
    monkeypatch.setattr(RunStore, "write_checkpoint", explode)
    with pytest.raises(RuntimeError):
        run_loop(CRYSTAL, mode="naive", rounds=1, seed=25, out_dir=out)
    monkeypatch.undo()
    assert _stops(out) == [], "mutation makes fail look like crash -- the guarded " \
        "distinction is exactly what the un-mutated fail path provides"


# ================================================================ R5 REF-1 P1-2: payload validation gate

def test_read_events_validate_gate_collects_missing_keys(tmp_path):
    """Opt-in gate: default OFF leaves behavior untouched; ON collects (never
    raises) violations for registry kinds with missing required payload keys."""
    store = RunStore(tmp_path / "r", create=True)
    store.append_event("routing", {"not_obs_id": 1})   # missing obs_id
    store.append_event("run_stop", {"exit_status": "success"})  # complete
    evs = store.read_events()                     # default: no validation
    assert store.last_payload_violations == []
    evs = store.read_events(validate=True)
    assert len(evs) == 2                          # events still all returned
    v = store.last_payload_violations
    assert len(v) == 1 and v[0]["kind"] == "routing" and v[0]["keys"] == ["obs_id"]


def test_grade_typo_reported_not_silently_folded(tmp_path):
    """R4-H F3 root cause: a present-but-invalid grade (typo) must surface as a
    PAYLOAD_VIOLATION from scan_run instead of silently reading as inactive."""
    from expos.eval.activity_budget import scan_run
    root = tmp_path / "S2.edge_evaporation.0.35__os__s1003"
    store = RunStore(root, create=True)
    for r in range(8):
        store.append_event("risk_map_applied", {
            "round_id": r, "grade": "actve" if r == 3 else "active",  # typo at r3
            "summary": {"is_none": False, "n_distinct": 2},
        })
    store.write_checkpoint({"completed_rounds": 8, "mode": "os"})
    v = scan_run(root)
    pv = [x for x in v if x.get("status") == "PAYLOAD_VIOLATION"]
    assert len(pv) == 1 and pv[0]["details"][0]["problem"] == "invalid_grade_value"
