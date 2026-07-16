"""A PATHWAY-INFORMED response backend -- the 4th M27 competitor (bio_refs §3 competition
grid: ``mean/NN baseline · linear response · pathway-informed baseline · foundation ·
flow/diffusion · ensemble``). It enriches the model-competition grid with a *structured
biological prior*: cell-state axes belong to co-regulated **pathway modules**, so a
prediction can share statistical strength within a module (shrink each axis toward its
module's mean predicted effect).

WHY THIS IS AN HONEST, DISCRIMINATIVE COMPETITOR (charter §4):
  * When the pathway prior MATCHES the biology (axes in a module genuinely co-move, and the
    embedding carries signal), module shrinkage denoises the per-axis prediction and the
    backend can legitimately beat the plain linear baseline -> it clears the gate.
  * When the prior is WRONG or the embedding is task-misaligned (the scrambled / frozen-
    foundation-embedding regime), the linear base collapses to the mean and shrinking a
    near-constant prediction changes nothing -> it does NOT beat mean/linear -> the
    baseline-gate logs a first-class negative claim. Structure != free lunch, made legible.

The module assignment is a *design annotation*: either supplied explicitly (a public
pathway grouping; its provenance rides in the fingerprint) or discovered deterministically
from the training-response correlation structure. Either way it enters ``_weight_bytes``
so the fingerprint reflects which pathway prior was used. ``is_baseline = False`` -> it
must clear the baseline-gate to steer acquisition.
"""

from __future__ import annotations

import numpy as np

from expos.adapters.models.virtual_cell import (
    BioModelBackend,
    PerturbationBatch,
    ResponsePrediction,
)
from expos.adapters.models.virtual_cell_baselines import solve_y_axb


def _discover_modules(deltas: np.ndarray, n_modules: int, *, seed: int = 0) -> np.ndarray:
    """Deterministically group the G cell-state axes into ``n_modules`` co-regulation
    modules by k-means on their training-response correlation rows. Returns (G,) int.

    Axes that co-move across training perturbations (high |correlation|) land in the same
    module -- a data-driven stand-in for a pathway annotation when none is supplied."""
    G = deltas.shape[1]
    n_modules = int(max(1, min(n_modules, G)))
    # Correlation of each axis's response profile across training conditions.
    x = deltas - deltas.mean(axis=0, keepdims=True)
    denom = np.linalg.norm(x, axis=0, keepdims=True) + 1e-12
    xn = x / denom
    corr = xn.T @ xn  # (G, G), each row = an axis's correlation signature
    rng = np.random.default_rng(seed)
    # farthest-first init on correlation-signature rows, then 3 Lloyd iterations.
    centers = [int(rng.integers(0, G))]
    for _ in range(1, n_modules):
        d = np.min(
            np.stack([np.linalg.norm(corr - corr[c][None, :], axis=1) for c in centers]),
            axis=0,
        )
        centers.append(int(np.argmax(d)))
    cen = corr[centers].copy()  # (P, G)
    assign = np.zeros(G, dtype=int)
    for _ in range(3):
        d = np.linalg.norm(corr[:, None, :] - cen[None, :, :], axis=2)  # (G, P)
        assign = np.argmin(d, axis=1)
        for p in range(n_modules):
            m = assign == p
            if m.any():
                cen[p] = corr[m].mean(axis=0)
    return assign


class PathwayInformedBackend(BioModelBackend):
    """Linear-ridge base prediction + pathway-module shrinkage.

    ``pathway_of`` (G,) int optionally supplies an external pathway annotation (a public
    design grouping); if None, modules are discovered from training-response correlation.
    ``shrinkage`` in [0,1] blends each axis toward its module-mean predicted effect
    (0 = plain linear, 1 = fully module-averaged)."""

    name = "pathway_informed"
    version = "1"
    is_baseline = False

    def __init__(
        self,
        *,
        n_modules: int = 8,
        shrinkage: float = 0.5,
        ridge: float = 0.1,
        pathway_of: np.ndarray | None = None,
        pathway_source: str = "discovered:training_response_correlation",
        abstain_quantile: float = 0.90,
    ) -> None:
        super().__init__(abstain_quantile=abstain_quantile)
        self._n_modules = int(n_modules)
        self._shrinkage = float(shrinkage)
        self._ridge = float(ridge)
        self._pathway_of = None if pathway_of is None else np.asarray(pathway_of, dtype=int)
        self._pathway_source = pathway_source

    def _fit(self, batch: PerturbationBatch) -> None:
        assert batch.deltas is not None
        Y = batch.deltas.T  # (G, m)
        B = batch.embeddings.T  # (d, m)
        sol = solve_y_axb(Y, A=None, B=B, B_ridge=self._ridge)
        self._K = sol["K"]  # (G, d)
        self._center = sol["center"]  # (G,)
        G = batch.deltas.shape[1]
        if self._pathway_of is not None:
            if self._pathway_of.shape != (G,):
                raise ValueError(f"pathway_of must be ({G},); got {self._pathway_of.shape}")
            self._modules = self._pathway_of
        else:
            self._modules = _discover_modules(batch.deltas, self._n_modules)
        # Homoscedastic residual std from the SMOOTHED training fit (honest calibration:
        # if shrinkage helped, residuals shrink -> tighter intervals; if it hurt, wider).
        pred_train = np.stack(
            [self._smooth(self._center + self._K @ B[:, j]) for j in range(B.shape[1])]
        ).T  # (G, m)
        resid = Y - pred_train
        self._std = resid.std(axis=1) + 1e-6

    def _smooth(self, vec: np.ndarray) -> np.ndarray:
        """Shrink each axis toward its pathway-module mean."""
        out = vec.copy()
        for p in np.unique(self._modules):
            m = self._modules == p
            mod_mean = float(vec[m].mean())
            out[m] = (1.0 - self._shrinkage) * vec[m] + self._shrinkage * mod_mean
        return out

    def _predict_one(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        mean = self._smooth(self._center + self._K @ x)
        return ResponsePrediction(
            pert_id=pert_id, mean=mean, std=self._std.copy(),
            reason=f"pathway-informed (modules={len(np.unique(self._modules))}, "
            f"shrink={self._shrinkage}, src={self._pathway_source})",
        )

    def _weight_bytes(self) -> bytes:
        return (
            self._K.tobytes()
            + self._center.tobytes()
            + self._modules.tobytes()
            + self._pathway_source.encode()
        )
