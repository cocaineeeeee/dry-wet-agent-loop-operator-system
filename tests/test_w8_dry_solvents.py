"""Solvent presets: all converge, correct polarity ordering, deterministic."""

from __future__ import annotations

import pytest

from expos.adapters.dry.compute import run_pyscf
from expos.adapters.dry.solvents import solvent_names
from expos.adapters.dry.spec import ComputeResult, JobSpec


def _spec(name: str, **kw) -> JobSpec:
    return JobSpec(job_id="t", well_id="w", cand_id="c", solvent=name, **kw)


@pytest.mark.parametrize("name", solvent_names())
def test_every_preset_converges(name: str) -> None:
    r = run_pyscf(_spec(name))
    assert r.converged is True
    assert r.dipole_debye >= 0.0
    assert r.engine == "pyscf"
    assert r.metric == "polarity_proxy"
    assert r.value == pytest.approx(r.dipole_debye)


def test_polarity_ordering_is_physical() -> None:
    """The dipole-based polarity proxy must rank nonpolar low, polar high."""
    dip = {n: run_pyscf(_spec(n)).dipole_debye for n in ("hexane", "toluene", "water", "dmso")}
    assert dip["hexane"] < 0.2          # nonpolar ~ 0
    assert dip["hexane"] < dip["toluene"] < dip["water"] < dip["dmso"]
    assert dip["dmso"] > 3.0            # strongly polar


def test_compute_is_deterministic() -> None:
    """Same input -> same result_sha (G2 determinism anchor).

    Note: the raw SCF energy carries sub-ULP jitter across runs (threaded BLAS
    reduction order), so the determinism contract is deliberately defined over
    ROUNDED key fields (result_sha), not bit-identical floats. This test proves
    the sha absorbs that jitter."""
    a = run_pyscf(_spec("dmso"))
    b = run_pyscf(_spec("dmso"))
    assert a.result_sha == b.result_sha            # the anchor: identical
    assert a.spec_sha == b.spec_sha
    assert a.total_energy_hartree == pytest.approx(b.total_energy_hartree, abs=1e-6)
    assert a.dipole_debye == pytest.approx(b.dipole_debye, abs=1e-6)


def test_distinct_inputs_have_distinct_sha() -> None:
    assert run_pyscf(_spec("water")).result_sha != run_pyscf(_spec("hexane")).result_sha
    assert _spec("water").spec_sha() != _spec("hexane").spec_sha()


# NOTE: B3LYP (DFT) is intentionally NOT exercised in-process. libxc on this
# host segfaults on DFT eval; running it in the pytest interpreter would crash
# the whole test process. The adapter runs every job out-of-process precisely
# so an engine segfault is contained as FAILED(signal) — see
# tests/test_lifecycle.py::test_b3lyp_engine_crash_is_contained.


def test_result_sha_stable_across_serialization() -> None:
    r = run_pyscf(_spec("methanol"))
    roundtrip = ComputeResult.model_validate_json(r.model_dump_json())
    assert roundtrip.result_sha == r.result_sha
