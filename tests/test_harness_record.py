"""Eval-harness provenance record tests (case 2, B): write/reconcile, tamper
detection, and the truth-blindness guard (no OS decision-path module reads it).

The record layer is exercised against a SYNTHETIC run dir (config.json + events.jsonl)
so the module is covered fully and cheaply, plus one real CLI ``run --loop mcl``
integration to prove the write is wired at the evaluation entry with a correct
reconciliation key recomputed independently from the actual events.jsonl.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from expos.eval.harness_record import (
    HARNESS_RECORD_NAME,
    EvalHarnessSpec,
    events_high_water,
    read_harness_record,
    verify_harness_record,
    write_harness_record,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _synthetic_run(run_dir: Path, config_fingerprint: str = "cfgfp123") -> None:
    """A minimal run dir: an OS config.json + a two-line events.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(
        json.dumps({"domain": "d", "seed": 7, "config_fingerprint": config_fingerprint}),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps({"seq": 0, "kind": "run_start"}) + "\n"
        + json.dumps({"seq": 1, "kind": "run_stop"}) + "\n",
        encoding="utf-8",
    )


def _spec(seed: int = 7) -> EvalHarnessSpec:
    return EvalHarnessSpec(
        truth_profile="polar_high", noise_sd=0.0, interleave=True,
        root_seed=seed, reader_seed=999, agent_backend="template", mode="os",
    )


# --------------------------------------------------------------------------- write

def test_write_places_peer_file_and_reconciliation_key(tmp_path):
    run = tmp_path / "run_a"
    _synthetic_run(run)
    path = write_harness_record(run, _spec().as_knobs())

    # Physical separation: a peer file alongside config.json (not inside it).
    assert path == run / HARNESS_RECORD_NAME
    assert path.exists()
    assert (run / "config.json").exists()  # untouched OS config

    rec = read_harness_record(run)
    assert rec["schema"] == "harness_record/v1"
    assert rec["knobs"]["truth_profile"] == "polar_high"
    assert rec["knobs"]["noise_sd"] == 0.0
    assert rec["knobs"]["reader_seed"] == 999

    key = rec["reconciliation_key"]
    assert key["run_dir_name"] == "run_a"
    assert key["seed"] == 7
    assert key["os_config_fingerprint"] == "cfgfp123"  # cross-pinned to the OS run

    # Independently recompute the high-water sha (read bytes, sha up to last newline).
    data = (run / "events.jsonl").read_bytes()
    hw = data[: data.rfind(b"\n") + 1]
    assert key["events_high_water_sha256"] == hashlib.sha256(hw).hexdigest()
    assert key["events_high_water_bytes"] == len(hw)

    # code provenance + numpy anchor present.
    prov = rec["code_provenance"]
    assert len(prov["sim_reader_sha256"]) == 64
    assert len(prov["screen_sha256"]) == 64
    assert prov["numpy_version"]


def test_fresh_record_verifies_clean(tmp_path):
    run = tmp_path / "run_b"
    _synthetic_run(run)
    write_harness_record(run, _spec().as_knobs())
    v = verify_harness_record(run)
    assert v.ok, v.reasons


def test_unknown_knob_rejected(tmp_path):
    run = tmp_path / "run_c"
    _synthetic_run(run)
    knobs = _spec().as_knobs()
    knobs["sneaky_unrecorded_knob"] = 1  # not in the white-list
    with pytest.raises(Exception) as e:
        write_harness_record(run, knobs)
    assert "unknown harness knob" in str(e.value)


# --------------------------------------------------------------------------- tamper

def test_tamper_events_flags_mismatch(tmp_path):
    run = tmp_path / "run_d"
    _synthetic_run(run)
    write_harness_record(run, _spec().as_knobs())
    assert verify_harness_record(run).ok

    # Edit events.jsonl after the record was written -> high-water sha drifts.
    (run / "events.jsonl").write_text(
        json.dumps({"seq": 0, "kind": "run_start"}) + "\n"
        + json.dumps({"seq": 1, "kind": "TAMPERED"}) + "\n",
        encoding="utf-8",
    )
    v = verify_harness_record(run)
    assert not v.ok
    assert any("events high-water mismatch" in r for r in v.reasons)


def test_tamper_record_body_flags_fingerprint(tmp_path):
    run = tmp_path / "run_e"
    _synthetic_run(run)
    write_harness_record(run, _spec().as_knobs())

    # Hand-edit the record's knobs but leave the stored fingerprint -> mismatch.
    rec = read_harness_record(run)
    rec["knobs"]["truth_profile"] = "nonpolar_high"
    (run / HARNESS_RECORD_NAME).write_text(json.dumps(rec), encoding="utf-8")

    v = verify_harness_record(run)
    assert not v.ok
    assert any("record_fingerprint mismatch" in r for r in v.reasons)


def test_high_water_excludes_torn_tail(tmp_path):
    run = tmp_path / "run_f"
    _synthetic_run(run)
    sha_before, bytes_before = events_high_water(run)
    # Append a torn (no trailing newline) partial line -> high-water unchanged.
    with (run / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "kind": "partial"')  # no newline: not a complete line
    sha_after, bytes_after = events_high_water(run)
    assert (sha_after, bytes_after) == (sha_before, bytes_before)


# ----------------------------------------------------------------- truth-blindness

def test_truth_blindness_no_os_module_reads_harness_record():
    """AST guard: NO OS decision-path package imports/references harness_record.
    The knobs enter a record SINK (written once at the CLI eval entry), never a
    decision SOURCE. cli.py and eval/ are allowed (the eval entry + the module
    itself); kernel/qc/planner/agent/models must be blind to it."""
    forbidden = ("kernel", "qc", "planner", "agent", "models")
    offenders: list[str] = []
    for pkg in forbidden:
        base = REPO_ROOT / "expos" / pkg
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            src = py.read_text(encoding="utf-8")
            if "harness_record" not in src:
                continue
            # A bare textual mention inside a comment/string is not an import; assert
            # there is no import statement or attribute access referencing it.
            tree = ast.parse(src, filename=str(py))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module \
                        and "harness_record" in node.module:
                    offenders.append(f"{py}: from {node.module}")
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        if "harness_record" in a.name:
                            offenders.append(f"{py}: import {a.name}")
                elif isinstance(node, ast.Attribute) and node.attr == "harness_record":
                    offenders.append(f"{py}: .harness_record attribute")
    assert offenders == [], f"OS decision-path modules must not read harness_record: {offenders}"


# ----------------------------------------------------------------- CLI integration

def test_cli_run_mcl_writes_record_with_correct_reconciliation_key(tmp_path):
    """Cheap full-loop run through the CLI eval entry: the harness record lands in
    the run dir with a reconciliation key that recomputes independently from the
    actual events.jsonl, and it verifies clean."""
    from expos.cli import main

    out = tmp_path / "mcl_run"
    rc = main([
        "run", "--domain", "solvent_screen", "--loop", "mcl",
        "--rounds", "1", "--seed", "7", "--out", str(out),
    ])
    assert rc == 0
    rec_path = out / HARNESS_RECORD_NAME
    assert rec_path.exists(), "CLI must write the harness record at the eval entry"

    rec = read_harness_record(out)
    assert rec["knobs"]["truth_profile"] == "polar_high"  # CLI default face
    key = rec["reconciliation_key"]
    assert key["run_dir_name"] == "mcl_run"
    assert key["os_config_fingerprint"]  # pinned to the real OS config.json

    data = (out / "events.jsonl").read_bytes()
    hw = data[: data.rfind(b"\n") + 1]
    assert key["events_high_water_sha256"] == hashlib.sha256(hw).hexdigest()

    assert verify_harness_record(out).ok
