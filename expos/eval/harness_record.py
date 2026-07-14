"""Eval-harness provenance record (case 2, B part 1): an INDEPENDENT peer file
that captures the evaluation-device knobs which must NEVER enter the OS run record.

Blindspot this closes (REF-CFG §Convergence(c) / INDEX_M22_EVALPROV): two runs can
carry a byte-identical ``config.json`` yet reach opposite verdicts, because the
knobs that decide the outcome -- ``truth_profile`` (mcl.py:851), ``noise_sd``
(mcl.py:850, pinned 0.0), ``interleave`` (mcl.py:~1106, pinned True), and the
derived reader sub-seed -- are threaded call-side and never reach
``store.save_config`` (mcl.py:900-904). ``config_fingerprint`` only covers the
``domain_config``, so it cannot distinguish them.

Design (borrowed from sacred's file-storage observer, cut to expos, INDEX_M22):
  * **Physical separation, not naming convention.** The record is a peer file
    ``harness_record.json`` sitting ALONGSIDE the OS ``config.json`` in the run dir,
    so the eval knobs are physically split from the OS config -- the same sacred
    ``config.json`` vs ``run.json`` split.
  * **Truth-blind red line (axiom 6).** This module exposes ONLY a write path
    (called once at the evaluation entry -- the CLI seam) and offline read/verify
    helpers. **No OS decision-path module (expos/{kernel,qc,planner,agent,models})
    imports or reads it** -- a grep-able / AST invariant asserted in
    tests/test_harness_record.py. The knobs enter a record SINK, never a decision
    SOURCE. Mirrors the ``save_truth`` / ``export_view`` isolation that already
    proves "writable, but unreadable by decision code".
  * **Off the critical path.** A write failure must never abort a run (the caller
    wraps and logs); this module raises loudly on its own contract violations.

Record shape (``harness_record/v1``)::

    {
      "schema": "harness_record/v1",
      "knobs": { truth_profile, noise_sd, interleave, root_seed, reader_seed,
                 derive_seed_algo, derive_parts, agent_backend, mode, ... },
      "code_provenance": { sim_reader_sha256, screen_sha256, numpy_version },
      "reconciliation_key": { run_dir_name, seed, events_high_water_sha256,
                              events_high_water_bytes, os_config_fingerprint },
      "record_fingerprint": "<sha256 over canonical json of everything above>"
    }

``knobs`` is supplied by the caller (the CLI builds it from the SAME values it
passes to ``run_mcl_loop`` -- the record side is single-object via
:class:`EvalHarnessSpec`). ``code_provenance`` and ``reconciliation_key`` are
computed here. See the hardening note in the module-level docstring of the CLI
seam for the constructive single-object dispatch refactor (mcl.py:850/851/1106),
which is out of scope for this wave (it would touch mcl.py's OS path).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from expos.errors import ExposError

HARNESS_RECORD_NAME = "harness_record.json"
SCHEMA = "harness_record/v1"

#: A stable tag for the derived-sub-seed algorithm (expos.loop.derive_seed). If that
#: algorithm ever changes, bump this so a history run's recorded reader_seed can be
#: told apart from a value the new algorithm would derive (INDEX_M22 point 5).
DERIVE_SEED_ALGO = "expos.loop.derive_seed:sha256-int32-v1"

#: The knob keys the record is allowed to carry (white-list, sacred ConfigScope
#: precedent). A knob outside this set is a loud error -- prevents a new
#: unrecorded knob silently re-opening the truth_profile blindspot.
_ALLOWED_KNOBS = frozenset({
    "truth_profile", "noise_sd", "interleave", "root_seed", "reader_seed",
    "derive_seed_algo", "derive_parts", "agent_backend", "mode",
})


class HarnessRecordError(ExposError):
    """Loud failure from the harness-record layer (unknown knob, missing run dir,
    unreadable record). The record layer is off the OS critical path, so the CALLER
    decides whether a write failure is fatal; the layer itself never degrades
    silently (no silent fallback)."""


@dataclass(frozen=True)
class EvalHarnessSpec:
    """The frozen single object the evaluation entry builds from the SAME values it
    dispatches (INDEX_M22 §2: "the spec that is dispatched is the spec that is
    recorded"). At this wave the dispatch (serve/compile_wet) happens inside
    ``mcl.py``; making dispatch consume this exact instance would touch mcl.py's OS
    path, so the CLI builds it from the same call-side values and records it. Its
    ``as_knobs()`` feeds :func:`write_harness_record`.

    ``noise_sd`` / ``interleave`` mirror mcl's pinned constants (0.0 / True); see
    the hardening note (single-object dispatch refactor, mcl.py:850/851/1106)."""

    truth_profile: str
    noise_sd: float
    interleave: bool
    root_seed: int
    reader_seed: int
    agent_backend: str
    mode: str
    derive_parts: tuple[str, ...] = ("reader",)
    derive_seed_algo: str = DERIVE_SEED_ALGO

    def as_knobs(self) -> dict[str, Any]:
        return {
            "truth_profile": self.truth_profile,
            "noise_sd": self.noise_sd,
            "interleave": self.interleave,
            "root_seed": self.root_seed,
            "reader_seed": self.reader_seed,
            "derive_seed_algo": self.derive_seed_algo,
            "derive_parts": list(self.derive_parts),
            "agent_backend": self.agent_backend,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class HarnessVerification:
    """Result of :func:`verify_harness_record`: ``ok`` plus a list of human-readable
    mismatch reasons (empty when ok)."""

    ok: bool
    reasons: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256_hex(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def events_high_water(run_dir: str | Path) -> tuple[str, int]:
    """The event-log high-water reconciliation datum: sha256 over ``events.jsonl``
    bytes up to and INCLUDING the last complete line (the high-water mark), plus the
    byte count. events.jsonl is JSON Lines (one record per newline-terminated line),
    so the high-water = the last newline: a torn/partial trailing write is excluded,
    exactly the "valid up to" water level. Independently recomputable (read bytes,
    find last ``\\n``, sha the prefix) -- tests recompute it to cross-check. A
    missing/empty log yields the sha of the empty prefix and 0 bytes."""
    path = Path(run_dir) / "events.jsonl"
    data = path.read_bytes() if path.exists() else b""
    high_water = data.rfind(b"\n") + 1  # 0 when no newline (nothing complete yet)
    return _sha256_hex(data[:high_water]), high_water


def _module_source_sha256(module_name: str) -> str:
    """sha256 of a knob-consuming module's SOURCE FILE (INDEX_M22 §4: distinguish
    "same knob bytes, drifted code"). Imported read-only, purely to locate the file;
    no behaviour is invoked."""
    import importlib

    mod = importlib.import_module(module_name)
    src_path = inspect.getsourcefile(mod) or getattr(mod, "__file__", None)
    if src_path is None:
        raise HarnessRecordError(
            f"cannot locate source of {module_name!r} for code provenance"
        )
    return _sha256_hex(Path(src_path).read_bytes())


def _code_provenance() -> dict[str, Any]:
    """Harness self-fingerprint: the source sha of the two modules that consume the
    knobs (the reader's truth surface + the wet replicate-order screen), plus a
    numpy version anchor (numpy backs the random streams -- a version change can
    drift the same-seed stream, INDEX_M22 §4). A per-round truth content hash is a
    filed hardening note (cheap only inside the run, not at the CLI seam)."""
    prov: dict[str, Any] = {
        "sim_reader_sha256": _module_source_sha256("expos.adapters.wet.sim_reader"),
        "screen_sha256": _module_source_sha256("expos.adapters.wet.screen"),
    }
    try:
        import numpy
        prov["numpy_version"] = str(numpy.__version__)
    except ImportError:
        prov["numpy_version"] = None
    return prov


def _os_config_fingerprint(run_dir: Path) -> str | None:
    """The OS run's ``config_fingerprint`` read from ``config.json`` (read-only
    cross-pin). Binds this record to that specific OS run bidirectionally; it commits
    to the whole ``domain_config`` (so e.g. ``replicates`` is covered transitively --
    not duplicated here, avoiding a second unreconciled book, EXP012's warning)."""
    cfg_path = run_dir / "config.json"
    if not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cfg.get("config_fingerprint") if isinstance(cfg, dict) else None


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    """sha256 over the canonical json of the record WITHOUT its own
    ``record_fingerprint`` field."""
    body = {k: v for k, v in record.items() if k != "record_fingerprint"}
    return _sha256_hex(_canonical(body).encode("utf-8"))


# --------------------------------------------------------------------------- write

def write_harness_record(run_dir: str | Path, knobs: Mapping[str, Any]) -> Path:
    """Write ``harness_record.json`` inside ``run_dir`` and return its path.

    ``knobs`` is the white-listed eval-device knob dict (see :data:`_ALLOWED_KNOBS`;
    build it via :meth:`EvalHarnessSpec.as_knobs`). This function adds the computed
    ``code_provenance`` + ``reconciliation_key`` + ``record_fingerprint``. Written
    atomically (tmp + os.replace). Raises :class:`HarnessRecordError` on a contract
    violation (missing run dir, unknown knob) -- the caller decides fatality."""
    import os

    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise HarnessRecordError(f"run dir does not exist: {run_dir}")
    stray = set(knobs) - _ALLOWED_KNOBS
    if stray:
        raise HarnessRecordError(
            f"unknown harness knob(s) {sorted(stray)}; allowed: {sorted(_ALLOWED_KNOBS)} "
            "(add to _ALLOWED_KNOBS deliberately -- an unrecorded knob is how the "
            "truth_profile blindspot happened)"
        )

    hw_sha, hw_bytes = events_high_water(run_dir)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "knobs": dict(knobs),
        "code_provenance": _code_provenance(),
        "reconciliation_key": {
            "run_dir_name": run_dir.name,
            "seed": knobs.get("root_seed"),
            "events_high_water_sha256": hw_sha,
            "events_high_water_bytes": hw_bytes,
            "os_config_fingerprint": _os_config_fingerprint(run_dir),
        },
    }
    record["record_fingerprint"] = _record_fingerprint(record)

    dest = run_dir / HARNESS_RECORD_NAME
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, dest)
    return dest


# --------------------------------------------------------------- offline read/verify

def read_harness_record(run_dir: str | Path) -> dict[str, Any]:
    """Read and parse ``harness_record.json`` (offline audit only -- NEVER called by
    any OS decision path). Raises :class:`HarnessRecordError` if absent/unparseable."""
    path = Path(run_dir) / HARNESS_RECORD_NAME
    if not path.exists():
        raise HarnessRecordError(f"no harness record at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HarnessRecordError(f"harness record is not valid json: {path}: {e}") from e


def verify_harness_record(run_dir: str | Path) -> HarnessVerification:
    """Offline integrity + reconciliation check (audit tool -- not on any OS path).

    Checks, accumulating all mismatches:
      1. ``record_fingerprint`` matches a recompute over the stored body (the record
         was not hand-edited).
      2. ``events_high_water_sha256`` matches a fresh recompute from the run's current
         ``events.jsonl`` (the event log was not tampered / truncated after the
         record was written).
      3. ``os_config_fingerprint`` still matches the run's ``config.json`` (the record
         is pinned to this OS run).
      4. ``code_provenance`` still matches the current knob-consuming module sources
         (informational drift signal: "same knobs, drifted code").
    """
    run_dir = Path(run_dir)
    reasons: list[str] = []
    record = read_harness_record(run_dir)

    stored_fp = record.get("record_fingerprint")
    recomputed_fp = _record_fingerprint(record)
    if stored_fp != recomputed_fp:
        reasons.append(
            f"record_fingerprint mismatch (stored {stored_fp}, recomputed "
            f"{recomputed_fp}) -- record body was edited"
        )

    key = record.get("reconciliation_key", {})
    cur_sha, cur_bytes = events_high_water(run_dir)
    if key.get("events_high_water_sha256") != cur_sha:
        reasons.append(
            f"events high-water mismatch (record {key.get('events_high_water_sha256')} "
            f"@ {key.get('events_high_water_bytes')} bytes, current {cur_sha} @ "
            f"{cur_bytes} bytes) -- events.jsonl changed after the record was written"
        )

    cur_cfg_fp = _os_config_fingerprint(run_dir)
    if key.get("os_config_fingerprint") != cur_cfg_fp:
        reasons.append(
            f"os_config_fingerprint mismatch (record {key.get('os_config_fingerprint')}, "
            f"current {cur_cfg_fp}) -- config.json changed or record pinned a different run"
        )

    stored_prov = record.get("code_provenance", {})
    try:
        cur_prov = _code_provenance()
        for k in ("sim_reader_sha256", "screen_sha256"):
            if stored_prov.get(k) != cur_prov.get(k):
                reasons.append(
                    f"code_provenance drift: {k} (record {stored_prov.get(k)}, current "
                    f"{cur_prov.get(k)}) -- knob-consuming code changed since the record"
                )
    except HarnessRecordError as e:  # source unlocatable -- report, don't crash verify
        reasons.append(f"code_provenance recompute failed: {e}")

    return HarnessVerification(ok=not reasons, reasons=reasons)
