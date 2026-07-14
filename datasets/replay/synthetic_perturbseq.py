"""Retrospective replay fixture for M27 (charter: "第一版用公开资料做 retrospective replay,
但必须标明不是本次 wet observation").

HONEST PROVENANCE FIRST. This is a **synthetic** retrospective fixture whose structure is
*modeled on* single-gene Perturb-seq (a control state + per-knockout cell-state deltas,
K562-style), NOT downloaded real Perturb-seq numbers. It is generated deterministically
from a seed with a KNOWN ground truth, on purpose: only with a known truth can the
baseline-gate be *provably* discriminative (we can construct both the regime where the
complex model beats the baselines and the regime where it does not). Every dataset it
emits carries :class:`DatasetProvenance` with ``is_wet_observation=False`` and
``validation_level='retrospective'`` -- it is benchmark/calibration material, never a
wet observation produced by this run (charter §4 iron rule).

FOLLOW-UP (declared, not done in v0.1): a real-data replay that ingests the public
Norman/Replogle Perturb-seq via ``references/linear_perturbation_prediction-Paper`` would
slot in behind the SAME :class:`PerturbationDataset` interface with the SAME non-wet
provenance guard. v0.1 uses the synthetic fixture so the gate's discriminative power is
demonstrable, not asserted.

TWO EMBEDDING REGIMES (the whole point of the competition layer):
  * ``informative`` -- the perturbation embedding is aligned with the response structure
    and the truth has local nonlinear (cluster) offsets a kNN can exploit. Here the
    complex backend CAN beat mean+linear -> baseline-gate PASSES.
  * ``scrambled``   -- the embedding is task-misaligned noise (the frozen-foundation-
    embedding failure regime of the Nat. Methods benchmark). No model extracts signal
    beyond the mean -> the complex backend does NOT beat baseline -> baseline-gate logs a
    first-class NEGATIVE claim.
The ground-truth responses are IDENTICAL across regimes; only the embedding handed to the
models differs -- expensive != better is made legible.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from domains.perturbation.objects import (
    DatasetProvenance,
    ObservedResponse,
    Perturbation,
    PerturbationDataset,
)

Regime = Literal["informative", "scrambled"]

_DIM = 40  # cell-state axes (in [20,100])
_EMB_DIM = 8  # perturbation embedding features
_N_CLUSTERS = 5  # driver-gene programs (nonlinear cluster offsets)


_N_FOURIER = 6  # random-Fourier-feature count for the smooth nonlinear component


def _truth(seed: int, n_pert: int) -> dict[str, np.ndarray]:
    """Build the KNOWN ground truth: a modest linear map W + per-cluster sparse offsets +
    a SMOOTH NONLINEAR component (random Fourier features). The nonlinear part is smooth
    in embedding space -- a local method (kNN) captures it, a global linear ridge on the
    raw embedding underfits it -- which is what lets the complex backend legitimately beat
    the linear baseline in the informative regime (and only there)."""
    rng = np.random.default_rng(seed)
    W = rng.normal(0, 0.5, size=(_DIM, _EMB_DIM))  # modest linear part
    offsets = rng.normal(0, 1.5, size=(_N_CLUSTERS, _DIM))
    mask = rng.random((_N_CLUSTERS, _DIM)) < 0.25
    offsets = offsets * mask  # sparse per-cluster direct effects
    # Smooth nonlinear map: sin(emb @ F) @ V  (locally smooth, globally nonlinear).
    F = rng.normal(0, 0.6, size=(_EMB_DIM, _N_FOURIER))
    V = rng.normal(0, 2.2, size=(_N_FOURIER, _DIM))
    centers = rng.normal(0, 2.0, size=(_N_CLUSTERS, _EMB_DIM))
    cluster_of = rng.integers(0, _N_CLUSTERS, size=n_pert)
    true_emb = centers[cluster_of] + rng.normal(0, 0.35, size=(n_pert, _EMB_DIM))
    control = rng.normal(0, 0.2, size=_DIM)
    return {
        "W": W,
        "offsets": offsets,
        "F": F,
        "V": V,
        "cluster_of": cluster_of,
        "true_emb": true_emb,
        "control": control,
    }


def make_replay_dataset(
    *,
    seed: int = 27,
    n_pert: int = 60,
    regime: Regime = "informative",
    noise: float = 0.15,
    n_ood: int = 6,
) -> PerturbationDataset:
    """Generate a deterministic retrospective replay dataset.

    ``n_ood`` of the perturbations are placed in a NOVEL embedding region (a 6th cluster
    far from the training clusters) to exercise OOD abstention. Response deltas are the
    same physics in both regimes; only the *embedding the models see* changes with
    ``regime``.
    """
    t = _truth(seed, n_pert)
    rng = np.random.default_rng(seed + 1)
    W, offsets, F, V, cluster_of, true_emb, control = (
        t["W"], t["offsets"], t["F"], t["V"], t["cluster_of"], t["true_emb"], t["control"]
    )

    # OOD block: last n_ood perturbations get a far-away novel embedding cluster.
    ood_center = rng.normal(0, 2.0, size=_EMB_DIM) + 12.0
    if n_ood > 0:
        true_emb = true_emb.copy()
        true_emb[-n_ood:] = ood_center + rng.normal(0, 0.35, size=(n_ood, _EMB_DIM))

    # Ground-truth response = modest linear(W@e) + sparse cluster offset
    #                         + SMOOTH NONLINEAR sin(e@F)@V + noise.
    nonlinear = np.sin(true_emb @ F) @ V
    deltas = (
        (true_emb @ W.T)
        + offsets[cluster_of]
        + nonlinear
        + rng.normal(0, noise, size=(n_pert, _DIM))
    )

    # The embedding the MODELS receive.
    if regime == "informative":
        model_emb = true_emb
    elif regime == "scrambled":
        # Task-misaligned frozen embedding: random, uncorrelated with the truth.
        model_emb = rng.normal(0, 2.0, size=(n_pert, _EMB_DIM))
    else:  # pragma: no cover - guarded literal
        raise ValueError(f"unknown regime {regime!r}")

    axis_names = tuple(f"axis_{i:02d}" for i in range(_DIM))
    perts, resps = [], []
    ood_start = n_pert - n_ood
    for i in range(n_pert):
        # OOD perturbations get an OOD_ prefix so downstream eval can hold them out and
        # mark the abstention target consistently (honest bookkeeping, not truth peeking).
        pid = f"OOD_{i:03d}" if (n_ood > 0 and i >= ood_start) else f"KO_{i:03d}"
        perts.append(
            Perturbation(
                pert_id=pid,
                modality="gene_knockout",
                target=f"GENE_{i:03d}",
                embedding=model_emb[i],
            )
        )
        resps.append(ObservedResponse(pert_id=pid, delta=deltas[i], n_replicates=3))

    prov = DatasetProvenance(
        source="synthetic retrospective replay (modeled on single-gene Perturb-seq, K562-style)",
        scope=(
            f"SYNTHETIC; {_DIM}-axis cell-state; single-gene knockouts; "
            f"embedding-regime={regime}; seed={seed}; NOT real Perturb-seq numbers"
        ),
        validation_level="retrospective",
        is_wet_observation=False,
        notes=(
            "Deterministic fixture with known ground truth so the baseline-gate is "
            "provably discriminative. Benchmark/calibration ONLY -- never a wet "
            "observation of this run (charter §4)."
        ),
    )
    return PerturbationDataset(
        axis_names=axis_names,
        perturbations=tuple(perts),
        responses=tuple(resps),
        provenance=prov,
        control_state=control,
    )
