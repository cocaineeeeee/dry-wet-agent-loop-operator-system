"""Composite reuse key (spec_sha, engine_id, engine_version) — engine version
enters the REUSE key (cache correctness) but NOT spec_sha (protocol identity).

Ruling: red_to_blue/100 §2. Blind spot: INDEX_M19_DATAVER §2/§3 (DVC changed_stage
— a reuse key must cover input UNION execution environment). These tests fabricate
``result.json`` files directly (no PySCF run) so they are fast and hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path

from expos.adapters.dry.reuse import (
    ENGINE_ID,
    current_engine_version,
    evaluate_reuse,
    reuse_key,
)
from expos.adapters.dry.spec import JobSpec


def _spec(**kw) -> JobSpec:
    return JobSpec(job_id="t:r:w1", well_id="w1", cand_id="c1", solvent="water", **kw)


def _write_result(
    workdir: Path, spec: JobSpec, *, engine_version: str | None, engine: str = "pyscf",
    result_sha: str = "deadbeef", spec_sha: str | None = None, drop_version: bool = False,
) -> Path:
    """Write a minimal result.json. When ``drop_version`` the engine_version key
    is omitted entirely (old-format artifact)."""
    workdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "spec_sha": spec_sha if spec_sha is not None else spec.spec_sha(),
        "result_sha": result_sha,
        "engine": engine,
    }
    if not drop_version:
        payload["engine_version"] = engine_version
    (workdir / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return workdir


# (a) same spec, same engine version -> reusable is True ----------------------
def test_same_spec_same_version_is_reusable(tmp_path: Path) -> None:
    spec = _spec()
    ver = current_engine_version()
    wd = _write_result(tmp_path / "job", spec, engine_version=ver)

    d = evaluate_reuse(spec, wd, engine_version=ver)
    assert d.reusable is True
    assert d.key == (spec.spec_sha(), ENGINE_ID, ver)
    assert d.reason  # a hit still records a loud reason
    # resolving via the result.json path directly is equivalent to via workdir
    assert evaluate_reuse(spec, wd / "result.json", engine_version=ver).reusable is True


# (b) fabricated OLD engine version -> reuse refused + downgrade reason logged -
def test_old_engine_version_is_refused_with_downgrade_reason(tmp_path: Path) -> None:
    spec = _spec()
    wd = _write_result(tmp_path / "job", spec, engine_version="1.0.0-ancient")

    d = evaluate_reuse(spec, wd, engine_version="2.13.1")
    assert d.reusable is False
    assert d.key is None
    assert "drift" in d.reason and "1.0.0-ancient" in d.reason  # loud downgrade reason


# (c) old-format result (no engine_version field) -> refuse, do not crash -----
def test_legacy_result_without_version_field_refused_not_crashed(tmp_path: Path) -> None:
    spec = _spec()
    wd = _write_result(tmp_path / "job", spec, engine_version=None, drop_version=True)

    d = evaluate_reuse(spec, wd, engine_version="2.13.1")
    assert d.reusable is False  # refused, no exception raised
    assert "no engine_version" in d.reason and "legacy" in d.reason


# (d) spec_sha itself carries NO engine version (protocol identity never drifts)
def test_spec_sha_is_version_independent() -> None:
    spec = _spec()
    # The reuse key differs across engine versions...
    k_old = reuse_key(spec, engine_version="1.0.0")
    k_new = reuse_key(spec, engine_version="2.13.1")
    assert k_old != k_new
    # ...but the spec_sha component (protocol identity) is IDENTICAL across both.
    assert k_old[0] == k_new[0] == spec.spec_sha()
    # And spec_sha, computed twice, is engine-version-agnostic by construction
    # (compute_fingerprint has no engine field at all).
    assert "engine" not in spec.compute_fingerprint()
    assert spec.spec_sha() == JobSpec(**spec.model_dump()).spec_sha()


# extra guards: the other three gates (error.json / spec_sha / result_sha) -----
def test_error_sidecar_blocks_reuse(tmp_path: Path) -> None:
    spec = _spec()
    ver = current_engine_version()
    wd = _write_result(tmp_path / "job", spec, engine_version=ver)
    (wd / "error.json").write_text("{}", encoding="utf-8")
    assert evaluate_reuse(spec, wd, engine_version=ver).reusable is False


def test_spec_sha_mismatch_blocks_reuse(tmp_path: Path) -> None:
    spec = _spec()
    ver = current_engine_version()
    wd = _write_result(tmp_path / "job", spec, engine_version=ver, spec_sha="not-the-same")
    d = evaluate_reuse(spec, wd, engine_version=ver)
    assert d.reusable is False and "spec_sha mismatch" in d.reason


def test_result_sha_bitwise_compare_is_retained(tmp_path: Path) -> None:
    """The DVC 'hit==trust' we do NOT copy: when a reference result_sha is
    supplied it must match bit-for-bit."""
    spec = _spec()
    ver = current_engine_version()
    wd = _write_result(tmp_path / "job", spec, engine_version=ver, result_sha="aaa")
    assert evaluate_reuse(spec, wd, engine_version=ver, expected_result_sha="aaa").reusable is True
    d = evaluate_reuse(spec, wd, engine_version=ver, expected_result_sha="bbb")
    assert d.reusable is False and "result_sha mismatch" in d.reason


def test_missing_result_json_is_not_reusable(tmp_path: Path) -> None:
    spec = _spec()
    (tmp_path / "empty").mkdir()
    assert evaluate_reuse(spec, tmp_path / "empty", engine_version="x").reusable is False
