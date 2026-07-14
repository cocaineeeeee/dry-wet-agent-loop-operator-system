"""Pre-submit validation (G2): invalid input is rejected BEFORE a job spawns."""

from __future__ import annotations

import pytest

from expos.adapters.base import AdapterError

from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.spec import JobSpec


@pytest.fixture
def adapter(tmp_path):
    return PySCFDryAdapter(jobs_root=tmp_path / "jobs")


def _spec(**kw) -> JobSpec:
    base = dict(job_id="t", well_id="w", cand_id="c", solvent="water")
    base.update(kw)
    return JobSpec(**base)


def test_valid_spec_passes(adapter):
    adapter.validate_spec(_spec())  # no raise


def test_missing_geometry_and_solvent_rejected(adapter):
    with pytest.raises(AdapterError, match="neither a `solvent`"):
        adapter.validate_spec(_spec(solvent=None, geometry=None))


def test_unknown_solvent_rejected(adapter):
    with pytest.raises(AdapterError, match="unknown solvent"):
        adapter.validate_spec(_spec(solvent="unobtainium"))


def test_disallowed_basis_rejected(adapter):
    with pytest.raises(AdapterError, match="basis"):
        adapter.validate_spec(_spec(basis="cc-pvqz"))


def test_unsupported_method_rejected(adapter):
    with pytest.raises(AdapterError, match="method"):
        adapter.validate_spec(_spec(method="CCSD"))


def test_bad_max_cycle_rejected(adapter):
    with pytest.raises(AdapterError, match="max_cycle"):
        adapter.validate_spec(_spec(max_cycle=0))


def test_impossible_charge_spin_parity_rejected(adapter):
    # Water has 10 electrons (even); spin=1 (odd unpaired count) is impossible.
    with pytest.raises(AdapterError, match="molecule build failed"):
        adapter.validate_spec(_spec(spin=1))


def test_bad_geometry_rejected(adapter):
    with pytest.raises(AdapterError, match="molecule build failed"):
        adapter.validate_spec(_spec(solvent=None, geometry="Zz 0 0 0"))


def test_explicit_geometry_accepted(adapter):
    geom = "H 0 0 0\nH 0 0 0.74"
    adapter.validate_spec(_spec(solvent=None, geometry=geom))  # no raise
