"""The two MANDATORY cheap reference lines of the M27 competition (bio_refs §1.3 ADOPT):
``mean`` and ``linear-ridge``. Any expensive proposer (foundation / flow / diffusion,
e.g. ``virtual_cell_complex``) must significantly and calibratedly beat BOTH of these on
the same held-out split or its "did-not-clear" is logged as a first-class negative claim
(the baseline-gate, competition.py). These two are ``is_baseline = True``.

Provenance of the maths: the ``linear-ridge`` backend is a faithful numpy port of the
two-sided ridge decomposition ``solve_y_axb`` from
``references/linear_perturbation_prediction-Paper/benchmark/src/run_linear_pretrained_model.R``
(Ahlmann-Eltze, Huber & Anders, *Nat. Methods* 22:1657-1661, 2025 -- the "deep learning
has not beaten a linear baseline" paper). We use the ``A = None`` branch (no separate gene
embedding): ``K = Y_centered @ Bᵀ @ (B @ Bᵀ + λI)⁻¹`` and predict ``center + K @ b``.
"""

from __future__ import annotations

import numpy as np

from expos.adapters.models.virtual_cell import (
    BioModelBackend,
    PerturbationBatch,
    ResponsePrediction,
)


def solve_y_axb(
    Y: np.ndarray,
    A: np.ndarray | None = None,
    B: np.ndarray | None = None,
    A_ridge: float = 0.01,
    B_ridge: float = 0.01,
) -> dict[str, np.ndarray]:
    """Numpy port of the reference ``solve_y_axb`` (see module docstring for source).

    ``Y`` is (G, m): rows = cell-state axes (genes), cols = training conditions. ``A`` is
    the optional (G, k) gene embedding; ``B`` is the optional (d, m) perturbation
    embedding (features x conditions). Returns ``{"K": K, "center": center}``.

    Both-sided:  ``K = (AᵀA+λI)⁻¹ Aᵀ Y Bᵀ (BBᵀ+λI)⁻¹``   (k x d)
    A is None:   ``K = Y Bᵀ (BBᵀ+λI)⁻¹``                    (G x d)
    B is None:   ``K = (AᵀA+λI)⁻¹ Aᵀ Y``                    (k x m)
    """
    center = Y.mean(axis=1)
    Yc = Y - center[:, None]
    if A is not None and B is not None:
        ka = A.T @ A + np.eye(A.shape[1]) * A_ridge
        kb = B @ B.T + np.eye(B.shape[0]) * B_ridge
        K = np.linalg.solve(ka, A.T) @ Yc @ B.T @ np.linalg.inv(kb)
    elif B is None:
        if A is None:
            raise ValueError("either A or B must be non-null")
        ka = A.T @ A + np.eye(A.shape[1]) * A_ridge
        K = np.linalg.solve(ka, A.T) @ Yc
    else:  # A is None
        kb = B @ B.T + np.eye(B.shape[0]) * B_ridge
        K = Yc @ B.T @ np.linalg.inv(kb)
    K = np.nan_to_num(K)
    return {"K": K, "center": center}


class MeanBaselineBackend(BioModelBackend):
    """The "mean" baseline (bio_refs §1.1 #2): predict = the average response over all
    training perturbations, ignoring the query entirely. Deliberately the cheapest,
    hardest-to-beat-honestly reference. Uncertainty = per-axis training spread."""

    name = "mean_baseline"
    version = "1"
    is_baseline = True

    def _fit(self, batch: PerturbationBatch) -> None:
        assert batch.deltas is not None
        self._mean = batch.deltas.mean(axis=0)
        self._std = batch.deltas.std(axis=0) + 1e-6

    def _predict_one(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        return ResponsePrediction(
            pert_id=pert_id, mean=self._mean.copy(), std=self._std.copy(),
            reason="mean-of-training",
        )

    def _weight_bytes(self) -> bytes:
        return self._mean.tobytes() + self._std.tobytes()


class LinearResponseBackend(BioModelBackend):
    """The linear-ridge baseline: ridge regression of the cell-state delta on the
    perturbation embedding via :func:`solve_y_axb` (A=None branch). Predict
    ``center + K @ b``. Residual spread sets the (homoscedastic) predictive std."""

    name = "linear_response"
    version = "1"
    is_baseline = True

    def __init__(self, *, ridge: float = 0.1, abstain_quantile: float = 0.90) -> None:
        super().__init__(abstain_quantile=abstain_quantile)
        self._ridge = float(ridge)

    def _fit(self, batch: PerturbationBatch) -> None:
        assert batch.deltas is not None
        Y = batch.deltas.T  # (G, m)
        B = batch.embeddings.T  # (d, m)
        sol = solve_y_axb(Y, A=None, B=B, B_ridge=self._ridge)
        self._K = sol["K"]  # (G, d)
        self._center = sol["center"]  # (G,)
        # Homoscedastic residual std from training fit quality.
        pred_train = self._center[:, None] + self._K @ B  # (G, m)
        resid = Y - pred_train
        self._std = resid.std(axis=1) + 1e-6  # (G,)

    def _predict_one(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        mean = self._center + self._K @ x
        return ResponsePrediction(
            pert_id=pert_id, mean=mean, std=self._std.copy(),
            reason="linear-ridge solve_y_axb",
        )

    def _weight_bytes(self) -> bytes:
        return self._K.tobytes() + self._center.tobytes()
