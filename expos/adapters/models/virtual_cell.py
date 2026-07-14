"""``BioModelBackend`` contract + shared numpy carriers for M27 (perturbation biology /
virtual cell), the model-competition layer of ``docs/BIOLOGY_PROGRAM_2026.md`` §3.

WHY THIS LIVES IN THE ADAPTER LAYER (charter §4): biological semantics may live in the
domain / provider / adapter / QC layers -- and a *virtual-cell model* is an adapter-layer
scorer. The kernel / planner / evidence-compiler / knowledge-compiler stay biology-blind;
they never import this module. A backend here is a **proposer/scorer ONLY**: it emits
``ResponsePrediction`` (dry evidence / proposal), it NEVER writes a claim. Only a trusted
observation certifies (charter §4). This module therefore imports no kernel/ledger/claim
symbol.

Every ``BioModelBackend`` must (charter §3, the five obligations):
  1. put its version + fitted-weight hash into a provenance fingerprint
     (:meth:`BioModelBackend.fingerprint`);
  2. have a baseline to compete against (enforced by the competition layer, not here);
  3. report calibration (predict a *distribution*: ``mean`` + per-axis ``std``);
  4. support abstention (``ResponsePrediction.abstained`` on out-of-distribution input);
  5. never modify a claim (it returns predictions; certification is elsewhere).

The prediction target is a **response distribution, not a point** (bio_refs §1.4 /
PerturbDiff finding): a backend returns ``mean`` + ``std`` per cell-state axis so the
competition layer's ``calibration`` and ``OOD/abstention`` faces have something to score.
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------- carriers


@dataclass(frozen=True)
class PerturbationBatch:
    """A numpy-level bundle of perturbations the backend fits on / predicts for.

    Deliberately plain arrays (not the typed domain objects in
    ``domains.perturbation.objects``) so the adapter layer has no dependency on the
    domain package -- the domain builds a batch via ``PerturbationDataset.to_batch()``.

    Fields:
      * ``pert_ids``   -- (n,) stable ids of each perturbation (e.g. knocked-out gene).
      * ``embeddings`` -- (n, d) perturbation feature/embedding matrix. For a *frozen
        foundation* backend this is the model's precomputed embedding; for the linear
        baseline it is the design embedding. Its provenance/version rides in the
        dataset fingerprint, never re-learned here.
      * ``deltas``     -- (n, G) observed cell-state delta-from-control (the response),
        or ``None`` for a query-only batch (prediction targets unknown).
      * ``ood_mask``   -- (n,) optional bool: True where the perturbation is *known* to
        be out-of-distribution relative to some reference (eval bookkeeping only; a
        backend never reads truth -- it decides abstention from geometry, see below).
    """

    pert_ids: tuple[str, ...]
    embeddings: np.ndarray
    deltas: np.ndarray | None = None
    ood_mask: np.ndarray | None = None

    def __post_init__(self) -> None:
        emb = np.asarray(self.embeddings, dtype=float)
        object.__setattr__(self, "embeddings", emb)
        if emb.ndim != 2 or emb.shape[0] != len(self.pert_ids):
            raise ValueError(
                f"embeddings must be (n_pert={len(self.pert_ids)}, d); got {emb.shape}"
            )
        if self.deltas is not None:
            d = np.asarray(self.deltas, dtype=float)
            object.__setattr__(self, "deltas", d)
            if d.ndim != 2 or d.shape[0] != len(self.pert_ids):
                raise ValueError(
                    f"deltas must be (n_pert={len(self.pert_ids)}, G); got {d.shape}"
                )

    @property
    def n(self) -> int:
        return len(self.pert_ids)


@dataclass(frozen=True)
class ResponsePrediction:
    """A backend's prediction for ONE perturbation: a distribution over the cell-state
    delta, plus an honest abstention flag. This is dry evidence / a proposal -- it does
    NOT certify anything.

      * ``mean``           -- (G,) predicted delta-from-control per cell-state axis.
      * ``std``            -- (G,) predictive standard deviation per axis (the
        distribution width that feeds the ``calibration`` decision face).
      * ``abstained``      -- True when the backend declines to predict (OOD / low
        confidence). An honest abstention is a FIRST-CLASS outcome (charter §4): the
        decision layer rewards it over a confident-wrong prediction.
      * ``in_distribution``-- [0,1] geometric confidence that this perturbation is inside
        the fitted support (1 = a training-adjacent point, 0 = far OOD).
      * ``reason``         -- short human string (why abstained / model note).
    """

    pert_id: str
    mean: np.ndarray
    std: np.ndarray
    abstained: bool = False
    in_distribution: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        m = np.asarray(self.mean, dtype=float)
        s = np.asarray(self.std, dtype=float)
        object.__setattr__(self, "mean", m)
        object.__setattr__(self, "std", s)
        if m.shape != s.shape or m.ndim != 1:
            raise ValueError(f"mean/std must be equal 1-D shapes; got {m.shape}/{s.shape}")


# --------------------------------------------------------------------------- contract


class BioModelBackend(abc.ABC):
    """One replaceable virtual-cell scorer. Fit on a training :class:`PerturbationBatch`,
    then predict a response *distribution* for held-out / query perturbations.

    Subclasses set ``name`` + ``version`` and implement :meth:`_fit` and
    :meth:`_predict_one`. The base class owns the shared machinery every backend must
    share so the competition is fair: (a) a geometry-based in-distribution score +
    abstention gate (charter obligation #4), and (b) the weight-hash fingerprint
    (obligation #1). A backend that overrides abstention must still route through
    :meth:`_in_distribution` so the OOD face compares like with like.
    """

    #: Human id (competition ledger key). Subclasses set it.
    name: str = "<unnamed-backend>"
    #: Version string; enters the fingerprint. Bump on any behaviour change.
    version: str = "0"
    #: Whether this backend counts as an *expensive* proposer (foundation/flow/diffusion)
    #: that must clear the baseline-gate. Baselines set this False.
    is_baseline: bool = False

    def __init__(self, *, abstain_quantile: float = 0.90) -> None:
        # A query is flagged OOD when its nearest-training distance exceeds the
        # ``abstain_quantile`` of the training set's own nearest-neighbour distances.
        # Geometric only -- never reads truth (charter red-line).
        self._abstain_quantile = float(abstain_quantile)
        self._train_emb: np.ndarray | None = None
        self._nn_scale: float = 1.0
        self._ood_radius: float = np.inf
        self._fitted = False

    # -- public API ---------------------------------------------------------

    def fit(self, batch: PerturbationBatch) -> "BioModelBackend":
        if batch.deltas is None:
            raise ValueError(f"{self.name}: fit() needs a batch with observed deltas")
        self._train_emb = batch.embeddings.copy()
        self._fit_support(self._train_emb)
        self._fit(batch)
        self._fitted = True
        return self

    def predict(self, batch: PerturbationBatch) -> list[ResponsePrediction]:
        if not self._fitted:
            raise RuntimeError(f"{self.name}: predict() before fit()")
        return [
            self._predict_with_abstention(batch.pert_ids[i], batch.embeddings[i])
            for i in range(batch.n)
        ]

    def fingerprint(self) -> str:
        """Provenance token = ``name@version#sha256:<weight-hash>`` (charter obligation
        #1). The weight hash covers the fitted parameters so a re-fit on different data
        (or a code change that shifts the weights) flips the fingerprint."""
        return f"{self.name}@{self.version}#sha256:{self._weight_hash()}"

    # -- shared abstention / support geometry -------------------------------

    def _fit_support(self, emb: np.ndarray) -> None:
        if emb.shape[0] < 2:
            self._nn_scale, self._ood_radius = 1.0, np.inf
            return
        d = _pairwise_dist(emb, emb)
        np.fill_diagonal(d, np.inf)
        nn = d.min(axis=1)
        self._nn_scale = float(np.median(nn)) or 1.0
        self._ood_radius = float(np.quantile(nn, self._abstain_quantile))

    def _in_distribution(self, x: np.ndarray) -> float:
        if self._train_emb is None or self._train_emb.shape[0] == 0:
            return 1.0
        dmin = float(np.min(np.linalg.norm(self._train_emb - x[None, :], axis=1)))
        # squashed distance -> [0,1]; 1 near training, decays with distance.
        return float(np.exp(-dmin / (self._nn_scale + 1e-12)))

    def _predict_with_abstention(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        conf = self._in_distribution(x)
        dmin = (
            float(np.min(np.linalg.norm(self._train_emb - x[None, :], axis=1)))
            if self._train_emb is not None
            else 0.0
        )
        pred = self._predict_one(pert_id, x)
        if dmin > self._ood_radius:
            # OOD: return the model mean but abstain honestly + inflate uncertainty.
            return ResponsePrediction(
                pert_id=pert_id,
                mean=pred.mean,
                std=pred.std * 2.0 + 1e-6,
                abstained=True,
                in_distribution=conf,
                reason=f"OOD: nn-dist {dmin:.3g} > radius {self._ood_radius:.3g}",
            )
        return ResponsePrediction(
            pert_id=pert_id,
            mean=pred.mean,
            std=pred.std,
            abstained=False,
            in_distribution=conf,
            reason=pred.reason,
        )

    # -- subclass hooks -----------------------------------------------------

    @abc.abstractmethod
    def _fit(self, batch: PerturbationBatch) -> None:
        """Fit model parameters from ``batch`` (which has observed deltas)."""

    @abc.abstractmethod
    def _predict_one(self, pert_id: str, x: np.ndarray) -> ResponsePrediction:
        """Predict the response distribution for one embedding ``x`` (d,). Return a
        prediction with ``mean`` + ``std``; the base class layers abstention on top."""

    @abc.abstractmethod
    def _weight_bytes(self) -> bytes:
        """Serialize the fitted weights for the fingerprint hash."""

    def _weight_hash(self) -> str:
        if not self._fitted:
            return "unfitted"
        return hashlib.sha256(self._weight_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------- helpers


def _pairwise_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix (|a| x |b|)."""
    return np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
