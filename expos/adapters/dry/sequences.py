"""Biological dry-leg sequence-feature proxies (M24, ``cell_free_expression_screen``).

Third-domain dry leg: the biological analogue of ``solvents.py`` / ``catalysts.py``.
Instead of a molecular dipole, a DNA/RNA *construct sequence* is reduced to FOUR
self-contained honest-biased feature proxies -- GC content, codon adaptation index
(CAI), ribosome-binding-site (RBS) strength, and 5'UTR RNA-folding Delta G -- and
synthesised into a single ``expression_proxy`` scalar. v1 deliberately does NOT run
a large protein/RNA language model (INDEX_M24_SEQFEAT; the ESM / RNA-FM upgrade seam
is the single :func:`expression_features` synthesis function, marked below).

HONEST-BIASED PROXY (identical semantics to the dry solvent / catalyst legs, see
``catalysts.py``'s note): each feature carries REAL approximation error and is only a
*correlated public design descriptor* of expression propensity. The TRUE fluorescence
expression level is NOT here -- it is hidden in the wet plate-reader truth surface
(``sim_reader`` ``expression_high`` / ``expression_flipped`` / ``flat`` faces), never
in a sequence descriptor. A construct sequence is public design knowledge; computing
features off it leaks no truth (mirrors the SOLVENT_POLARITY / catalyst dipole notes).

not-copy boundary (INDEX_M24_SEQFEAT §8): no ViennaRNA C build / Zuker DP re-impl
(soft-depend on the pip wheel or fall back to a heuristic), no full Salis RBS-Calculator
+ NuPACK model (only the public anti-SD anchor + optimal-spacing form), no large
reference-genome pull (one static Sharp & Li 1987 E. coli w-table baked in below).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 1. GC content -- the ONE exact (non-approximate) feature
# ---------------------------------------------------------------------------


def gc_content(seq: str) -> float:
    """Exact G+C fraction of ``seq`` -- a pure character count, NO approximation.

    HONEST-BIASED PROXY note: the COUNT is exact; the proxy bias lives ONLY in the
    downstream "higher GC -> higher expression" monotone assumption (system-dependent,
    a crude prior applied in :func:`expression_features`), not in this arithmetic.
    RNA ``U`` is treated as non-GC (uppercased ``T`` equivalent)."""
    s = seq.upper()
    n = len(s)
    if n == 0:
        return 0.0
    return (s.count("G") + s.count("C")) / n


# ---------------------------------------------------------------------------
# 2. Codon adaptation index (CAI) -- log-domain geometric mean over a static
#    E. coli relative-adaptiveness (w) table
# ---------------------------------------------------------------------------

#: Static E. coli relative adaptiveness (w in [0, 1]) per sense codon (DNA alphabet).
#: SOURCE: Sharp & Li 1987, *Nucleic Acids Res.* 15:1281 -- published E. coli w-values
#: derived from highly-expressed genes. Single-codon amino acids (Met=ATG, Trp=TGG)
#: and the three stops are DELIBERATELY ABSENT so they are excluded from the geometric
#: mean (a w that is always 1.0 would otherwise dilute the index; see Benjamin-Lee /
#: biopython CAI edge handling, INDEX_M24_SEQFEAT §2).
#: HONEST-BIASED PROXY (core near-approximation): using the GENERIC E. coli w-table as
#: a stand-in for the SPECIFIC cell-free reagent kit's true codon preference is the
#: proxy's central bias -- the cell-free translation machinery approximates E. coli but
#: is not it. M24+ upgrade: re-derive w from a public highly-expressed gene set so the
#: table feeds the expos data-self-derivation fingerprint chain rather than being frozen.
ECOLI_W: dict[str, float] = {
    # Phe / Leu
    "TTT": 0.296, "TTC": 1.000,
    "TTA": 0.020, "TTG": 0.020,
    "CTT": 0.042, "CTC": 0.037, "CTA": 0.007, "CTG": 1.000,
    # Ile
    "ATT": 0.185, "ATC": 1.000, "ATA": 0.003,
    # Val
    "GTT": 1.000, "GTC": 0.066, "GTA": 0.495, "GTG": 0.221,
    # Ser
    "TCT": 1.000, "TCC": 0.744, "TCA": 0.077, "TCG": 0.017,
    "AGT": 0.085, "AGC": 0.410,
    # Pro
    "CCT": 0.070, "CCC": 0.012, "CCA": 0.135, "CCG": 1.000,
    # Thr
    "ACT": 0.965, "ACC": 1.000, "ACA": 0.076, "ACG": 0.099,
    # Ala
    "GCT": 1.000, "GCC": 0.122, "GCA": 0.586, "GCG": 0.424,
    # Tyr
    "TAT": 0.239, "TAC": 1.000,
    # His / Gln
    "CAT": 0.291, "CAC": 1.000,
    "CAA": 0.124, "CAG": 1.000,
    # Asn / Lys
    "AAT": 0.051, "AAC": 1.000,
    "AAA": 1.000, "AAG": 0.253,
    # Asp / Glu
    "GAT": 0.434, "GAC": 1.000,
    "GAA": 1.000, "GAG": 0.259,
    # Cys
    "TGT": 0.500, "TGC": 1.000,
    # Arg
    "CGT": 1.000, "CGC": 0.356, "CGA": 0.004, "CGG": 0.004,
    "AGA": 0.004, "AGG": 0.002,
    # Gly
    "GGT": 1.000, "GGC": 0.724, "GGA": 0.010, "GGG": 0.019,
}

#: Floor on w so an all-rarest-codon sequence still yields a finite log (avoids log(0)).
_W_FLOOR = 1e-3


def cai(seq: str, w_table: dict[str, float] | None = None) -> float:
    """Codon adaptation index of a coding sequence -- the log-domain geometric mean
    of per-codon relative adaptiveness (Sharp & Li 1987): ``exp(mean(log w))``.

    Log-domain form (biopython ``CodonAdaptationIndex.calculate`` style) avoids the
    long-sequence underflow a naive product would hit. Codons absent from ``w_table``
    (the two single-codon amino acids Met/Trp and the three stops) are EXCLUDED from
    the mean. Returns 0.0 for a sequence with no scorable codon.

    HONEST-BIASED PROXY: see the :data:`ECOLI_W` note -- the generic table is a biased
    stand-in for the true cell-free codon preference."""
    table = w_table if w_table is not None else ECOLI_W
    s = seq.upper().replace("U", "T")
    logs: list[float] = []
    for i in range(0, len(s) - (len(s) % 3), 3):
        codon = s[i : i + 3]
        w = table.get(codon)
        if w is None:  # Met / Trp / stop / unknown -> excluded from the geometric mean
            continue
        logs.append(math.log(max(w, _W_FLOOR)))
    if not logs:
        return 0.0
    return math.exp(sum(logs) / len(logs))


# ---------------------------------------------------------------------------
# 3. RBS strength -- crude anti-SD complementarity x spacing x start-codon proxy
# ---------------------------------------------------------------------------

#: E. coli 16S rRNA 3'-end anti-Shine-Dalgarno anchor (public constant, Salis 2009
#: RBS_Calculator: ``rRNA = "acctcctta"``). The Shine-Dalgarno motif in the 5'UTR is
#: complementary to this; a strong RBS carries an SD closely matching its reverse
#: complement at the right spacing upstream of the start codon.
ANTI_SD = "ACCTCCTTA"

#: Optimal SD-to-start spacing (nt); Salis 2009 ``optimal_spacing = 5``.
_OPTIMAL_SPACING = 5
_SPACING_SIGMA = 2.5

#: Start-codon initiation preference (crude relative bonus; Salis start_codon_energies
#: form, ATG strongest). Anything else falls back to the weak default.
_START_CODON_BONUS: dict[str, float] = {"ATG": 1.0, "GTG": 0.5, "TTG": 0.3}
_START_CODON_DEFAULT = 0.3

_COMPLEMENT = {"A": "T", "T": "A", "G": "C", "C": "G", "N": "N"}


def _revcomp(seq: str) -> str:
    return "".join(_COMPLEMENT.get(b, "N") for b in reversed(seq.upper()))


#: The SD motif most complementary to the anti-SD anchor (matching this by identity in
#: the 5'UTR == being complementary to the anti-SD). e.g. "TAAGGAGGT".
_IDEAL_SD = _revcomp(ANTI_SD)


def _best_sd_match(region: str) -> tuple[int, int]:
    """Slide the ideal SD motif across ``region``; return (best_base_matches, end_pos)."""
    length = len(_IDEAL_SD)
    best, best_end = 0, length
    for i in range(0, len(region) - length + 1):
        window = region[i : i + length]
        matches = sum(1 for a, b in zip(window, _IDEAL_SD) if a == b)
        if matches > best:
            best, best_end = matches, i + length
    return best, best_end


def rbs_strength(rbs_region: str, start_codon: str | None = None) -> float:
    """CRUDE PROXY for ribosome-binding-site strength in [0, 1].

    NOT a free energy and NOT a translation-initiation rate -- it is the heuristic
    product of three public sequence forms (INDEX_M24_SEQFEAT §3): (1) best anti-SD
    reverse-complement base match in the 5'UTR window, (2) a Gaussian penalty on the
    SD-to-start spacing centred at the optimal ~5 nt, and (3) a small start-codon
    initiation bonus. It ignores mRNA-folding competition (that effect is approximated
    separately by :func:`folding_dg`) -- honestly a crude sequence feature, not the
    Salis RBS-Calculator biophysical model (which needs NuPACK; not copied)."""
    region = rbs_region.upper().replace("U", "T")
    if len(region) < len(_IDEAL_SD):
        return 0.0
    best, best_end = _best_sd_match(region)
    comp_score = best / len(_IDEAL_SD)
    spacing = len(region) - best_end  # nt from SD match end to the 3' (start-codon) side
    spacing_penalty = math.exp(-(((spacing - _OPTIMAL_SPACING) / _SPACING_SIGMA) ** 2))
    codon = (start_codon or "ATG").upper().replace("U", "T")[:3]
    start_bonus = _START_CODON_BONUS.get(codon, _START_CODON_DEFAULT)
    return comp_score * spacing_penalty * start_bonus


# ---------------------------------------------------------------------------
# 4. RNA folding Delta G -- soft ViennaRNA MFE, else a GC + hairpin heuristic
# ---------------------------------------------------------------------------


def _longest_hairpin_stem(seq: str) -> int:
    """Longest self-complementary stem (with a >= 4 nt loop) -- a crude hairpin probe."""
    s = seq.upper().replace("U", "T")
    n = len(s)
    best = 0
    for i in range(n):
        for j in range(i + 5, n):  # >= 4 nt loop between the two stem arms
            t = 0
            while i + t < j - t and _COMPLEMENT.get(s[i + t]) == s[j - t]:
                t += 1
            if t > best:
                best = t
    return best


def _folding_dg_heuristic(utr5: str) -> float:
    """Fallback 5'UTR structure-stability proxy (CRUDE PROXY, not MFE): GC content x
    length + longest hairpin stem, sign-fixed NEGATIVE (more stable = more negative)."""
    s = utr5.upper().replace("U", "T")
    if not s:
        return 0.0
    gc = gc_content(s)
    stem = _longest_hairpin_stem(s)
    return -(gc * len(s) * 0.05 + stem * 0.5)


def folding_dg(utr5: str) -> float:
    """5'UTR minimum-free-energy proxy (kcal/mol-ish).

    Soft-depends on ViennaRNA (``import RNA``; the pip wheel, NOT the vendored C build):
    when present, returns the real MFE from ``RNA.fold``. On ImportError falls back to
    :func:`_folding_dg_heuristic` (labelled crude proxy). More NEGATIVE Delta G = more
    stable 5'UTR structure = more likely to occlude the RBS = LOWER expression -- the
    sign is fixed here and is a biased directional assumption. UPGRADE: a true MFE is
    already one soft-dependency away; a full RNA-FM/UTR-LM head is the language-model seam."""
    try:
        import RNA  # ViennaRNA pip wheel; soft (optional) dependency
    except ImportError:
        return _folding_dg_heuristic(utr5)
    seq = utr5.upper().replace("T", "U")
    if not seq:
        return 0.0
    _structure, mfe = RNA.fold(seq)
    return float(mfe)


# ---------------------------------------------------------------------------
# 5. Synthesis -- 1 main scalar (expression_proxy) + 4 raw features
# ---------------------------------------------------------------------------

#: Uncalibrated equal-ish prior weights for the synthesis. EXPLICITLY NOT regressed
#: from data -- calibration waits for wet truth-surface backfill (M24+, the expos data-
#: self-derivation fingerprint chain). CAI / RBS are weighted a touch heavier as the
#: more translation-proximate features; folding enters with a NEGATIVE sign (structure
#: suppresses expression). Sum of positive weights is 1.0.
_W_GC = 0.20
_W_CAI = 0.30
_W_RBS = 0.30
_W_FOLD = 0.20
#: |Delta G| scale (kcal/mol) at which the folding penalty saturates to 1.0.
_FOLD_SCALE = 20.0


@dataclass(frozen=True)
class SequenceFeatures:
    """The dry sequence-feature result: 1 synthesised main scalar + 4 raw features.

    Mirrors the dry ``ComputeResult(value + secondary)`` shape (``compute.py`` :129) so
    the discriminator face / QC / ledger need no biological special-casing: the main
    scalar ``expression_proxy`` rides in as ``value``; ``gc / cai / rbs_strength /
    folding_dg / transcript_length`` are the ``secondary`` channels."""

    gc: float
    cai: float
    rbs_strength: float
    folding_dg: float
    transcript_length: int
    expression_proxy: float

    def secondary(self) -> dict[str, float]:
        """The four raw features + transcript length as the ``secondary`` map."""
        return {
            "gc": self.gc,
            "cai": self.cai,
            "rbs_strength": self.rbs_strength,
            "folding_dg": self.folding_dg,
            "transcript_length": float(self.transcript_length),
        }


def _folding_penalty(dg: float) -> float:
    """Map a (negative) Delta G to a [0, 1] suppression penalty (|dg| capped at scale)."""
    return min(abs(dg) / _FOLD_SCALE, 1.0)


def expression_features(
    sequence: str,
    promoter: str | None = None,
    rbs: str | None = None,
    cds: str | None = None,
) -> SequenceFeatures:
    """Compute the four proxies off a construct and synthesise ``expression_proxy``.

    Region routing (all inputs public design knowledge): GC + transcript length over the
    whole ``sequence``; CAI over ``cds`` (or the whole sequence if no CDS component);
    RBS + folding over the ``rbs`` 5'UTR component (or the leading 30 nt of ``sequence``).

    WEIGHTS ARE AN UNCALIBRATED SIMPLE PRIOR -- explicitly NOT regressed from data (see
    the weight-constants note). UPGRADE SEAM: replace this function body with an
    ESM / RNA-FM embedding -> regression head; the :class:`SequenceFeatures` (value +
    secondary) shape stays fixed so the discriminator face / QC / provider never move."""
    cds_region = cds if cds else sequence
    rbs_region = rbs if rbs else sequence[:30]
    start_codon = (cds[:3] if cds else None)

    gc = gc_content(sequence)
    cai_v = cai(cds_region)
    rbs_v = rbs_strength(rbs_region, start_codon=start_codon)
    dg = folding_dg(rbs_region)

    proxy = (
        _W_GC * gc
        + _W_CAI * cai_v
        + _W_RBS * rbs_v
        - _W_FOLD * _folding_penalty(dg)
    )
    proxy = max(0.0, proxy)

    return SequenceFeatures(
        gc=gc,
        cai=cai_v,
        rbs_strength=rbs_v,
        folding_dg=dg,
        transcript_length=len(sequence),
        expression_proxy=proxy,
    )
