"""Pure PySCF compute for one JobSpec.

This module is imported by the out-of-process worker (dry.worker). It is also
directly importable for in-process unit tests of the *compute content* (NOT for
the real-job path — G2 requires jobs run as separate processes; in-process use
here is only for testing determinism of the numerics).

Content (solvent_screen domain): small-molecule single point at STO-3G-class
basis with HF or B3LYP, exporting a "polarity proxy score" (dipole magnitude in
Debye) as the metric value, with total energy and HOMO-LUMO gap as secondary.
"""

from __future__ import annotations

import time

import numpy as np

from expos.adapters.dry.solvents import geometry_for
from expos.adapters.dry.spec import ComputeResult, JobSpec

_HARTREE_TO_EV = 27.211386245988


class ConvergenceError(RuntimeError):
    """Raised when the SCF does not converge within max_cycle (reason=convergence)."""


class ComputeError(RuntimeError):
    """Raised on any other compute failure (bad geometry, PySCF internal error)."""


def _resolve_geometry(spec: JobSpec) -> tuple[str, int, int]:
    """Return (geometry_string, charge, spin). Explicit geometry wins over
    a preset solvent; a preset fills in its own charge/spin."""
    if spec.geometry and spec.geometry.strip():
        return spec.geometry, spec.charge, spec.spin
    if spec.solvent:
        zmat, charge, spin = geometry_for(spec.solvent)
        # Explicit non-default charge/spin on the spec override the preset.
        if spec.charge != 0:
            charge = spec.charge
        if spec.spin != 0:
            spin = spec.spin
        return zmat, charge, spin
    raise ComputeError("spec has neither `solvent` preset nor explicit `geometry`")


def build_mol(spec: JobSpec):
    """Build a PySCF Mole. Cheap (no SCF); safe to call in pre-submit
    validation to catch bad geometry / charge / spin parity early."""
    from pyscf import gto

    geom, charge, spin = _resolve_geometry(spec)
    try:
        mol = gto.M(
            atom=geom,
            basis=spec.basis,
            charge=charge,
            spin=spin,
            verbose=0,
        )
    except Exception as exc:  # noqa: BLE001 - normalize to ComputeError
        raise ComputeError(f"failed to build molecule: {exc}") from exc
    return mol


def run_pyscf(spec: JobSpec) -> ComputeResult:
    """Run the single-point job and return a ComputeResult.

    Raises ConvergenceError if the SCF fails to converge, ComputeError on any
    other numerical failure. Honors spec.sleep_s (test hook) to open a
    killable window.
    """
    import pyscf
    from pyscf import dft, scf

    if spec.sleep_s > 0:
        time.sleep(spec.sleep_s)

    mol = build_mol(spec)

    if spec.method == "HF":
        mf = scf.RHF(mol)
    elif spec.method == "B3LYP":
        mf = dft.RKS(mol)
        mf.xc = "b3lyp"
    else:  # defensive; validated upstream
        raise ComputeError(f"unsupported method {spec.method!r}")

    mf.max_cycle = int(spec.max_cycle)
    mf.conv_tol = float(spec.conv_tol)
    if spec.disable_diis:
        mf.diis = False

    cycles = {"n": 0}

    def _count(_envs):
        cycles["n"] += 1

    mf.callback = _count

    try:
        energy = float(mf.kernel())
    except Exception as exc:  # noqa: BLE001
        raise ComputeError(f"SCF raised: {exc}") from exc

    if not mf.converged:
        raise ConvergenceError(
            f"SCF did not converge in {spec.max_cycle} cycles "
            f"(method={spec.method}, basis={spec.basis})"
        )

    dipole_vec = mf.dip_moment(unit="Debye", verbose=0)
    dipole = float(np.linalg.norm(dipole_vec))

    mo_e = np.asarray(mf.mo_energy)
    occ = np.asarray(mf.mo_occ)
    homo = float(mo_e[occ > 0].max())
    lumo = float(mo_e[occ == 0].min()) if (occ == 0).any() else homo
    gap_ev = (lumo - homo) * _HARTREE_TO_EV

    secondary = {
        "total_energy_hartree": energy,
        "homo_lumo_gap_ev": gap_ev,
        "n_electrons": float(mol.nelectron),
    }

    return ComputeResult(
        metric=spec.metric,
        value=dipole,  # polarity proxy = molecular dipole magnitude
        unit="Debye",
        secondary=secondary,
        engine="pyscf",
        engine_version=pyscf.__version__,
        basis=spec.basis,
        method=spec.method,
        charge=mol.charge,
        spin=mol.spin,
        converged=True,
        scf_cycles=int(cycles["n"]),
        n_electrons=int(mol.nelectron),
        total_energy_hartree=energy,
        dipole_debye=dipole,
        spec_sha=spec.spec_sha(),
        result_sha=ComputeResult.result_fingerprint(energy, dipole, gap_ev),
    )
