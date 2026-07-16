"""Diversity-aware acquisition for the M25 generative-construct domain.

The acquisition (selection) layer of the model-competition stack (docs/BIOLOGY_
PROGRAM_2026.md §3): given a pool of proposed designs with dry proxy previews,
pick the batch to (simulated-)assay next. It is **observation-independent** and
**deterministic** -- it consumes only the dry proxy VALUE and sequence COMPOSITION,
never a wet observation, so it can feed the integration owner's (B's) policy
without touching the claim path. Nothing here certifies anything.

ADAPTed from ALDE (references/ALDE, Nat. Commun. 2025; §7 of docs/bio_refs/01):
  * ALDE scores every point in a discrete pool then argmax-selects, masking the
    already-picked (``src/acquisition.py`` ``get_next_query``). We mirror that
    "score-pool -> greedy pick -> mask" batch structure exactly.
  * ALDE's competition grid is ``4 models x {GREEDY, UCB, TS}``. With no trained
    posterior in v0.1 (auditable-operators-first: ALDE itself shows onehot + a
    simple acquisition suffices to go 12%->93% wet without a PLM), we implement
    the acquisition axis over an onehot/composition encoding and expose GREEDY +
    a diversity-augmented (MMR) selector. The GP-posterior UCB/TS variants are the
    explicit v2 seam (a supervised scorer feeds a real posterior); here "novelty"
    (composition distance to the picked set) is an honest exploration surrogate,
    NOT a calibrated GP uncertainty (labelled as such).

NOT-COPY: no botorch/torch dependency, no GP posterior in v0.1 (§4 no framework
transplant before a discriminative test). The onehot idea and the grid SHAPE are
adopted; the code is a clean domain-local reimplementation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from domains.generative_construct.objects import ConstructDesign

_BASES = ("A", "C", "G", "T")


# ---------------------------------------------------------------------------
# Encoding (ALDE-style onehot / composition) -- observation-independent
# ---------------------------------------------------------------------------


def composition_vector(components: dict[str, str]) -> list[float]:
    """A fixed-length composition encoding of a construct: mononucleotide (4) +
    dinucleotide (16) frequencies over the full sequence = a 20-dim vector.

    Deterministic, observation-independent, and length-normalized so designs of
    different length are comparable. This is the "onehot-ish" featurization the
    diversity distance runs on (ALDE uses onehot over a fixed site set; a variable-
    length construct needs a composition encoding instead -- same role)."""
    seq = components["sequence"].upper().replace("U", "T")
    n = len(seq)
    mono = [0.0] * 4
    idx = {b: i for i, b in enumerate(_BASES)}
    for b in seq:
        if b in idx:
            mono[idx[b]] += 1.0
    mono = [c / n for c in mono] if n else mono
    di = [0.0] * 16
    if n >= 2:
        for i in range(n - 1):
            a, b = seq[i], seq[i + 1]
            if a in idx and b in idx:
                di[idx[a] * 4 + idx[b]] += 1.0
        total = sum(di)
        if total:
            di = [c / total for c in di]
    return mono + di


def composition_distance(a: dict[str, str], b: dict[str, str]) -> float:
    """Euclidean distance between two constructs' composition vectors (>= 0)."""
    va, vb = composition_vector(a), composition_vector(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(va, vb)))


# ---------------------------------------------------------------------------
# Selection strategies (deterministic greedy over a discrete pool)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """One acquisition pick: the design, its selection rank, and the acquisition
    score it was picked on (value, diversity, or the blended MMR score)."""

    design_id: str
    rank: int
    value: float
    diversity: float
    acq_score: float


def _normed_values(pool: list[ConstructDesign]) -> dict[str, float]:
    """Min-max normalize dry proxies to [0,1] (constant pool -> all 0.5)."""
    vals = [d.proxy if d.proxy is not None else 0.0 for d in pool]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    return {
        d.design_id: (0.5 if span == 0 else ((d.proxy or 0.0) - lo) / span)
        for d in pool
    }


def select(
    pool: list[ConstructDesign],
    k: int,
    strategy: str = "value_diversity",
    lam: float = 0.5,
) -> list[Selection]:
    """Select up to ``k`` designs from ``pool`` under a named strategy.

    Strategies (all deterministic; ties broken by design_id):
      * ``greedy``          -- ALDE GREEDY: rank by dry proxy value only.
      * ``diversity``       -- farthest-point: maximize min composition distance to
                               the already-picked set (pure exploration).
      * ``value_diversity`` -- MMR blend: at each step maximize
                               ``(1-lam)*value_norm + lam*min_dist_to_picked``.
                               ``lam=0`` -> greedy, ``lam=1`` -> diversity.

    Observation-independent (uses only proxy + composition). Returns the picks in
    selection order with their component scores (feeds B's policy)."""
    if strategy not in ("greedy", "diversity", "value_diversity"):
        raise ValueError(f"unknown strategy {strategy!r}")
    if k <= 0 or not pool:
        return []
    value = _normed_values(pool)
    remaining = {d.design_id: d for d in pool}
    picked: list[Selection] = []
    picked_designs: list[ConstructDesign] = []

    def min_dist(d: ConstructDesign) -> float:
        if not picked_designs:
            return 1.0  # first pick: max exploration credit
        return min(
            composition_distance(d.components, p.components) for p in picked_designs
        )

    while remaining and len(picked) < k:
        best_id = None
        best_score = -math.inf
        best_val = best_div = 0.0
        for did in sorted(remaining):  # stable, id-ordered iteration = explicit tie-break
            d = remaining[did]
            v = value[did]
            div = min_dist(d)
            if strategy == "greedy":
                score = v
            elif strategy == "diversity":
                score = div
            else:  # value_diversity (MMR)
                score = (1.0 - lam) * v + lam * div
            if score > best_score:
                best_score, best_id, best_val, best_div = score, did, v, div
        d = remaining.pop(best_id)
        picked_designs.append(d)
        picked.append(
            Selection(
                design_id=best_id,
                rank=len(picked) + 1,
                value=best_val,
                diversity=best_div,
                acq_score=best_score,
            )
        )
    return picked


#: The ALDE-mirrored acquisition menu name -> the strategy this v0.1 implements.
#: (GP-posterior UCB/TS are the v2 seam; here the "novelty" column is the
#: composition-distance exploration surrogate, honestly not a GP posterior.)
ACQUISITION_GRID: dict[str, str] = {
    "GREEDY": "greedy",
    "DIVERSITY": "diversity",
    "GREEDY+DIVERSITY(MMR)": "value_diversity",
}
