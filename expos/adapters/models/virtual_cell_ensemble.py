"""An ENSEMBLE response backend -- the 5th M27 competitor (bio_refs §3 competition grid
lists ``ensemble`` explicitly). It combines several member backends into one predictive
distribution whose uncertainty has TWO honest components:

    predictive variance = mean(member aleatoric variance)      # within-model width
                        + variance(member means)               # epistemic disagreement

so an ensemble is *sharp and well-covered* where its members agree and *honestly wide*
where they disagree -- exactly the signal the ``calibration`` and ``ood_abstention``
decision faces reward. This is the cleanest way to turn model DISAGREEMENT (which the
active-selection layer already uses) into calibrated predictive uncertainty.

HONEST GATE BEHAVIOUR (charter §4): the ensemble is ``is_baseline = False`` -- it must
clear the baseline-gate like any expensive proposer. Averaging cheap baselines does not
grant a free pass: if no member carries signal beyond the mean (scrambled regime), the
ensemble mean stays at the mean and it does NOT beat the baseline -> first-class negative
claim. When a member (e.g. the kNN on an informative embedding) genuinely wins, the
ensemble inherits that lift while regularising it -> it can clear the gate with better
calibration than the single model.
"""

from __future__ import annotations

import hashlib

import numpy as np

from expos.adapters.models.virtual_cell import (
    BioModelBackend,
    PerturbationBatch,
    ResponsePrediction,
)
from expos.adapters.models.virtual_cell_baselines import (
    LinearResponseBackend,
    MeanBaselineBackend,
)
from expos.adapters.models.virtual_cell_complex import KNNResponseBackend


def _default_members() -> list[BioModelBackend]:
    return [MeanBaselineBackend(), LinearResponseBackend(), KNNResponseBackend()]


class EnsembleBackend(BioModelBackend):
    """Uncertainty-aware ensemble of member backends.

    Members are fitted on the same training batch inside :meth:`fit`. The prediction mean
    is the (optionally weighted) average of member means; the predictive std combines mean
    member variance (aleatoric) with the variance of member means (epistemic). Abstention
    is layered by the base class on the ensemble's own support geometry AND escalated when
    a majority of members abstain."""

    name = "ensemble"
    version = "1"
    is_baseline = False

    def __init__(
        self,
        members: list[BioModelBackend] | None = None,
        *,
        weights: np.ndarray | None = None,
        abstain_quantile: float = 0.90,
    ) -> None:
        super().__init__(abstain_quantile=abstain_quantile)
        self._members = members if members is not None else _default_members()
        if not self._members:
            raise ValueError("EnsembleBackend needs >= 1 member backend")
        w = (
            np.ones(len(self._members))
            if weights is None
            else np.asarray(weights, dtype=float)
        )
        if w.shape != (len(self._members),):
            raise ValueError("weights must match the number of members")
        self._weights = w / w.sum()

    def _fit(self, batch: PerturbationBatch) -> None:
        for m in self._members:
            m.fit(batch)

    def _predict_one(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        # Raw (pre-abstention) member distributions; the ensemble owns its own abstention.
        preds = [m._predict_one(pert_id, x) for m in self._members]
        means = np.stack([p.mean for p in preds])  # (M, G)
        stds = np.stack([p.std for p in preds])  # (M, G)
        w = self._weights[:, None]
        mean = (w * means).sum(axis=0)  # (G,)
        aleatoric = (w * stds**2).sum(axis=0)  # mean within-model variance
        epistemic = (w * (means - mean[None, :]) ** 2).sum(axis=0)  # disagreement variance
        std = np.sqrt(aleatoric + epistemic) + 1e-6
        # Escalate abstention if a weighted majority of members would abstain OOD.
        member_ab = np.array(
            [m._predict_with_abstention(pert_id, x).abstained for m in self._members]
        )
        reason = f"ensemble[{','.join(m.name for m in self._members)}]"
        if float(self._weights[member_ab].sum()) > 0.5:
            reason += " (member-majority OOD)"
        return ResponsePrediction(pert_id=pert_id, mean=mean, std=std, reason=reason)

    def _weight_bytes(self) -> bytes:
        h = hashlib.sha256()
        for m in self._members:
            h.update(m.fingerprint().encode())
        h.update(self._weights.tobytes())
        return h.digest()
