"""Dry-job result reuse gate — the composite reuse key (spec_sha, engine_id,
engine_version) and the decision function that consumes a cached ``result.json``.

WHY engine_version rides the REUSE key but NOT ``spec_sha`` (B-session ruling,
letter red_to_blue/100 §2)
--------------------------------------------------------------------------
``spec_sha`` (spec.py) is the *protocol/compute semantic identity* — a
consumer of ``protocol_fingerprint`` is on record depending on it. Mixing the
engine version into ``spec_sha`` would let the identity of "the same protocol"
drift every time the engine is upgraded, polluting the W2 fingerprint chain and
the gate-12 diff's "same-protocol comparison" semantics. The engine belongs to
the EXECUTION plane: its version enters the *reuse key* (a cache-correctness
concern) but never the *protocol identity* (a decision-plane semantic). This is
the same decision-plane / execution-plane cut applied elsewhere.

Hence the reuse key is the COMPOSITE ``(spec_sha, engine_id, engine_version)``
and ``spec_sha`` itself is left untouched: two runs on two engine versions of
the same protocol still share one ``spec_sha`` (asserted in the tests), so the
protocol identity does not move — only reuse eligibility does.

DVC counter-example we deliberately do NOT copy (INDEX_M19_DATAVER §2/§3)
--------------------------------------------------------------------------
DVC's run-cache, once a stage hash HITS, checks the cached outputs out and skips
execution WITHOUT re-verifying the output content ("hit == trust"). We keep the
opposite discipline: our fourth gate is a bit-for-bit ``result_sha`` compare
(``expected_result_sha`` below), and a legacy artifact that cannot prove its
engine identity is refused, loudly, rather than trusted. DVC's lesson we DO
take (§3, ``changed_stage``): a reuse key must cover input UNION execution
environment — covering only the input (``spec_sha``) is a reproducibility blind
spot ("reuse an old-PySCF result after upgrading PySCF"). This gate closes it.

We deliberately do NOT append the engine version to the workdir NAME
(``{job_id}__{spec_sha[:12]}``): that would re-address every artifact and make
pre-upgrade workdirs unfindable. Addressing stays by ``spec_sha`` (backward
compatible); the engine version is enforced at the gate, so an old artifact is
still *located* — and then *rejected* with a loud downgrade reason — instead of
silently vanishing from the reuse namespace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from expos.adapters.dry.spec import JobSpec

#: The dry engine identity. Fixed for the PySCF dry face; a second engine would
#: get its own id and its own slice of the composite reuse key.
ENGINE_ID = "pyscf"


def current_engine_version() -> str:
    """Engine version of the *current* environment — the right-hand side of the
    composite-key comparison. Read from installed package metadata so it matches
    exactly what the worker records into ``result.json``
    (``compute.py`` writes ``engine_version=pyscf.__version__``)."""
    from importlib.metadata import version

    return version("pyscf")


def reuse_key(
    spec: JobSpec, engine_id: str = ENGINE_ID, engine_version: str | None = None
) -> tuple[str, str, str]:
    """The composite reuse key ``(spec_sha, engine_id, engine_version)``.

    ``spec_sha`` is the untouched protocol/compute identity; ``engine_id`` +
    ``engine_version`` are the execution-plane dimensions that make a cached
    result eligible for reuse only under the same engine."""
    if engine_version is None:
        engine_version = current_engine_version()
    return (spec.spec_sha(), engine_id, engine_version)


@dataclass(frozen=True)
class ReuseDecision:
    """Outcome of evaluating one cached ``result.json`` against a spec.

    ``reusable`` is the verdict; ``reason`` is always populated (a loud,
    human-readable record of *why* — required for the "downgrade reason logged"
    discipline on every refusal); ``key`` is the matched composite key on a hit,
    ``None`` on a miss."""

    reusable: bool
    reason: str
    key: tuple[str, str, str] | None = None


def evaluate_reuse(
    spec: JobSpec,
    workdir_or_result: str | Path,
    *,
    engine_id: str = ENGINE_ID,
    engine_version: str | None = None,
    expected_result_sha: str | None = None,
) -> ReuseDecision:
    """Decide whether a cached ``result.json`` may be reused for ``spec``.

    ``workdir_or_result`` may be a job workdir (``result.json`` is resolved
    inside it) or the ``result.json`` path directly.

    Gate order (the original four gates + the composite-key fifth check):
      1. no sibling ``error.json`` and the ``result.json`` exists & parses
         (SUCCEEDED, complete, non-error artifact);
      2. recorded ``spec_sha`` == ``spec.spec_sha()`` (protocol/compute identity);
      3. ``result_sha`` present, and — when ``expected_result_sha`` is given —
         bit-for-bit equal to it (determinism anchor; the DVC "hit==trust" we do
         NOT copy);
      4/5. COMPOSITE KEY: recorded ``(engine, engine_version)`` matches the
         current ``(engine_id, engine_version)``. A legacy artifact with no
         ``engine_version`` field cannot prove engine identity → refused, loudly,
         never crashed.

    Any refusal returns ``reusable=False`` with a loud ``reason``; the caller
    then recomputes (and re-anchors ``result_sha``) rather than trusting a stale
    or version-drifted artifact.
    """
    if engine_version is None:
        engine_version = current_engine_version()
    want_key = (spec.spec_sha(), engine_id, engine_version)

    p = Path(workdir_or_result)
    result_path = p / "result.json" if p.is_dir() else p
    workdir = result_path.parent

    # Gate 1: no error sidecar; declared product present and parseable.
    if (workdir / "error.json").exists():
        return ReuseDecision(
            False,
            f"reject: error.json present in {workdir} — a failed job is never reused",
        )
    if not result_path.exists():
        return ReuseDecision(
            False,
            f"reject: no result.json at {result_path} — no prior success to reuse",
        )
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return ReuseDecision(
            False, f"reject: result.json at {result_path} unparseable: {exc}"
        )

    # Gate 2: protocol/compute identity must match.
    rec_spec_sha = raw.get("spec_sha")
    if rec_spec_sha != spec.spec_sha():
        return ReuseDecision(
            False,
            f"reject: spec_sha mismatch (recorded {rec_spec_sha!r} != "
            f"current {spec.spec_sha()!r}) — different compute identity",
        )

    # Gate 3: determinism anchor present; bit-for-bit compare when a reference
    # result_sha is supplied (the result_sha discipline we keep vs DVC).
    rec_result_sha = raw.get("result_sha")
    if not rec_result_sha:
        return ReuseDecision(
            False,
            f"reject: result.json at {result_path} has no result_sha — "
            f"cannot anchor determinism, refuse to reuse",
        )
    if expected_result_sha is not None and rec_result_sha != expected_result_sha:
        return ReuseDecision(
            False,
            f"reject: result_sha mismatch (recorded {rec_result_sha!r} != "
            f"expected {expected_result_sha!r}) — bit-for-bit reproduction failed",
        )

    # Gate 4/5: COMPOSITE KEY — engine identity + version must match. A legacy
    # artifact (no engine_version field) is refused with a loud downgrade reason,
    # never trusted and never crashed.
    rec_engine = raw.get("engine")
    rec_version = raw.get("engine_version")
    if rec_version is None:
        return ReuseDecision(
            False,
            f"downgrade: result.json at {result_path} has no engine_version field "
            f"(legacy artifact) — cannot prove engine identity, treated as "
            f"non-reusable; recompute under {engine_id} {engine_version}",
        )
    rec_key = (rec_spec_sha, rec_engine, rec_version)
    if rec_key != want_key:
        return ReuseDecision(
            False,
            f"downgrade: engine version drift — recorded key {rec_key} != current "
            f"{want_key}; refuse to reuse a result across an engine upgrade "
            f"(reuse key covers input UNION execution environment)",
        )

    return ReuseDecision(
        True,
        f"reuse: composite key {want_key} matches; four gates pass "
        f"(SUCCEEDED, spec_sha, result_sha present, engine version)",
        key=want_key,
    )
