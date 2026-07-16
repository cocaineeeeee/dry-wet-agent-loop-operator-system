"""Domain-local SIMULATED wet phenotype for the M25 generative-construct domain.

SIMULATION LEVEL (honest label, docs/BIOLOGY_PROGRAM_2026.md §5): this is a
domain-local *simulated* wet plate-reader phenotype for a designed construct --
NOT a real assay and NOT retrospective data. It exists so the M25 organ can close
a full loop (design -> dry proxy -> simulated wet observation -> claim -> knowledge
-> next design) domain-locally before the integration owner wires the real socket
``sim_reader`` / mcl path. It reuses the SHARED, domain-neutral ``TruthSurface``
from ``expos.adapters.wet.sim_reader`` (imported, never edited) so the phenotype
mapping (coordinate -> response) is the exact same 1-D Gaussian the rest of the OS
uses -- the truth surface is a reusable domain-neutral runtime piece.

THE HONEST-BIASED-PROXY GAP (why a discriminative "dry ranking overturned by wet"
case exists at all): the dry ``expression_proxy`` is a SEQUENCE-FEATURE proxy
(GC / CAI / RBS-heuristic / folding). It is BLIND to a promoter's measured
transcriptional strength -- it only sees the promoter's GC, not its function. The
wet truth coordinate here is DOMINATED by the promoter's published functional
strength (the real driver of expression). So a design that grafts a strong RBS +
optimal CDS onto a GC-rich-but-transcriptionally-WEAK promoter scores HIGH on the
dry proxy yet expresses LOW in (simulated) wet -- the model ranking is overturned
by the phenotype. This is a mechanistically honest gap, not a tuning artefact.

TRUTH ISOLATION (M24-B red line): the truth coordinate and the phenotype live
here (the "wet" side); the dry leg never imports this module. A phenotype reading
is a TRUSTED OBSERVATION; the dry proxy is only a proposal/preview.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from expos.adapters.dry.constructs import _ANDERSON, _RBS_LADDER  # public catalogues
from expos.adapters.dry.sequences import cai
from expos.adapters.wet.sim_reader import TruthSurface

# ---------------------------------------------------------------------------
# The hidden true drivers of expression (the "wet" knowledge the dry leg lacks).
# ---------------------------------------------------------------------------

#: promoter sequence (upper) -> published functional strength in [0,1]. This is
#: the DOMINANT true driver of expression and the dry proxy is blind to it (the
#: dry leg sees only the promoter's GC, never its transcriptional output).
_PROMOTER_STRENGTH: dict[str, float] = {
    promoter.upper(): float(rel) for (_pid, promoter, rel) in _ANDERSON
}

#: RBS ladder element (upper) -> rank-normalized true strength (strongest ladder
#: element -> 1.0, weakest -> 0.0). A secondary true driver.
_RBS_TRUE: dict[str, float] = {}
_n_rbs = len(_RBS_LADDER)
for _i, _rbs in enumerate(_RBS_LADDER):
    # ladder is ordered strongest->weakest; keep the STRONGEST value on collisions
    _val = 1.0 - _i / (_n_rbs - 1)
    _RBS_TRUE[_rbs.upper()] = max(_RBS_TRUE.get(_rbs.upper(), 0.0), _val)

# truth-coordinate mixing weights (promoter dominant; sum to 1.0)
_W_PROMOTER = 0.60
_W_RBS = 0.25
_W_CDS = 0.15


def promoter_strength(promoter: str) -> float:
    """True functional strength of a promoter (catalogue lookup; 0.5 default for
    an off-catalogue promoter). The dry proxy cannot compute this."""
    return _PROMOTER_STRENGTH.get(promoter.upper(), 0.5)


def rbs_true_strength(rbs: str) -> float:
    """True RBS strength (ladder-rank normalized; 0.5 for off-ladder)."""
    return _RBS_TRUE.get(rbs.upper(), 0.5)


def cds_true_quality(cds: str) -> float:
    """True CDS translational quality = codon adaptation index (0..1). The dry
    proxy also uses CAI, so dry and wet AGREE on the CDS axis by construction --
    the honest proxy-truth divergence lives in the PROMOTER axis."""
    return cai(cds)


def truth_coord(components: dict[str, str]) -> float:
    """Hidden TRUE expression coordinate of a construct in [0,1] -- the argument
    to the shared ``TruthSurface``. Dominated by promoter functional strength."""
    return (
        _W_PROMOTER * promoter_strength(components.get("promoter", ""))
        + _W_RBS * rbs_true_strength(components.get("rbs", ""))
        + _W_CDS * cds_true_quality(components.get("cds", ""))
    )


# ---------------------------------------------------------------------------
# Simulated wet phenotype (reuses the shared domain-neutral TruthSurface)
# ---------------------------------------------------------------------------

#: The M24 biological truth faces (names live in the shared sim_reader):
#:  * expression_high    -- POSITIVE face: monotone-rising, high-coord expresses
#:                          highest (the "higher-design-wins" seeded claim's face).
#:  * expression_flipped -- NEGATIVE face: monotone-falling, low-coord wins
#:                          (contradicts the seeded claim -- surface-level negative).
#:  * flat               -- NULL/FLAT face: response independent of coord.
POSITIVE_FACE = "expression_high"
NEGATIVE_FACE = "expression_flipped"
FLAT_FACE = "flat"


@dataclass(frozen=True)
class Phenotype:
    """One simulated wet phenotype reading (a TRUSTED observation candidate).

    ``value`` is the simulated fluorescence (truth response + measurement noise);
    ``true_coord`` / ``true_response`` are the server-side truth (recorded here for
    the domain-local loop's audit; a real reading would never expose them)."""

    design_id: str
    value: float
    true_coord: float
    true_response: float
    face: str
    seed: int
    replicate: int


def wet_phenotype(
    design_id: str,
    components: dict[str, str],
    face: str = POSITIVE_FACE,
    seed: int = 0,
    replicate: int = 0,
    noise_sd: float = 0.02,
) -> Phenotype:
    """Simulate one wet phenotype reading for a construct on a named truth face.

    Deterministic given ``(design_id, face, seed, replicate)``: the RNG is keyed
    on all of them, so a technical replicate is a NEW draw (never a copy of an
    earlier reading -- M24-B technical-replicate discipline), yet the whole run is
    reproducible. Uses the SHARED ``TruthSurface`` (domain-neutral) for the
    coordinate->response map."""
    surface = TruthSurface.from_profile(face)
    coord = truth_coord(components)
    true_resp = surface.response(coord)
    rng = random.Random(f"{design_id}|{face}|{seed}|{replicate}")
    noise = rng.gauss(0.0, noise_sd)
    return Phenotype(
        design_id=design_id,
        value=true_resp + noise,
        true_coord=coord,
        true_response=true_resp,
        face=face,
        seed=seed,
        replicate=replicate,
    )


def measure_pool(
    designs: dict[str, dict[str, str]],
    face: str = POSITIVE_FACE,
    seed: int = 0,
    replicates: int = 3,
    noise_sd: float = 0.02,
) -> dict[str, list[Phenotype]]:
    """Measure ``replicates`` biological replicates for each design in a pool.

    Returns design_id -> [Phenotype, ...]. Each replicate is an INDEPENDENT draw
    (distinct RNG key), so replicate spread is real measurement variance, not a
    duplicated value (the technical-replicate red line)."""
    return {
        did: [
            wet_phenotype(did, comp, face=face, seed=seed, replicate=r, noise_sd=noise_sd)
            for r in range(replicates)
        ]
        for did, comp in designs.items()
    }
