"""M27 v0.1 END-TO-END runnable (charter DoD #2 + #8: one runnable end-to-end workflow +
a machine-derived report).

    cell-state + perturbation
      -> three competing backends predict a response DISTRIBUTION (dry evidence)
      -> five decision faces scored on a held-out split
      -> BASELINE-GATE (admit / first-class negative claim)
      -> active selection of the next perturbation (admitted models vote)
      -> trusted (retrospective, non-wet) observation certifies causal claims
      -> changed knowledge re-orders the next selection

Run BOTH data regimes so the gate is shown to be discriminative:
  * informative embedding -> complex backend CLEARS the gate (positive face)
  * scrambled  embedding -> complex backend does NOT -> negative claim (flat/negative face)

Everything runs DOMAIN-LOCALLY (no mcl needed). Certifying through the real expos trusted-
observation path is the seam to B (docs/bio_seams/M27.md); the causal step here is honestly
labeled retrospective/non-wet.

Usage:  python -m domains.perturbation.run_v01
"""

from __future__ import annotations

import json

import numpy as np

from datasets.replay.synthetic_perturbseq import make_replay_dataset
from domains.perturbation.causal import (
    certified_axis_index,
    certify_causal_claims,
)
from domains.perturbation.competition import baseline_gate, score_backend
from domains.perturbation.objects import PerturbationDataset
from domains.perturbation.selection import select_next_perturbation
from expos.adapters.models import (
    KNNResponseBackend,
    LinearResponseBackend,
    MeanBaselineBackend,
)
from expos.adapters.models.virtual_cell import PerturbationBatch


def _split(dataset: PerturbationDataset, holdout_frac: float = 0.3, seed: int = 0):
    ids = [p.pert_id for p in dataset.perturbations]
    ood_ids = [i for i in ids if i.startswith("OOD_")]
    in_ids = [i for i in ids if not i.startswith("OOD_")]
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(in_ids))
    n_hold = max(4, int(len(in_ids) * holdout_frac))
    hold = {in_ids[perm[k]] for k in range(n_hold)}
    train_ids = [i for i in in_ids if i not in hold]  # OOD never enters training
    held_ids = [i for i in in_ids if i in hold] + ood_ids  # OOD always held out
    return dataset.subset(train_ids), dataset.subset(held_ids)


def _held_batch_with_ood(dataset: PerturbationDataset):
    """Build a held-out batch; the abstention target = the injected OOD perturbations
    (OOD_ prefix). This is honest bookkeeping of what we deliberately placed out of
    distribution -- the BACKENDS still decide abstention from embedding geometry alone
    (they never see this mask)."""
    batch = dataset.to_batch()
    ood = np.array([pid.startswith("OOD_") for pid in batch.pert_ids])
    return PerturbationBatch(
        pert_ids=batch.pert_ids, embeddings=batch.embeddings,
        deltas=batch.deltas, ood_mask=ood,
    )


def run_regime(regime: str, seed: int = 27) -> dict:
    dataset = make_replay_dataset(seed=seed, n_pert=60, regime=regime, n_ood=6)
    train, held = _split(dataset, seed=1)
    train_batch = train.to_batch()
    held_batch = _held_batch_with_ood(held)

    backends = [
        MeanBaselineBackend().fit(train_batch),
        LinearResponseBackend().fit(train_batch),
        KNNResponseBackend().fit(train_batch),
    ]
    scores = {b.name: score_backend(b, held_batch) for b in backends}
    verdicts = baseline_gate(scores)

    admitted = [b for b in backends if b.name in {v.backend for v in verdicts if v.admitted}]
    # Baselines are always available voters; add admitted complex backends.
    voters = [b for b in backends if b.is_baseline] + admitted

    # Active selection over a candidate pool (the held-out perts as un-run candidates).
    ranking = select_next_perturbation(voters, held_batch)
    pre_pick = ranking[0].pert_id

    # Trusted (retrospective, non-wet) observation certifies causal claims (DoD #5/#6).
    proposals = {b.name: b.predict(held_batch) for b in backends}
    claims = certify_causal_claims(held, effect_threshold=6.0, proposals=proposals)

    # ACTIVE-LOOP STEP (DoD #7 -- changed knowledge alters a later decision): we now
    # "observe" the just-selected perturbation; its certified causal claims become
    # knowledge that marks its informative axes as already-learned, so re-running
    # selection pushes the next pick OFF the now-redundant perturbation.
    cert_idx = certified_axis_index(
        [c for c in claims if c.pert_id == pre_pick], held.axis_names
    )
    ranking_after = select_next_perturbation(
        voters, held_batch, certified_axes=cert_idx, redundancy_weight=1.0
    )
    post_pick = ranking_after[0].pert_id

    n_supported = sum(1 for c in claims if c.status == "supported")
    return {
        "regime": regime,
        "dataset_fingerprint": dataset.fingerprint(),
        "dataset_provenance": {
            "source": dataset.provenance.source,
            "scope": dataset.provenance.scope,
            "validation_level": dataset.provenance.validation_level,
            "is_wet_observation": dataset.provenance.is_wet_observation,
            "fingerprint": dataset.provenance.fingerprint(),
        },
        "backends": {
            name: {
                "fingerprint": b.fingerprint(),
                "l2_mean": round(scores[name].l2_mean, 4),
                "pearson_delta": round(scores[name].pearson_delta, 4),
                "calibration_error": round(scores[name].calibration_error, 4),
                "de_overlap": round(scores[name].de_overlap, 4),
                "ood_score": round(scores[name].ood_score, 4),
                "cost": scores[name].cost,
                "is_baseline": scores[name].is_baseline,
            }
            for name, b in ((bk.name, bk) for bk in backends)
        },
        "baseline_gate": [
            {
                "backend": v.backend,
                "admitted": v.admitted,
                "vs": v.beat_baseline,
                "l2_improvement": round(v.l2_improvement, 4),
                "ci_low": round(v.ci_low, 4),
                "reason": v.reason,
                "negative_claim": v.negative_claim,
            }
            for v in verdicts
        ],
        "selection_pre_knowledge": pre_pick,
        "selection_post_knowledge": post_pick,
        "selection_changed": pre_pick != post_pick,
        "causal_claims_total": len(claims),
        "causal_claims_supported": n_supported,
        "example_supported_claim": next(
            (
                {
                    "claim_id": c.claim_id,
                    "direction": c.direction,
                    "effect_size": round(c.effect_size, 3),
                    "status": c.status,
                    "evidence": c.evidence[0],
                    "proposals": c.proposals[:2],
                }
                for c in claims
                if c.status == "supported"
            ),
            None,
        ),
    }


def main() -> dict:
    report = {
        "milestone": "M27 perturbation-biology / virtual-cell v0.1",
        "validation_level": "retrospective (synthetic replay; NOT wet-lab validated)",
        "cell_state_dim": 40,
        "modality": "gene_knockout",
        "regimes": {r: run_regime(r) for r in ("informative", "scrambled")},
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
