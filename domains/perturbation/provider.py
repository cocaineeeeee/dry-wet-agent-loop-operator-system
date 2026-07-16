"""M27 domain provider: ``perturbation_screen`` (charter DoD #1: typed domain object +
PROVIDER). Implements the existing five-hook :class:`DomainProvider` contract so the
perturbation-biology domain plugs into expos's birth-time governance exactly as the
chemistry / cell-free-expression domains do -- and so it can be validated domain-locally
NOW (``check_complete()`` passes) ahead of B wiring it into ``mcl``.

Biology stays confined to this domain/provider/adapter layer (charter §4): the provider
imports only leaf tables + the adapter-layer model carrier, never a kernel/mcl symbol.

SEAM (docs/bio_seams/M27.md, integration owner B): the ``input_kind`` this domain needs,
``cell_state_perturbation``, is NOT yet in the central vocabulary
(``expos/adapters/domain_provider.py`` defines only molecular_geometry / sequence_*). We
reference the literal locally (``objects.INPUT_KIND_CELL_STATE_PERTURBATION``) so the
provider is constructible and governance-checkable today; adding it centrally (plus a
``cell_state_perturbation`` dry-adapter capability + the model-competition dispatch seam)
is B's single-writer job. Until then, full ``load_domain()`` would reject the yaml at the
adapter gate -- the exact staging precedent ``cell_free_expression_screen`` sat in.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import numpy as np

from expos.adapters.domain_provider import (
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    SeedClaim,
)
from expos.adapters.models.virtual_cell import PerturbationBatch

from domains.perturbation.objects import (
    CELL_STATE_PERTURBATION_SCHEMA_VERSION,
    INPUT_KIND_CELL_STATE_PERTURBATION,
)

if TYPE_CHECKING:  # pragma: no cover
    from domains.perturbation.objects import PerturbationDataset
    from expos.domain import DomainConfig

_SCREEN_VAR = "knockout"

#: Canonical small knockout level set for the discrete screening machinery (the dynamic
#: replay dataset carries the full population; these are the provider's declared levels).
#: coord = a public design descriptor (relative knockdown strength proxy), NOT truth.
_KNOCKOUTS: dict[str, float] = {
    "ko_ctrl0": 0.05,
    "ko_low1": 0.25,
    "ko_mid2": 0.45,
    "ko_mid3": 0.60,
    "ko_high4": 0.80,
    "ko_high5": 1.00,
}

#: Truth faces (reader-side only): a positive perturbation-effect face (effect grows with
#: the design coordinate), its flipped mirror, and the shared cross-domain ``flat`` null.
_FACES: dict[str, float] = {
    "perturbation_effect_high": 1.0,
    "perturbation_effect_flipped": 0.0,
    "flat": 0.5,
}
_NULL_FACES = frozenset({"flat"})

#: Seed causal family: "strong knockout drives a larger cell-state shift" (supported/higher)
#: + its rejected mirror (charter DoD #5 seed-claim entry point).
_SEED_CLAIMS: tuple[SeedClaim, ...] = (
    SeedClaim(
        claim_id="p_strongko_shifts_more",
        status="supported",
        direction="higher",
        statement="a stronger knockout produces a larger cell-state response shift",
    ),
    SeedClaim(
        claim_id="p_weakko_shifts_more",
        status="rejected",
        direction="lower",
        statement="a weaker knockout produces a larger cell-state response shift",
    ),
)


def cell_state_perturbation_target(target_id: str, coord: float) -> ComputeTarget:
    """A ``cell_state_perturbation`` ComputeTarget for one knockout level. Payload carries
    the knockout id + its public design coordinate (no fabricated geometry)."""
    return ComputeTarget(
        target_id=target_id,
        input_kind=INPUT_KIND_CELL_STATE_PERTURBATION,
        payload={"knockout": target_id, "design_coord": float(coord), "modality": "gene_knockout"},
        payload_schema_version=CELL_STATE_PERTURBATION_SCHEMA_VERSION,
        adapter_capability=INPUT_KIND_CELL_STATE_PERTURBATION,
    )


#: ---- v0.1 replay-source parameters for the neutral round-batch hook ------------------
#: The RETROSPECTIVE replay fixture the model-competition leg is scored on. These are
#: DEFAULTS (constructor-overridable) and every one of them folds into
#: :meth:`PerturbationScreenProvider.round_batches_fingerprint`, so a change to the source
#: or the split flips the batch provenance token (docs/bio_seams/M27.md SEAM 4).
_REPLAY_SEED = 27
_REPLAY_N_PERT = 60
_REPLAY_N_OOD = 6
_REPLAY_REGIME = "informative"
#: Ids carrying this prefix are DELIBERATELY out-of-distribution: they are ALWAYS held out
#: and NEVER enter training (the abstention face reads the mask; no backend ever does).
_OOD_PREFIX = "OOD_"
#: Fraction of the in-distribution pool reserved as the round-INVARIANT held-out split.
_HOLDOUT_FRAC = 0.3
_SPLIT_SEED = 1
_MIN_HOLDOUT = 4
#: The training set GROWS with the round (see :meth:`round_batches`): round r trains on the
#: first ``ceil(n_pool * min(1, INITIAL + r*GROWTH))`` rows of a deterministic reveal order.
_TRAIN_INITIAL_FRAC = 0.6
_TRAIN_GROWTH_FRAC = 0.2
#: A ridge fit on a handful of rows is not a competition; refuse a degenerate train split.
_MIN_TRAIN = 8


class PerturbationScreenProvider(DomainProvider):
    """DomainProvider for the M27 perturbation-biology domain (cell-state + knockout).

    Beyond the five contract hooks it exposes ONE extra, domain-neutral hook —
    :meth:`round_batches` — the M27 analogue of M25's ``propose_candidates`` (docs/
    bio_seams/M27.md SEAM 2). See that method for the seam contract.

    Constructor knobs select the retrospective replay source + split (all defaulted, so
    ``check_complete()`` / ``load_domain`` construct it with no arguments); every knob folds
    into :meth:`round_batches_fingerprint`."""

    domain_name = "perturbation_screen"

    def __init__(
        self,
        *,
        replay_regime: str = _REPLAY_REGIME,
        replay_seed: int = _REPLAY_SEED,
        n_pert: int = _REPLAY_N_PERT,
        n_ood: int = _REPLAY_N_OOD,
        holdout_frac: float = _HOLDOUT_FRAC,
        split_seed: int = _SPLIT_SEED,
    ) -> None:
        self._replay_regime = replay_regime
        self._replay_seed = int(replay_seed)
        self._n_pert = int(n_pert)
        self._n_ood = int(n_ood)
        self._holdout_frac = float(holdout_frac)
        self._split_seed = int(split_seed)
        self._dataset_cache: "PerturbationDataset | None" = None

    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        return {k: cell_state_perturbation_target(k, c) for k, c in _KNOCKOUTS.items()}

    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
        return {k: {"coord": float(c)} for k, c in _KNOCKOUTS.items()}

    def truth_profiles(self) -> Mapping[str, float]:
        return dict(_FACES)

    def null_profiles(self) -> frozenset[str]:
        return _NULL_FACES

    def seed_claims(self) -> Sequence[SeedClaim]:
        return _SEED_CLAIMS

    def validate_yaml(self, cfg: "DomainConfig") -> None:
        var = next((v for v in cfg.design_space.variables if v.name == _SCREEN_VAR), None)
        if var is None:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: yaml design_space has no screening "
                f"variable {_SCREEN_VAR!r} (declared: "
                f"{sorted(v.name for v in cfg.design_space.variables)})"
            )
        choices = set(var.choices or ())
        levels = set(_KNOCKOUTS)
        if choices != levels:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: yaml {_SCREEN_VAR!r} choices must equal "
                f"the provider's knockouts (yaml-only={sorted(choices - levels)}, "
                f"provider-only={sorted(levels - choices)})"
            )

    # ---- SEAM 2: the neutral round-batch hook (the M27 batch_compete driver) ----------
    #
    # WHY IT LIVES HERE AND NOT IN mcl (B's constraint, red_to_blue/160 §3.2). The M27 dry
    # leg is BATCH-shaped: ``CellStatePerturbationAdapter.compete_round(train_batch,
    # held_batch)`` needs the round's training/held split WITH reference deltas. Those
    # batches are built out of ``datasets/replay/*`` — biology. If mcl reached into the
    # replay package to build them, biology would enter the domain-NEUTRAL core (EXP014 red,
    # charter §4: biological semantics live only in domain/provider/adapter/QC). So the DATA
    # ACCESS is sealed inside the provider and mcl only ever calls the neutral signature
    #
    #     train_batch, held_batch = provider.round_batches(round_id)
    #
    # and hands the two opaque carriers straight to the adapter it already dispatches to.
    # mcl imports nothing from ``datasets/`` or ``domains/``, learns no biological word, and
    # gates on the HOOK'S PRESENCE (the ``propose_candidates`` precedent) — a provider
    # without ``round_batches`` is unchanged.

    def round_batches(
        self, round_id: int = 0, *, seed: int = 0
    ) -> tuple[PerturbationBatch, PerturbationBatch]:
        """This round's ``(train_batch, held_batch)`` — the neutral hook the batch-shaped
        dry leg is driven from. ``held_batch`` ALWAYS carries the REFERENCE DELTAS
        (``held_batch.deltas``, the observed cell-state delta-from-control every face is
        scored against) plus the ``ood_mask``; ``train_batch`` carries the rows the backends
        may fit on. Pure + deterministic in ``(round_id, seed)`` — no clock, no I/O, no
        unseeded randomness (gate K5), so a resumed round rebuilds the same split bitwise.

        Round shape (v0.1): the HELD-OUT split is round-INVARIANT (so a cross-round score
        comparison means something and no round leaks a held row into training) and the
        TRAINING set GROWS with ``round_id`` along a deterministic reveal order — the replay
        analogue of "each round the loop has more observations to fit on". Every ``OOD_``
        perturbation is held out in every round and never enters training.

        HONESTY (charter §4, the iron rule — this hook is the one that touches data, so the
        rule is enforced HERE). The rows come from ``datasets/replay/*``: RETROSPECTIVE,
        ``is_wet_observation=False``, ``role='benchmark_calibration'`` material. They are
        BENCHMARK/CALIBRATION input to the DRY model-competition leg — they are NOT this
        run's wet observations and this hook can never make them so: the dataset provenance
        is re-checked on every build and a ``is_wet_observation=True`` source is refused
        LOUDLY (:class:`DatasetProvenance` refuses to even construct one). Nothing this hook
        returns may be promoted to a trusted observation; certification of a BIOLOGICAL claim
        still requires a trusted observation on the real wet/sim path (SEAM 3).

        ``seed`` is the run seed B may thread in (defaulted, like ``propose_candidates``'s);
        it only re-draws the deterministic split, never the data-generating truth."""
        dataset = self._replay_dataset()
        train_ids, held_ids = self._round_split(round_id, seed)
        train_batch = dataset.subset(list(train_ids)).to_batch()
        held = dataset.subset(list(held_ids)).to_batch()
        held_batch = PerturbationBatch(
            pert_ids=held.pert_ids,
            embeddings=held.embeddings,
            deltas=held.deltas,
            ood_mask=np.array([pid.startswith(_OOD_PREFIX) for pid in held.pert_ids]),
        )
        if held_batch.deltas is None or train_batch.deltas is None:
            raise DomainProviderError(  # pragma: no cover - structurally unreachable
                f"domain {self.domain_name!r}: round_batches must yield reference deltas "
                "on both batches (the competition leg scores against them)"
            )
        return train_batch, held_batch

    def round_batches_provenance(self) -> Mapping[str, Any]:
        """The machine-readable §4 label of what :meth:`round_batches` yields: source,
        scope (the context boundary a negative result must be scoped to), validation level,
        ``is_wet_observation`` (hard False) and role. A neutral dict of scalars — B can stamp
        it into a report / the batch's dry provenance without importing a domain symbol."""
        p = self._replay_dataset().provenance
        return {
            "source": p.source,
            "scope": p.scope,
            "validation_level": p.validation_level,
            "is_wet_observation": p.is_wet_observation,
            "role": p.role,
            "regime": self._replay_regime,
        }

    def round_batches_fingerprint(self) -> str:
        """Provenance token for the round-batch SOURCE: the replay dataset's own fingerprint
        (which folds provenance + scope + data bytes) plus the split parameters.

        SEAM (docs/bio_seams/M27.md SEAM 4): fold this into ``config_fingerprint`` exactly as
        ``_run_config_fingerprint`` folds M25's ``operator_fingerprint`` — gated on the hook's
        presence, so every other domain stays byte-identical. Then a replay-source or
        regime/scope change flips run identity instead of silently re-scoring the gate on
        different data."""
        h = hashlib.sha256()
        h.update(self._replay_dataset().fingerprint().encode("utf-8"))
        h.update(b"\x00split\x00")
        h.update(
            json.dumps(
                {
                    "holdout_frac": self._holdout_frac,
                    "split_seed": self._split_seed,
                    "min_holdout": _MIN_HOLDOUT,
                    "min_train": _MIN_TRAIN,
                    "train_initial_frac": _TRAIN_INITIAL_FRAC,
                    "train_growth_frac": _TRAIN_GROWTH_FRAC,
                    "ood_prefix": _OOD_PREFIX,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return "batches:sha256:" + h.hexdigest()[:16]

    # ---- replay source, sealed behind the hook ---------------------------------------

    def _replay_dataset(self) -> "PerturbationDataset":
        """Build (once) the retrospective replay dataset. Imported IN-METHOD so importing
        the provider never drags the dataset package in at load time (the same discipline
        ``CellStatePerturbationAdapter`` uses for its in-method domain import), and so this
        module keeps its "leaf tables only" import posture at module scope."""
        if self._dataset_cache is None:
            from datasets.replay.synthetic_perturbseq import make_replay_dataset

            dataset = make_replay_dataset(
                seed=self._replay_seed,
                n_pert=self._n_pert,
                regime=self._replay_regime,
                n_ood=self._n_ood,
            )
            # Re-assert the dual-role guard AT THE HOOK (charter §4). DatasetProvenance
            # already refuses to construct a wet-flagged replay dataset; this is the second
            # lock on the one code path that could ever hand replay rows to the OS.
            if dataset.provenance.is_wet_observation:
                raise DomainProviderError(  # pragma: no cover - guarded upstream too
                    f"domain {self.domain_name!r}: replay source claims "
                    "is_wet_observation=True; retrospective replay is benchmark/calibration "
                    "material and can NEVER be a wet observation of this run (charter §4)"
                )
            self._dataset_cache = dataset
        return self._dataset_cache

    def _round_split(self, round_id: int, seed: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """``(train_ids, held_ids)`` for one round. Held-out = a round-invariant draw from
        the in-distribution pool + EVERY ``OOD_`` id; train = the growing prefix of the
        remaining pool in a deterministic reveal order."""
        dataset = self._replay_dataset()
        ids = [p.pert_id for p in dataset.perturbations]
        ood_ids = [i for i in ids if i.startswith(_OOD_PREFIX)]
        in_ids = [i for i in ids if not i.startswith(_OOD_PREFIX)]
        rng = np.random.default_rng(self._split_seed + int(seed))
        perm = rng.permutation(len(in_ids))
        n_hold = max(_MIN_HOLDOUT, int(len(in_ids) * self._holdout_frac))
        hold = {in_ids[perm[k]] for k in range(min(n_hold, len(in_ids)))}
        held_ids = [i for i in in_ids if i in hold] + ood_ids  # OOD is ALWAYS held out
        pool = [i for i in in_ids if i not in hold]  # OOD NEVER enters training
        # Deterministic reveal order: which rows a round is "allowed to have observed".
        order = np.random.default_rng(self._split_seed + int(seed) + 1_000).permutation(
            len(pool)
        )
        revealed = [pool[k] for k in order]
        n_train = self._train_size(len(revealed), round_id)
        return tuple(sorted(revealed[:n_train])), tuple(held_ids)

    def _train_size(self, n_pool: int, round_id: int) -> int:
        frac = min(1.0, _TRAIN_INITIAL_FRAC + _TRAIN_GROWTH_FRAC * max(0, int(round_id)))
        n = int(math.ceil(n_pool * frac))
        if n_pool < _MIN_TRAIN:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: training pool of {n_pool} row(s) is too "
                f"small to fit a competition roster on (need >= {_MIN_TRAIN}); widen the "
                "replay source or lower holdout_frac"
            )
        return max(_MIN_TRAIN, min(n_pool, n))
