"""The "more complex" M27 backend (charter v0.1 requires a third, non-baseline model):
a k-nearest-neighbour response model in perturbation-embedding space. It predicts the
response of a query perturbation as the distance-weighted average of its k nearest
training responses, with a genuine predictive *distribution* whose per-axis std comes
from neighbour disagreement (so the ``calibration`` face has real signal).

WHY kNN AND NOT A HALF-TRAINED NEURAL NET: v0.1 forbids large-model training (§1.5). A kNN
over a *given* embedding is the honest stand-in for a "frozen foundation model used as a
scorer": it is only as good as the embedding it is handed. When the embedding is aligned
with the response structure it can beat the baselines; when it is a scrambled / task-
misaligned frozen embedding (the regime the Nat. Methods benchmark exposes) it does NOT
beat mean/linear -- and the baseline-gate then logs a first-class negative claim. The
model is identical in both regimes; only the data regime differs. That is exactly the
point of the competition layer -- expensive != better, and we make the failure legible.

``is_baseline = False`` -> this backend MUST clear the baseline-gate to be admitted.
"""

from __future__ import annotations

import numpy as np

from expos.adapters.models.virtual_cell import (
    BioModelBackend,
    PerturbationBatch,
    ResponsePrediction,
    _pairwise_dist,
)


class KNNResponseBackend(BioModelBackend):
    """Distance-weighted kNN response predictor in embedding space.

    ``k`` neighbours, Gaussian distance weights with bandwidth = median training
    nearest-neighbour distance. Prediction mean = weighted average of neighbour deltas;
    std = weighted std across neighbours (+ a floor). Presents as an "expensive"
    proposer (foundation-style, embedding-driven) that must earn admission.
    """

    name = "knn_response"
    version = "1"
    is_baseline = False

    def __init__(self, *, k: int = 5, abstain_quantile: float = 0.90) -> None:
        super().__init__(abstain_quantile=abstain_quantile)
        self._k = int(k)

    def _fit(self, batch: PerturbationBatch) -> None:
        assert batch.deltas is not None
        self._emb = batch.embeddings.copy()  # (m, d)
        self._deltas = batch.deltas.copy()  # (m, G)
        d = _pairwise_dist(self._emb, self._emb)
        np.fill_diagonal(d, np.inf)
        self._bw = float(np.median(d.min(axis=1))) or 1.0
        self._global_std = batch.deltas.std(axis=0) + 1e-6
        # HONEST CALIBRATION: per-axis predictive std estimated from the model's OWN
        # leave-one-out residuals on the training set (not a hand-tuned floor). This is
        # what makes the calibration face meaningful: in the informative regime the LOO
        # residuals are small (kNN predicts well -> tight, well-covered intervals); in the
        # scrambled regime they are large (kNN cannot predict -> honestly wide intervals).
        resid = np.stack(
            [self._deltas[i] - self._knn_mean(self._emb[i], exclude=i) for i in range(len(self._emb))]
        )
        self._cal_std = resid.std(axis=0) + 1e-6  # (G,)

    def _knn_mean(self, x: np.ndarray, *, exclude: int | None = None) -> np.ndarray:
        dists = np.linalg.norm(self._emb - x[None, :], axis=1)
        if exclude is not None:
            dists = dists.copy()
            dists[exclude] = np.inf
        k = min(self._k, np.isfinite(dists).sum())
        idx = np.argsort(dists)[:k]
        w = np.exp(-((dists[idx] / self._bw) ** 2))
        w = np.ones_like(w) if w.sum() <= 1e-12 else w / w.sum()
        return (w[:, None] * self._deltas[idx]).sum(axis=0)

    def _predict_one(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        dists = np.linalg.norm(self._emb - x[None, :], axis=1)
        k = min(self._k, len(dists))
        idx = np.argsort(dists)[:k]
        w = np.exp(-((dists[idx] / self._bw) ** 2))
        if w.sum() <= 1e-12:
            w = np.ones_like(w)
        w = w / w.sum()
        nb = self._deltas[idx]  # (k, G)
        mean = (w[:, None] * nb).sum(axis=0)
        nb_var = (w[:, None] * (nb - mean[None, :]) ** 2).sum(axis=0)
        # Predictive std = local neighbour spread combined with the LOO calibration std.
        std = np.sqrt(nb_var + self._cal_std**2)
        return ResponsePrediction(
            pert_id=pert_id, mean=mean, std=std,
            reason=f"knn k={k} bw={self._bw:.3g}",
        )

    def _weight_bytes(self) -> bytes:
        return self._emb.tobytes() + self._deltas.tobytes()
