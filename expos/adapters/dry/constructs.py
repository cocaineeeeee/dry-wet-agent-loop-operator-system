"""Preset DNA construct designs for the ``cell_free_expression_screen`` dry domain (M24).

Third-domain existence proof: this is the biological analogue of ``solvents.py`` /
``catalysts.py``. Where the chemistry legs preset a molecular *geometry* (a Z-matrix),
the biology leg presets a *construct sequence* -- a promoter + 5'UTR/RBS + reporter
CDS assembled from REAL public genetic elements. There is NO molecular geometry here
and NONE is fabricated: the dry input of a construct is the sequence itself
(``SequenceProxyAdapter`` consumes ``sequence``/``promoter``/``rbs``/``cds`` params;
it never sees a zmatrix).

HONEST-BIASED PROXY (identical semantics to the dry solvent / catalyst legs): the
elements below are PUBLIC DESIGN KNOWLEDGE, never truth.
  * Promoters: the Anderson constitutive promoter collection (iGEM Registry
    ``BBa_J231xx``); the relative constitutive strengths (RFP a.u. normalized to
    J23100 = 1.00) are the published community measurements -- a public design
    catalogue, NOT this run's observed expression. They calibrate the design
    coordinate (``CONSTRUCT_DESCRIPTORS``), the biological analogue of the catalyst
    ligand dipole coordinate.
  * RBS 5'UTR ladder: the canonical strong Shine-Dalgarno core ``TAAGGAGGT`` (the
    reverse complement of the Salis-2009 anti-SD anchor ``ACCTCCTTA``; it carries the
    B0034 ``AAGGAGG`` core) at the optimal ~5 nt spacing, then progressively mismatched
    toward a non-SD polyA spacer -- a public RBS-tuning design ladder.
  * CDS: the GFP/sfGFP N-terminal peptide (M S K G E E L F T G V V P I L) encoded with
    E. coli-optimal synonymous codons for the strong designs and rare synonymous codons
    (same peptide) for the weak designs -- a public codon-optimization ladder over a
    real reporter ORF fragment.

The TRUE fluorescence expression level is NOT here -- it lives in the wet plate-reader
truth surface (``sim_reader`` ``expression_high`` / ``expression_flipped`` / ``flat``
faces), never in a construct descriptor. Choosing an element of known public strength
and realising its normalized design coordinate is an experimental INPUT; it leaks no
truth (mirrors the SOLVENT_POLARITY / catalyst dipole notes).

DISCRIMINATOR GRADIENT (committed, machine-checked in tests/test_m24_bio_domain.py):
the constructs form a strictly MONOTONE design ladder. The strong end (J23100: strong
promoter + ideal SD + fully codon-optimized ORF) computes ``expression_proxy`` ~= 0.68
and the weak end (J23103: weakest promoter + no SD + rare-codon ORF) ~= 0.20 -- the same
~3.4x discriminative span the committed internals fixtures set (0.68 vs the poly-A 0.14;
the weak end here reads ~0.20 rather than 0.14 BY DESIGN, because a real construct still
carries the GC-rich Anderson promoter, which lifts the GC feature -- an honest
provenance point, not a tuning knob). ``expression_proxy`` and the design coordinate
correlate at r ~= 0.96, so the Dry->Wet promotion signal holds.

ZERO-adapter-change contract (M20/M24 launch discipline): ``SequenceProxyAdapter`` is
NOT modified. A construct candidate carries its sequence components in the candidate/well
``params`` (public design input), sourced from :func:`construct_params` below (which
delegates to ``sequence_adapter.sequence_params``). The dry metric name
(``expression_proxy``) rides in on ``exp.objective.metric``.
"""

from __future__ import annotations

from expos.adapters.dry.sequence_adapter import sequence_params
from expos.adapters.dry.sequences import expression_features

# ---------------------------------------------------------------------------
# 1. Real public genetic elements (design catalogue -- NOT truth)
# ---------------------------------------------------------------------------

#: Anderson constitutive promoter collection (iGEM Registry ``BBa_J231xx``): the
#: (part_id, 35-nt promoter sequence, published relative strength in [0, 1]) triples,
#: ordered STRICTLY DESCENDING by relative strength. The relative strength is the
#: public design coordinate (``CONSTRUCT_DESCRIPTORS['...']['coord']``): a normalized
#: "design expression strength", the biological analogue of the catalyst ligand
#: coordinate. HONEST-BIASED PROXY: these are catalogue values, not this run's readings.
_ANDERSON: list[tuple[str, str, float]] = [
    ("J23100", "ttgacggctagctcagtcctaggtacagtgctagc", 1.00),
    ("J23102", "ttgacagctagctcagtcctaggtactgtgctagc", 0.86),
    ("J23104", "ttgacagctagctcagtcctaggtattgtgctagc", 0.72),
    ("J23101", "tttacagctagctcagtcctaggtattatgctagc", 0.70),
    ("J23106", "tttacggctagctcagtcctaggtatagtgctagc", 0.47),
    ("J23110", "tttacggctagctcagtcctaggtacaatgctagc", 0.33),
    ("J23105", "tttacggctagctcagtcctaggtactatgctagc", 0.24),
    ("J23116", "ttgacagctagctcagtcctagggactatgctagc", 0.16),
    ("J23114", "tttatggctagctcagtcctaggtacaatgctagc", 0.10),
    ("J23117", "ttgacagctagctcagtcctagggattgtgctagc", 0.06),
    ("J23103", "ctgatagctagctcagtcctagggattatgctagc", 0.01),
]

#: RBS 5'UTR ladder (one per construct, aligned to ``_ANDERSON`` order): the canonical
#: strong Shine-Dalgarno core ``TAAGGAGGT`` at optimal ~5 nt spacing, progressively
#: mismatched toward a non-SD poly-A spacer. Public RBS-tuning design ladder (the strong
#: end == the reverse complement of the Salis anti-SD anchor). Monotone weakening.
_RBS_LADDER: list[str] = [
    "TAAGGAGGTAAAAA",  # ideal SD, spacing 5 (rbs_strength -> ~1.0)
    "TAAGGAGGTAAAAA",
    "TAAGGAGGCAAAAA",  # 1-base SD mismatch
    "TAAGGACGTAAAAA",
    "TAAGCAGGTAAAAA",
    "TAAGCAGCTAAAAA",
    "TACGCAGCTAAAAA",
    "TACGCAGCTAACAA",
    "AACGCAGCTAACAA",
    "AACGCACCTAACAA",
    "AAAAAAAAAAAAAA",  # no SD (poly-A spacer)
]

#: GFP/sfGFP N-terminal peptide M-S-K-G-E-E-L-F-T-G-V-V-P-I-L, encoded twice: with the
#: E. coli-optimal synonymous codon (strong) and a rare synonymous codon (weak) at each
#: position (position 0 = the ATG start, invariant). A construct's CDS blends the two by
#: its design level -> a public codon-optimization ladder over a real reporter ORF.
_CDS_OPTIMAL: list[str] = [
    "ATG", "TCT", "AAA", "GGT", "GAA", "GAA", "CTG", "TTC",
    "ACC", "GGT", "GTT", "GTT", "CCG", "ATC", "CTG",
]
_CDS_RARE: list[str] = [
    "ATG", "AGT", "AAG", "GGG", "GAG", "GAG", "CTA", "TTT",
    "ACG", "GGA", "GTC", "GTC", "CCC", "ATA", "CTA",
]


def _blend_cds(frac_optimal: float) -> str:
    """Encode the GFP N-terminal peptide with ``frac_optimal`` of its codon positions
    (after the fixed ATG start) taken from the optimal table, the rest from the rare
    table -- a deterministic public codon-optimization ladder. Same peptide throughout."""
    n = len(_CDS_OPTIMAL)
    n_opt = round(frac_optimal * (n - 1))
    return "".join(
        "ATG" if i == 0 else (_CDS_OPTIMAL[i] if i <= n_opt else _CDS_RARE[i])
        for i in range(n)
    )


# ---------------------------------------------------------------------------
# 2. Assembled construct table + public design-coordinate descriptors
# ---------------------------------------------------------------------------


def _build() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    """Assemble the constructs (promoter + RBS 5'UTR + reporter CDS) and their public
    design-coordinate descriptors, in strictly descending design-strength order."""
    constructs: dict[str, dict[str, str]] = {}
    descriptors: dict[str, dict[str, float]] = {}
    n = len(_ANDERSON)
    for i, (part_id, promoter, rel_strength) in enumerate(_ANDERSON):
        cid = part_id.lower()  # construct id, e.g. "j23100"
        promoter_u = promoter.upper()
        rbs = _RBS_LADDER[i]
        cds = _blend_cds(1.0 - i / (n - 1))
        constructs[cid] = {
            "sequence": promoter_u + rbs + cds,
            "promoter": promoter_u,
            "rbs": rbs,
            "cds": cds,
        }
        # coord == the promoter's published relative strength: the normalized design
        # expression coordinate (public design knowledge, NOT truth). The dry
        # expression_proxy is designed to correlate with it (honest-biased proxy).
        descriptors[cid] = {"coord": float(rel_strength)}
    return constructs, descriptors


#: construct_id -> {sequence, promoter, rbs, cds}. The dry input to
#: SequenceProxyAdapter (public design knowledge). Strictly descending design strength.
CONSTRUCTS: dict[str, dict[str, str]] = {}

#: construct_id -> {coord: value in [0,1]} public normalized "design expression
#: strength" coordinate (the biological analogue of CATALYST_DESCRIPTORS). Fed to the
#: wet TruthSurface; the dry expression_proxy is engineered to correlate with it so the
#: Dry->Wet promotion signal is real. Same nested {level: {coord: value}} shape as the
#: chemistry legs, transcribed verbatim into the domain yaml's per-variable descriptors.
CONSTRUCT_DESCRIPTORS: dict[str, dict[str, float]] = {}

CONSTRUCTS, CONSTRUCT_DESCRIPTORS = _build()


# ---------------------------------------------------------------------------
# 3. Helpers
# ---------------------------------------------------------------------------


def construct_names() -> list[str]:
    """Preset construct ids in design-strength order (strongest first)."""
    return list(CONSTRUCTS)


def components_for(construct_id: str) -> dict[str, str]:
    """Return the {sequence, promoter, rbs, cds} components of a preset construct.

    Raises KeyError with the available ids if unknown.
    """
    try:
        return CONSTRUCTS[construct_id]
    except KeyError:
        raise KeyError(
            f"unknown construct {construct_id!r}; available presets: {construct_names()}"
        )


def construct_params(construct_id: str) -> dict[str, object]:
    """Candidate/well ``params`` for a construct level, feeding the UNCHANGED
    ``SequenceProxyAdapter`` via the explicit ``sequence`` key (zero-adapter-change
    contract). Delegates to ``sequence_adapter.sequence_params`` so the param shape is
    the single source of truth. Also stamps the ``construct`` screening-dim key (the
    biological analogue of the ``catalyst`` param) so the wet ``screen_param`` path can
    read the level."""
    comp = components_for(construct_id)
    params = sequence_params(
        sequence=comp["sequence"],
        promoter=comp["promoter"],
        rbs=comp["rbs"],
        cds=comp["cds"],
        construct_id=construct_id,
    )
    params["construct"] = construct_id
    return params


def expression_proxy_for(construct_id: str) -> float:
    """The dry ``expression_proxy`` scalar for a preset construct (deterministic).
    Convenience for tests / Dry->Wet promotion previews -- not a truth channel."""
    comp = components_for(construct_id)
    return expression_features(
        sequence=comp["sequence"],
        promoter=comp["promoter"],
        rbs=comp["rbs"],
        cds=comp["cds"],
    ).expression_proxy
