"""M27 causal claim update (charter DoD #5/#6: a trusted observation updates the claim
"which perturbation affects which cell-state axis").

THE RED LINE (charter §4): models PROPOSE, only trusted OBSERVATIONS certify. This module
therefore takes :class:`ObservedResponse` (the trusted response) to move a claim's
``status``; a backend's :class:`ResponsePrediction` may only be recorded in the claim's
``proposals`` list (dry evidence, non-certifying) -- it can never set ``status``.

v0.1 honesty (charter §4/§5): the observations here are materialized from the
RETROSPECTIVE replay fixture (``is_wet_observation=False``). Exercising the claim
lifecycle with them is legitimate *within the simulation/retrospective boundary* and is
labeled as such in every claim's ``evidence`` (it records the dataset's non-wet
provenance). Certifying through the real expos trusted-observation path (a wet/sim reader
observation entering ``mcl``) is the seam handed to integration owner B
(docs/bio_seams/M27.md) -- NOT claimed as done here.
"""

from __future__ import annotations

import numpy as np

from domains.perturbation.objects import (
    ObservedResponse,
    PerturbationCausalClaim,
    PerturbationDataset,
)
from expos.adapters.models.virtual_cell import ResponsePrediction

_MAX_AXES_PER_PERT = 8  # only claim the top hits (a real screen claims the strong effects)


def certify_causal_claims(
    dataset: PerturbationDataset,
    *,
    effect_threshold: float = 2.0,
    min_replicates: int = 2,
    proposals: dict[str, list[ResponsePrediction]] | None = None,
) -> list[PerturbationCausalClaim]:
    """Certify per-(perturbation, axis) causal claims from the dataset's TRUSTED observed
    responses.

    For each perturbation, the up-to-:data:`_MAX_AXES_PER_PERT` axes with the largest
    ``|delta|`` are considered; an axis is ``supported`` (direction up/down) when
    ``|delta| >= effect_threshold`` and the observation has ``>= min_replicates``
    biological replicates, else ``insufficient``. ``proposals`` (backend name ->
    predictions) is recorded as NON-certifying dry evidence only.

    Every claim's ``evidence`` records the dataset fingerprint + its non-wet provenance,
    so a claim can never be mistaken for wet-certified.
    """
    prov_tag = (
        f"{dataset.fingerprint()} [{dataset.provenance.validation_level}; "
        f"is_wet_observation={dataset.provenance.is_wet_observation}]"
    )
    resp_by_id = {r.pert_id: r for r in dataset.responses}
    prop_by_id = {}
    if proposals:
        # index proposals by pert_id per backend for quick attach
        for bname, preds in proposals.items():
            for pr in preds:
                prop_by_id.setdefault(pr.pert_id, []).append((bname, pr))

    claims: list[PerturbationCausalClaim] = []
    for pert in dataset.perturbations:
        resp: ObservedResponse = resp_by_id[pert.pert_id]
        top_axes = np.argsort(-np.abs(resp.delta))[:_MAX_AXES_PER_PERT]
        for a in top_axes:
            eff = float(resp.delta[a])
            supported = (
                abs(eff) >= effect_threshold and resp.n_replicates >= min_replicates
            )
            claim = PerturbationCausalClaim(
                claim_id=f"m27_causal__{pert.pert_id}__{dataset.axis_names[a]}",
                pert_id=pert.pert_id,
                axis=dataset.axis_names[a],
                direction=("up" if eff > 0 else "down") if supported else "none",
                effect_size=eff,
                status="supported" if supported else "insufficient",
                evidence=[
                    f"trusted-observation delta={eff:.3f} n_rep={resp.n_replicates}; {prov_tag}"
                ],
                proposals=[
                    f"{bname} predicted axis-delta {float(pr.mean[a]):.3f} "
                    f"(abstained={pr.abstained})"
                    for bname, pr in prop_by_id.get(pert.pert_id, [])
                ],
            )
            claims.append(claim)
    return claims


def update_claim_with_observation(
    claim: PerturbationCausalClaim,
    observed_delta_on_axis: float,
    *,
    n_replicates: int = 1,
    effect_threshold: float = 2.0,
    min_replicates: int = 2,
    provenance: str = "trusted-observation",
) -> PerturbationCausalClaim:
    """Update a single claim's status/direction from a new trusted observation on its
    axis (the per-claim lifecycle step). Mutates and returns ``claim``. A model prediction
    must NOT be routed here -- only a trusted observation may move ``status``."""
    supported = abs(observed_delta_on_axis) >= effect_threshold and n_replicates >= min_replicates
    claim.effect_size = float(observed_delta_on_axis)
    if supported:
        claim.status = "supported"
        claim.direction = "up" if observed_delta_on_axis > 0 else "down"
    else:
        claim.status = "insufficient"
        claim.direction = "none"
    claim.evidence.append(
        f"{provenance}: delta={observed_delta_on_axis:.3f} n_rep={n_replicates}"
    )
    return claim


def certified_axis_index(
    claims: list[PerturbationCausalClaim], axis_names: tuple[str, ...]
) -> dict[str, set[int]]:
    """Build the ``pert_id -> {certified axis indices}`` map that active selection uses to
    down-weight already-learned perturbations (closes the knowledge -> next-decision loop)."""
    name_to_idx = {n: i for i, n in enumerate(axis_names)}
    out: dict[str, set[int]] = {}
    for c in claims:
        if c.status == "supported":
            out.setdefault(c.pert_id, set()).add(name_to_idx[c.axis])
    return out
