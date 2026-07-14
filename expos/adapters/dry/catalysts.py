"""Preset catalyst-ligand geometries for the ``catalyst_screen`` dry domain (M20).

Second-domain existence proof: this is the catalyst analogue of ``solvents.py``.
Each entry is a small **ligand model fragment** -- a phosphorus/nitrogen donor
that stands in for a member of the Suzuki cross-coupling ligand family (phosphines
+ an amine reference). They are deliberately tiny (<= 13 atoms) so an HF/STO-3G
single point converges in seconds on a laptop, exactly like the solvent presets.

HONEST-BIASED PROXY (identical semantics to the dry solvent leg): the dry compute
exports a ``reactivity_proxy`` (the molecular dipole magnitude, Debye) that carries
REAL first-principles method error (basis/geometry/functional) and is only a
*correlated stand-in* for catalytic performance. The TRUE reaction yield is NOT
here -- it is hidden in the wet plate-reader truth surface (sim_reader), never in a
ligand descriptor. A ligand-fragment dipole is public design knowledge; leaking it
leaks no truth (mirrors the SOLVENT_POLARITY note in adapters/wet/screen.py).

Geometries are PySCF Z-matrix strings from standard bond lengths/angles (chosen so
the fragment is chemically sane without a geometry optimiser; a single-point
dipole/energy proxy needs no optimised structure). Each entry:
``name -> (zmatrix, charge, spin=2S)``. All presets are closed-shell singlets.

ZERO-adapter-change contract (M20 launch letter §2.3): ``PySCFDryAdapter`` is NOT
modified. A catalyst candidate carries its ligand geometry in the candidate/well
``params`` under the explicit ``geometry`` key (public design input), sourced from
:func:`catalyst_params` below. The adapter's ``_resolve_geometry`` already prefers
an explicit ``geometry`` over the ``solvent`` preset, so the catalyst leg flows
through the unchanged adapter with ``spec.solvent is None`` -- the ``JobSpec.solvent``
field stays the solvent-domain convenience alias and is untouched. The dry metric
name (``reactivity_proxy``) rides in on ``exp.objective.metric`` (already
parameterised), so ``spec.py``/``compute.py`` are likewise unchanged.
"""

from __future__ import annotations

# ---- bond lengths (Angstrom) / angles (deg) baked into the Z-matrices ----

_NH3 = """
N
H 1 1.012
H 1 1.012 2 106.7
H 1 1.012 2 106.7 3 120.0
"""

_PH3 = """
P
H 1 1.42
H 1 1.42 2 93.5
H 1 1.42 2 93.5 3 120.0
"""

_PF3 = """
P
F 1 1.57
F 1 1.57 2 97.8
F 1 1.57 2 97.8 3 120.0
"""

_PCL3 = """
P
Cl 1 2.04
Cl 1 2.04 2 100.0
Cl 1 2.04 2 100.0 3 120.0
"""

# Trimethylphosphine P(CH3)3 -- the closest small honest proxy for a real Suzuki
# trialkylphosphine ligand; still only 13 atoms (converges in a few seconds).
_PME3 = """
P
C 1 1.84
C 1 1.84 2 100.0
C 1 1.84 2 100.0 3 120.0
H 2 1.09 1 110.0 3 60.0
H 2 1.09 1 110.0 3 180.0
H 2 1.09 1 110.0 3 300.0
H 3 1.09 1 110.0 4 60.0
H 3 1.09 1 110.0 4 180.0
H 3 1.09 1 110.0 4 300.0
H 4 1.09 1 110.0 2 60.0
H 4 1.09 1 110.0 2 180.0
H 4 1.09 1 110.0 2 300.0
"""

#: name -> (zmatrix, charge, spin=2S). Closed-shell singlets.
CATALYSTS: dict[str, tuple[str, int, int]] = {
    "pf3": (_PF3, 0, 0),
    "pme3": (_PME3, 0, 0),
    "ph3": (_PH3, 0, 0),
    "pcl3": (_PCL3, 0, 0),
    "nh3": (_NH3, 0, 0),
}

#: Public normalized ligand descriptor coordinate (in [0, 1]) -- the "categorical
#: level -> physical coordinate" table for the catalyst domain, the exact analogue
#: of adapters/wet/screen.SOLVENT_POLARITY. NOT truth: it is a public design
#: descriptor the dry leg estimates (dipole) and the wet leg realises by mixing two
#: bracketing stocks. Expressed in the generic ``{level: {coord: value}}`` shape
#: (summit ``CategoricalVariable.descriptors``, INDEX_M19_DOMAIN2 §5) so it plugs
#: straight into the generalized wet path (``compile_wet(..., descriptors=...)``)
#: and is transcribed verbatim into the domain yaml's per-variable ``descriptors``
#: block once session B lands the schema field (see catalyst_screen.yaml).
CATALYST_DESCRIPTORS: dict[str, dict[str, float]] = {
    "pf3": {"coord": 0.05},
    "pme3": {"coord": 0.30},
    "ph3": {"coord": 0.50},
    "pcl3": {"coord": 0.75},
    "nh3": {"coord": 1.00},
}


def catalyst_names() -> list[str]:
    """Sorted list of preset catalyst-ligand names."""
    return sorted(CATALYSTS)


def geometry_for(name: str) -> tuple[str, int, int]:
    """Return (zmatrix, charge, spin) for a preset catalyst name.

    Raises KeyError with the available names if unknown.
    """
    try:
        return CATALYSTS[name]
    except KeyError:
        raise KeyError(
            f"unknown catalyst {name!r}; available presets: {catalyst_names()}"
        )


def catalyst_params(name: str) -> dict[str, object]:
    """Candidate/well ``params`` for a catalyst level, feeding the UNCHANGED
    ``PySCFDryAdapter`` via an explicit ``geometry`` (zero-adapter-change contract).

    Returns ``{"catalyst": name, "geometry": <zmatrix>, "charge": .., "spin": ..}``.
    A candidate builder (mcl / a test) spreads this into its param dict; the dry
    adapter reads ``geometry`` and ignores the absent ``solvent`` preset.
    """
    zmat, charge, spin = geometry_for(name)
    return {"catalyst": name, "geometry": zmat, "charge": charge, "spin": spin}
