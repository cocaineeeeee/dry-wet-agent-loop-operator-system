"""Deterministic Bradley-Terry pairwise strength — ADAPTED from Robin (v0.1).

Robin (Future-House, ``references/robin/``) ranks candidate assays and therapeutic
candidates by having an LLM emit pairwise comparisons and then calling
``choix.ilsr_pairwise`` (a Bradley-Terry / Luce spectral estimator) to turn those pairwise
"games" into a single strength score per item (``robin/assays.py`` L232,
``robin/candidates.py``). That ranking IS Robin's "discovery" output.

**What expos ADAPTS (charter M28 finding, docs/bio_refs/04):** we keep the *method* —
aggregate pairwise preferences into a per-item strength — but we DO NOT let it be a
discovery/verdict. Here the strengths are a PROPOSER-INTERNAL PRIORITISATION only (an
acquisition-layer heuristic that decides which hypothesis to *test first*); the actual
verdict is produced solely by trusted observations routed through the claim ledger. So the
demo can (and does) show trusted evidence OVERTURNING this ranking.

**What expos does NOT copy:** the ``choix`` dependency (kept out — no framework transplant
before a discriminative test, charter §4) and the LLM-as-judge pairwise emitter (the
comparisons here are derived deterministically from stated priors/parsimony, not from an
LLM's free-text preference). This is a ~30-line pure-Python MM estimator, no external dep.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairwiseComparison:
    """One pairwise "game": ``winner`` was preferred over ``loser`` (by item index).
    ``weight`` lets a comparison count for more than one game (e.g. a strong prior)."""

    winner: int
    loser: int
    weight: float = 1.0


def bradley_terry_strengths(
    n_items: int,
    comparisons: list[PairwiseComparison],
    *,
    iters: int = 200,
    reg: float = 1e-3,
    tol: float = 1e-9,
) -> list[float]:
    """Fit Bradley-Terry item strengths from weighted pairwise comparisons via the
    classic Zermelo / minorisation-maximisation (MM) iteration — deterministic, pure, no
    randomness, no external dependency (mirrors the PURPOSE of ``choix.ilsr_pairwise``).

    Returns one non-negative strength per item, normalised to a geometric mean of 1 so the
    scale is stable and comparable across calls. ``reg`` is a tiny symmetric smoothing
    (adds ``reg`` phantom wins/losses against a uniform opponent) so an item that never
    won still gets a finite, ordered strength. Identical inputs give bit-for-bit identical
    outputs (determinism, charter DoD #10 spirit / kernel gate K5)."""
    if n_items <= 0:
        return []
    # wins[i] = total weighted wins of i; pair[i][j] = total weighted games between i,j.
    wins = [reg] * n_items  # reg phantom win vs the uniform field
    pair = [[0.0] * n_items for _ in range(n_items)]
    for c in comparisons:
        wins[c.winner] += c.weight
        pair[c.winner][c.loser] += c.weight
        pair[c.loser][c.winner] += c.weight
    # phantom games vs a uniform opponent keep the estimator well-posed for sparse graphs.
    for i in range(n_items):
        for j in range(n_items):
            if i != j:
                pair[i][j] += reg

    p = [1.0] * n_items
    for _ in range(iters):
        new_p = [0.0] * n_items
        for i in range(n_items):
            denom = 0.0
            for j in range(n_items):
                if i == j:
                    continue
                denom += pair[i][j] / (p[i] + p[j])
            new_p[i] = wins[i] / denom if denom > 0 else p[i]
        # normalise to geometric-mean 1 (deterministic gauge fix).
        log_mean = sum(_safe_log(x) for x in new_p) / n_items
        scale = _safe_exp(-log_mean)
        new_p = [x * scale for x in new_p]
        if max(abs(a - b) for a, b in zip(new_p, p)) < tol:
            p = new_p
            break
        p = new_p
    return p


def _safe_log(x: float) -> float:
    from math import log

    return log(x) if x > 0 else -30.0


def _safe_exp(x: float) -> float:
    from math import exp

    return exp(max(-60.0, min(60.0, x)))
