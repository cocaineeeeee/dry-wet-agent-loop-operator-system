"""M27 active selection (charter DoD: "active selection 挑下一个 perturbation").

Given the ADMITTED backends (those that cleared the baseline-gate) and a pool of candidate
un-run perturbations, choose the most *informative* next perturbation to observe. v0.1
informativeness = model DISAGREEMENT (variance of predicted mean responses across admitted
backends) + predictive UNCERTAINTY (mean per-axis std). High disagreement/uncertainty =
the observation that most reduces our ignorance.

Closes the loop (charter DoD #7 -- changed knowledge changes a later decision): certified
causal knowledge is fed back as ``certified_axes``; a candidate whose informative axes are
already causally certified is DOWN-WEIGHTED, so what we have already learned steers the
next pick away from redundancy. Passing an empty vs non-empty ``certified_axes`` visibly
re-orders the selection.

Only ADMITTED models vote (baseline-gate first): an un-admitted expensive proposer does
not get to steer acquisition (bio_refs §1.4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from expos.adapters.models.virtual_cell import BioModelBackend, PerturbationBatch


@dataclass(frozen=True)
class SelectionScore:
    pert_id: str
    disagreement: float
    uncertainty: float
    redundancy_penalty: float
    score: float


def select_next_perturbation(
    backends: list[BioModelBackend],
    candidates: PerturbationBatch,
    *,
    certified_axes: dict[str, set[int]] | None = None,
    redundancy_weight: float = 0.5,
) -> list[SelectionScore]:
    """Rank ``candidates`` by informativeness for the admitted ``backends``.

    ``certified_axes`` maps ``pert_id -> {already-certified axis indices}``; a candidate's
    score is reduced in proportion to how much of its predicted top-effect mass lies on
    already-certified axes. Returns the full ranking (highest score first).
    """
    if not backends:
        raise ValueError("select_next_perturbation: no admitted backends to vote")
    certified_axes = certified_axes or {}

    # (n_backend, n_cand, G) stack of predicted means; per-backend mean std too.
    means = np.stack(
        [np.stack([p.mean for p in b.predict(candidates)]) for b in backends]
    )  # (B, n, G)
    stds = np.stack(
        [np.stack([p.std for p in b.predict(candidates)]) for b in backends]
    )  # (B, n, G)

    disagreement = means.var(axis=0).mean(axis=1)  # (n,)  cross-backend variance
    uncertainty = stds.mean(axis=(0, 2))  # (n,)  mean predictive std
    consensus = means.mean(axis=0)  # (n, G)

    out: list[SelectionScore] = []
    for i in range(candidates.n):
        pid = candidates.pert_ids[i]
        top = set(np.argsort(-np.abs(consensus[i]))[:8].tolist())
        cert = certified_axes.get(pid, set())
        redundancy = len(top & cert) / max(len(top), 1)
        base = float(disagreement[i] + uncertainty[i])
        score = base * (1.0 - redundancy_weight * redundancy)
        out.append(
            SelectionScore(
                pert_id=pid,
                disagreement=float(disagreement[i]),
                uncertainty=float(uncertainty[i]),
                redundancy_penalty=redundancy,
                score=score,
            )
        )
    out.sort(key=lambda s: s.score, reverse=True)
    return out
