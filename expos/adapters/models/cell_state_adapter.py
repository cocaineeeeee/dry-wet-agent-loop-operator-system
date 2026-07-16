"""CellStatePerturbationAdapter (M27 v0.1) -- the DRY model-competition leg for the
``cell_state_perturbation`` input_kind, the perturbation-biology analogue of
``CircuitTopologyAdapter`` (M26) / ``SequenceProxyAdapter``.

WHERE IT SITS. ``CircuitTopologyAdapter`` reduces a typed graph to a per-well phenotype;
this adapter is the M27 organ's *competition* dry leg: each round it fits the registered
virtual-cell backends on the round's training observations, scores them on a held-out
split (``competition.score_backend`` -> the five decision faces), and runs the HARD
BASELINE-GATE (``competition.baseline_gate``). Its output is DRY evidence / proposals:
admitted backends (which may steer the next selection) + a first-class NEGATIVE claim for
every expensive proposer that did NOT significantly-and-calibratedly beat the baseline.

TRUTH SEMANTICS (charter §4, identical to the other dry legs). A backend PROPOSES; this
leg SCORES proposals and decides *model admission*. It NEVER certifies a biological claim
(that needs a trusted observation -- ``domains/perturbation/causal.py``) and NEVER mutates
a claim ledger. "Did not clear the baseline-gate" is a first-class REJECTED claim
(``kind="baseline_gate_negative"``), not a leaderboard row -- knowledge, not failure.

SEAM (docs/bio_seams/M27.md, integration owner B). ``input_kind='cell_state_perturbation'``
is already in the central vocabulary (``domain_provider.INPUT_KIND_CELL_STATE_PERTURBATION``,
landed by B). The remaining seam is registering THIS adapter in mcl's dry dispatch +
``ADAPTER_REGISTRY`` (as ``circuit_topology`` / ``sequence_proxy`` are) and routing each
round's negative claims into the existing claim ledger. Until then the adapter runs
domain-locally (``compete_round`` / ``compete_from_dataset`` stand alone, exercised by
``tests/test_m27_perturbation_v01.py``). It imports NO kernel/ledger/mcl symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Sequence

import numpy as np

from expos.adapters.domain_provider import INPUT_KIND_CELL_STATE_PERTURBATION
from expos.adapters.models.virtual_cell import BioModelBackend, PerturbationBatch
from expos.adapters.models.virtual_cell_baselines import (
    LinearResponseBackend,
    MeanBaselineBackend,
)
from expos.adapters.models.virtual_cell_complex import KNNResponseBackend
from expos.adapters.models.virtual_cell_ensemble import EnsembleBackend
from expos.adapters.models.virtual_cell_pathway import PathwayInformedBackend

if TYPE_CHECKING:  # pragma: no cover
    from domains.perturbation.competition import FaceScores, GateVerdict
    from domains.perturbation.objects import PerturbationDataset

#: The default bio_refs §3 competition grid: two MANDATORY baselines (mean + linear-ridge)
#: + three expensive candidate proposers, each of which must clear the baseline-gate. A
#: fresh, UNFITTED list every call (``compete_round`` fits them on the round's train split).
def _default_backends() -> list[BioModelBackend]:
    return [
        MeanBaselineBackend(),
        LinearResponseBackend(),
        KNNResponseBackend(),
        PathwayInformedBackend(),
        EnsembleBackend(),
    ]


@dataclass(frozen=True)
class CompetitionRoundResult:
    """The dry-evidence bundle this leg emits for ONE round of the perturbation loop.

      * ``admitted``        -- names of backends that cleared the gate (baselines are
        always available voters; ONLY these expensive proposers additionally steer
        acquisition -- an un-admitted proposer must NOT influence selection, bio_refs §1.4).
      * ``negative_claims`` -- one first-class REJECTED claim per proposer that did NOT
        clear the gate (``kind="baseline_gate_negative"``). This is the load-bearing output
        B routes into the claim ledger.
      * ``verdicts``        -- the full per-proposer ``GateVerdict`` list (admitted + not).
      * ``scores``          -- per-backend ``FaceScores`` (the five decision faces).
      * ``backend_fingerprints`` -- ``name@version#sha256:<weight-hash>`` per backend, so a
        re-fit on different data flips the provenance token (charter obligation #1).
    """

    round_index: int
    admitted: list[str]
    negative_claims: list[dict]
    verdicts: list["GateVerdict"] = field(repr=False, default_factory=list)
    scores: dict[str, "FaceScores"] = field(repr=False, default_factory=dict)
    backend_fingerprints: dict[str, str] = field(default_factory=dict)

    @property
    def has_negative(self) -> bool:
        """True iff at least one expensive proposer failed the baseline-gate this round."""
        return bool(self.negative_claims)


class CellStatePerturbationAdapter:
    """Synchronous, deterministic DRY model-competition leg for ``perturbation_screen``.

    Fit the backends on the round's training observations, score on a held-out split, run
    the baseline-gate; emit admitted models + first-class negative claims. It reads only
    numpy carriers + reference deltas -- never a claim or a kernel symbol.
    """

    name = "cell_state_perturbation"

    #: Capability declaration (docs/bio_refs §C). Matches the shared central constant B
    #: landed, so B's dry-dispatch registration keys straight onto it.
    ACCEPTS_INPUT_KINDS: tuple[str, ...] = (INPUT_KIND_CELL_STATE_PERTURBATION,)

    default_metric = "cell_state_response"

    def __init__(
        self, backend_factory: Callable[[], Sequence[BioModelBackend]] | None = None
    ) -> None:
        # A ZERO-ARG factory returning a fresh, unfitted backend list (default = the
        # bio_refs §3 grid). Injectable so B / tests can swap the competition roster.
        self._backend_factory = backend_factory or _default_backends

    # ---- the per-round competition dispatch (the load-bearing dry leg) --------

    def compete_round(
        self,
        train_batch: PerturbationBatch,
        held_batch: PerturbationBatch,
        *,
        round_index: int = 0,
        min_improvement: float = 0.05,
        calibration_slack: float = 0.15,
    ) -> CompetitionRoundResult:
        """Fit -> score -> baseline-gate for ONE round. ``held_batch`` must carry reference
        ``deltas`` (retrospective / non-wet) and MAY carry an ``ood_mask`` (the OOD/abstention
        face reads it; the backends never do). Returns admitted backends + first-class
        negative claims for the proposers that did not clear the gate."""
        # Lazy import so ``expos.adapters`` never hard-depends on the domain package at
        # import time (same discipline as CircuitTopologyAdapter's in-method domain import).
        from domains.perturbation.competition import baseline_gate, score_backend

        if held_batch.deltas is None:
            raise ValueError(
                f"{self.name}: held_batch must carry reference deltas to score against"
            )
        backends = [b.fit(train_batch) for b in self._backend_factory()]
        if not any(b.is_baseline for b in backends):
            raise ValueError(
                f"{self.name}: competition roster has no baseline backend "
                "(the baseline-gate needs at least the mean baseline to gate against)"
            )
        scores = {b.name: score_backend(b, held_batch) for b in backends}
        verdicts = baseline_gate(
            scores, min_improvement=min_improvement, calibration_slack=calibration_slack
        )
        admitted = [v.backend for v in verdicts if v.admitted]
        negative_claims = [
            dict(v.negative_claim, round_index=round_index)
            for v in verdicts
            if v.negative_claim is not None
        ]
        return CompetitionRoundResult(
            round_index=round_index,
            admitted=admitted,
            negative_claims=negative_claims,
            verdicts=verdicts,
            scores=scores,
            backend_fingerprints={b.name: b.fingerprint() for b in backends},
        )

    # ---- convenience: drive one round straight from a typed dataset -----------

    def compete_from_dataset(
        self,
        dataset: "PerturbationDataset",
        *,
        round_index: int = 0,
        holdout_frac: float = 0.3,
        split_seed: int = 1,
        **gate_kwargs,
    ) -> CompetitionRoundResult:
        """Split a ``PerturbationDataset`` into train / held-out (OOD_ perturbations are
        ALWAYS held out and never enter training) and run one competition round. This is
        the domain-local, one-call entry (a runnable demo + the test hook); the mcl seam
        will instead feed ``compete_round`` the round's real training observations."""
        ids = [p.pert_id for p in dataset.perturbations]
        ood_ids = [i for i in ids if i.startswith("OOD_")]
        in_ids = [i for i in ids if not i.startswith("OOD_")]
        rng = np.random.default_rng(split_seed)
        perm = rng.permutation(len(in_ids))
        n_hold = max(4, int(len(in_ids) * holdout_frac))
        hold = {in_ids[perm[k]] for k in range(n_hold)}
        train_ids = [i for i in in_ids if i not in hold]
        held_ids = [i for i in in_ids if i in hold] + ood_ids

        train_batch = dataset.subset(train_ids).to_batch()
        held = dataset.subset(held_ids).to_batch()
        ood_mask = np.array([pid.startswith("OOD_") for pid in held.pert_ids])
        held_batch = PerturbationBatch(
            pert_ids=held.pert_ids,
            embeddings=held.embeddings,
            deltas=held.deltas,
            ood_mask=ood_mask,
        )
        return self.compete_round(
            train_batch, held_batch, round_index=round_index, **gate_kwargs
        )
