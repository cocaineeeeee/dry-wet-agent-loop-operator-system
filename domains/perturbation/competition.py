"""M27 model-competition + decision layer with the BASELINE-GATE as a hard gate
(bio_refs §1.4 / §6 #1): "跑赢 baseline" is a gate, not a leaderboard row. An expensive
proposer (foundation/flow/diffusion, here ``KNNResponseBackend``) must *significantly and
calibratedly* beat BOTH mandatory baselines (mean + linear-ridge) on the SAME held-out
split, or its "did-not-clear" is emitted as a FIRST-CLASS negative claim -- and its
proposals are NOT admitted to acquisition.

Five decision faces (charter §3): ``performance`` · ``calibration`` ·
``biological_fidelity`` · ``ood_abstention`` · ``experiment_cost``. A backend PROPOSES;
this layer scores proposals and decides admission. It NEVER certifies a biological claim
(that needs a trusted observation, causal.py) -- it decides which *models* may steer the
next experiment selection.

Honesty (charter §4): abstention is a first-class, REWARDED action (a model that honestly
abstains on OOD scores better on the ``ood_abstention`` face than one that is confidently
wrong). The gate can and should return NEGATIVE -- that is knowledge, not failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from expos.adapters.models.virtual_cell import (
    BioModelBackend,
    PerturbationBatch,
    ResponsePrediction,
)

_TOPK_DE = 8  # top-k most-moved axes for the DE-overlap fidelity face
#: nominal coverage of the 1-sigma predictive interval (calibration target).
_NOMINAL_COVERAGE = 0.6827
#: rough compute-cost proxy per backend name (experiment_cost face; higher = costlier).
_COST_PROXY = {"mean_baseline": 1.0, "linear_response": 2.0, "knn_response": 8.0}


@dataclass(frozen=True)
class FaceScores:
    """The five decision-face scores for one backend on one held-out split."""

    backend: str
    is_baseline: bool
    # performance
    l2_mean: float  # mean per-perturbation L2 (lower better)
    pearson_delta: float  # mean per-perturbation Pearson of predicted vs true delta
    l2_per_pert: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    # calibration
    coverage_68: float = 0.0  # empirical coverage of the 1-sigma interval
    calibration_error: float = 0.0  # |coverage - nominal|
    # biological fidelity
    de_overlap: float = 0.0  # top-k moved-axis overlap (with sign)
    # OOD / abstention
    ood_abstain_rate: float = 0.0  # fraction of OOD points abstained (want high)
    id_abstain_rate: float = 0.0  # fraction of in-dist points abstained (want low)
    ood_score: float = 0.0  # ood_abstain_rate - id_abstain_rate  (in [-1,1])
    # experiment cost
    cost: float = 1.0
    n_eval: int = 0


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / denom) if denom > 1e-12 else 0.0


def score_backend(
    backend: BioModelBackend,
    held_out: PerturbationBatch,
) -> FaceScores:
    """Compute the five decision faces for a fitted ``backend`` on a held-out batch.

    ``held_out.deltas`` are the (retrospective, non-wet) reference responses; ``ood_mask``
    (if present) marks which held-out perturbations are truly OOD. Performance /
    calibration / fidelity are scored on the IN-DISTRIBUTION, non-abstained points (a
    model is not penalised on performance for honestly abstaining -- that is the OOD
    face's job). The OOD face scores abstention behaviour on the whole split.
    """
    assert held_out.deltas is not None, "held_out must carry reference deltas"
    preds: list[ResponsePrediction] = backend.predict(held_out)
    truth = held_out.deltas
    ood_mask = (
        held_out.ood_mask
        if held_out.ood_mask is not None
        else np.zeros(held_out.n, dtype=bool)
    )

    l2s, pears, covers = [], [], []
    for i, p in enumerate(preds):
        if ood_mask[i] or p.abstained:
            continue
        err = p.mean - truth[i]
        l2s.append(float(np.linalg.norm(err)))
        pears.append(_pearson(p.mean, truth[i]))
        covers.append(float(np.mean(np.abs(err) <= p.std)))

    # DE-overlap fidelity: top-k moved axes (signed) on in-dist non-abstained points.
    overlaps = []
    for i, p in enumerate(preds):
        if ood_mask[i] or p.abstained:
            continue
        t_top = set(np.argsort(-np.abs(truth[i]))[:_TOPK_DE])
        p_top = set(np.argsort(-np.abs(p.mean))[:_TOPK_DE])
        inter = t_top & p_top
        sign_ok = sum(
            1 for a in inter if np.sign(truth[i][a]) == np.sign(p.mean[a])
        )
        overlaps.append(sign_ok / _TOPK_DE)

    # OOD / abstention behaviour over the whole split.
    ood_idx = np.where(ood_mask)[0]
    id_idx = np.where(~ood_mask)[0]
    ood_ab = float(np.mean([preds[i].abstained for i in ood_idx])) if len(ood_idx) else 0.0
    id_ab = float(np.mean([preds[i].abstained for i in id_idx])) if len(id_idx) else 0.0

    l2_arr = np.asarray(l2s)
    return FaceScores(
        backend=backend.name,
        is_baseline=backend.is_baseline,
        l2_mean=float(l2_arr.mean()) if len(l2_arr) else float("inf"),
        pearson_delta=float(np.mean(pears)) if pears else 0.0,
        l2_per_pert=l2_arr,
        coverage_68=float(np.mean(covers)) if covers else 0.0,
        calibration_error=abs((float(np.mean(covers)) if covers else 0.0) - _NOMINAL_COVERAGE),
        de_overlap=float(np.mean(overlaps)) if overlaps else 0.0,
        ood_abstain_rate=ood_ab,
        id_abstain_rate=id_ab,
        ood_score=ood_ab - id_ab,
        cost=_COST_PROXY.get(backend.name, 5.0),
        n_eval=len(l2s),
    )


@dataclass(frozen=True)
class GateVerdict:
    """The baseline-gate outcome for ONE expensive (non-baseline) backend."""

    backend: str
    admitted: bool
    beat_baseline: str  # the best baseline it was measured against
    l2_improvement: float  # best_baseline_l2 - candidate_l2 (positive = better)
    ci_low: float  # bootstrap 95% lower bound on the paired improvement
    calibration_ok: bool
    reason: str
    negative_claim: dict | None = None  # populated iff NOT admitted


def _paired_bootstrap_ci_low(
    baseline_l2: np.ndarray, cand_l2: np.ndarray, *, n_boot: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """Paired bootstrap 95% CI lower bound on the mean per-perturbation improvement
    (baseline_l2 - cand_l2). Positive lower bound => significantly better."""
    imp = baseline_l2 - cand_l2
    if len(imp) == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    boots = np.array(
        [imp[rng.integers(0, len(imp), len(imp))].mean() for _ in range(n_boot)]
    )
    return float(imp.mean()), float(np.quantile(boots, 0.025))


def baseline_gate(
    scores: dict[str, FaceScores],
    *,
    min_improvement: float = 0.05,
    calibration_slack: float = 0.15,
) -> list[GateVerdict]:
    """Apply the hard baseline-gate to every non-baseline backend in ``scores``.

    A candidate is ADMITTED iff, against the STRONGER baseline (lower L2), it:
      (a) has a positive mean per-perturbation L2 improvement above ``min_improvement``;
      (b) that improvement's paired-bootstrap 95% lower bound is > 0 (significant); AND
      (c) its calibration error is not worse than the best baseline's by more than
          ``calibration_slack`` ("calibratedly beat", not just lower L2).
    Otherwise a first-class NEGATIVE claim is emitted (proposals NOT admitted).
    """
    baselines = [s for s in scores.values() if s.is_baseline]
    if not baselines:
        raise ValueError("baseline_gate: no baseline backends present (need mean+linear)")
    best_base = min(baselines, key=lambda s: s.l2_mean)
    best_base_cal = min(b.calibration_error for b in baselines)

    verdicts: list[GateVerdict] = []
    for s in scores.values():
        if s.is_baseline:
            continue
        # Align per-perturbation L2 arrays (same eval order/points).
        n = min(len(best_base.l2_per_pert), len(s.l2_per_pert))
        imp_mean, ci_low = _paired_bootstrap_ci_low(
            best_base.l2_per_pert[:n], s.l2_per_pert[:n]
        )
        cal_ok = s.calibration_error <= best_base_cal + calibration_slack
        admitted = (imp_mean > min_improvement) and (ci_low > 0.0) and cal_ok
        if admitted:
            verdicts.append(
                GateVerdict(
                    backend=s.backend, admitted=True, beat_baseline=best_base.backend,
                    l2_improvement=imp_mean, ci_low=ci_low, calibration_ok=cal_ok,
                    reason=(
                        f"cleared gate: beat {best_base.backend} by L2 {imp_mean:.3f} "
                        f"(CI-low {ci_low:.3f}>0), calibration ok"
                    ),
                )
            )
        else:
            why = []
            if imp_mean <= min_improvement:
                why.append(f"L2 improvement {imp_mean:.3f} <= {min_improvement}")
            if ci_low <= 0.0:
                why.append(f"not significant (CI-low {ci_low:.3f} <= 0)")
            if not cal_ok:
                why.append(
                    f"calibration worse (err {s.calibration_error:.3f} > "
                    f"{best_base_cal:.3f}+{calibration_slack})"
                )
            reason = "did NOT clear baseline-gate: " + "; ".join(why)
            verdicts.append(
                GateVerdict(
                    backend=s.backend, admitted=False, beat_baseline=best_base.backend,
                    l2_improvement=imp_mean, ci_low=ci_low, calibration_ok=cal_ok,
                    reason=reason,
                    negative_claim={
                        "claim_id": f"m27_gate_{s.backend}_not_over_baseline",
                        "status": "rejected",
                        "statement": (
                            f"virtual-cell backend '{s.backend}' did not significantly and "
                            f"calibratedly beat baseline '{best_base.backend}' on the "
                            f"held-out split"
                        ),
                        "kind": "baseline_gate_negative",
                        "evidence": reason,
                    },
                )
            )
    return verdicts
