"""Preset solvent geometries for the ``solvent_screen`` dry domain.

Geometries are given as PySCF Z-matrix strings (internal coordinates) built
from standard bond lengths / angles. Z-matrices are used deliberately: they are
far less error-prone to author by hand than raw Cartesians and stay chemically
sane without a geometry optimiser (a single-point dipole/energy proxy does not
require an optimised structure).

These are approximate geometries chosen so that the polarity ordering comes out
qualitatively right (DMSO/acetonitrile high dipole, hexane ~0). They are NOT
reference structures and carry method + geometry error on purpose: the dry
compute is an observation, not truth.

Each entry: name -> (zmatrix, charge, spin). spin = 2S (number of unpaired
electrons); all presets are closed-shell singlets (spin=0).
"""

from __future__ import annotations

# ---- bond lengths (Angstrom) / angles (deg) are baked into the Z-matrices ----

_WATER = """
O
H 1 0.958
H 1 0.958 2 104.5
"""

_METHANOL = """
C
O 1 1.42
H 1 1.09 2 109.5
H 1 1.09 2 109.5 3 120
H 1 1.09 2 109.5 3 240
H 2 0.96 1 108.0 3 180
"""

_ETHANOL = """
C
C 1 1.52
O 2 1.42 1 109.5
H 1 1.09 2 109.5 3 60
H 1 1.09 2 109.5 3 180
H 1 1.09 2 109.5 3 300
H 2 1.09 1 109.5 3 120
H 2 1.09 1 109.5 3 240
H 3 0.96 2 108.0 1 180
"""

# Cartesian (not Z-matrix): the CH3-C#N backbone is collinear, which makes a
# methyl-H dihedral referenced against the axis degenerate (singular overlap).
_ACETONITRILE = """
C  0.000000  0.000000  0.000000
C  0.000000  0.000000  1.460000
N  0.000000  0.000000  2.620000
H  1.024000  0.000000 -0.373000
H -0.512000  0.887000 -0.373000
H -0.512000 -0.887000 -0.373000
"""

_ACETONE = """
C
O 1 1.22
C 1 1.51 2 121.0
C 1 1.51 2 121.0 3 180.0
H 3 1.09 1 109.5 2 0
H 3 1.09 1 109.5 2 120
H 3 1.09 1 109.5 2 240
H 4 1.09 1 109.5 2 0
H 4 1.09 1 109.5 2 120
H 4 1.09 1 109.5 2 240
"""

_DMSO = """
S
O 1 1.53
C 1 1.80 2 106.0
C 1 1.80 2 106.0 3 97.0
H 3 1.09 1 109.5 2 0
H 3 1.09 1 109.5 2 120
H 3 1.09 1 109.5 2 240
H 4 1.09 1 109.5 2 0
H 4 1.09 1 109.5 2 120
H 4 1.09 1 109.5 2 240
"""

_TOLUENE = """
C
C 1 1.40
C 2 1.40 1 120.0
C 3 1.40 2 120.0 1 0.0
C 4 1.40 3 120.0 2 0.0
C 5 1.40 4 120.0 3 0.0
C 1 1.51 2 120.0 3 180.0
H 2 1.09 1 120.0 3 180.0
H 3 1.09 2 120.0 1 180.0
H 4 1.09 3 120.0 2 180.0
H 5 1.09 4 120.0 3 180.0
H 6 1.09 5 120.0 4 180.0
H 7 1.09 1 111.0 2 0.0
H 7 1.09 1 111.0 2 120.0
H 7 1.09 1 111.0 2 240.0
"""

_HEXANE = """
C
C 1 1.53
C 2 1.53 1 111.0
C 3 1.53 2 111.0 1 180.0
C 4 1.53 3 111.0 2 180.0
C 5 1.53 4 111.0 3 180.0
H 1 1.09 2 109.5 3 60
H 1 1.09 2 109.5 3 180
H 1 1.09 2 109.5 3 300
H 2 1.09 1 109.5 3 120
H 2 1.09 1 109.5 3 240
H 3 1.09 2 109.5 4 120
H 3 1.09 2 109.5 4 240
H 4 1.09 3 109.5 5 120
H 4 1.09 3 109.5 5 240
H 5 1.09 4 109.5 6 120
H 5 1.09 4 109.5 6 240
H 6 1.09 5 109.5 4 60
H 6 1.09 5 109.5 4 180
H 6 1.09 5 109.5 4 300
"""

#: name -> (zmatrix, charge, spin=2S). Closed-shell singlets.
SOLVENTS: dict[str, tuple[str, int, int]] = {
    "water": (_WATER, 0, 0),
    "methanol": (_METHANOL, 0, 0),
    "ethanol": (_ETHANOL, 0, 0),
    "acetonitrile": (_ACETONITRILE, 0, 0),
    "acetone": (_ACETONE, 0, 0),
    "dmso": (_DMSO, 0, 0),
    "toluene": (_TOLUENE, 0, 0),
    "hexane": (_HEXANE, 0, 0),
}


def solvent_names() -> list[str]:
    """Sorted list of preset solvent names."""
    return sorted(SOLVENTS)


def geometry_for(name: str) -> tuple[str, int, int]:
    """Return (zmatrix, charge, spin) for a preset solvent name.

    Raises KeyError with the available names if unknown.
    """
    try:
        return SOLVENTS[name]
    except KeyError:
        raise KeyError(
            f"unknown solvent {name!r}; available presets: {solvent_names()}"
        )
