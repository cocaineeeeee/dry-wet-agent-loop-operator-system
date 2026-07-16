"""Deterministic analysis backend + a labelled synthetic assay-dataset generator (v0.1).

The backend computes a standardized effect (mean(perturbation) - control_mean), its
standard error over INDEPENDENT biological replicates, and z = effect / se, then emits an
:class:`EvidenceObservation`. It is pure and deterministic. It NEVER constructs a claim.

The dataset generator is honestly labelled ``simulation`` / ``is_wet_observation=False`` —
these are simulated measurements exercising the analysis + ledger path within the
simulation boundary (charter §5); a real wet/sim-reader observation entering ``mcl`` is the
seam handed to B (docs/bio_seams/M28.md).
"""

from __future__ import annotations

import hashlib
from math import sqrt

from analysis_backends.objects import AssayDataset, EvidenceObservation, ReplicateMeasurement
from hypotheses.objects import Hypothesis


def _obs_id(dataset: AssayDataset, hypothesis: Hypothesis) -> str:
    digest = hashlib.sha256(
        f"{dataset.fingerprint()}|{hypothesis.hypothesis_id}".encode("utf-8")
    ).hexdigest()
    return f"obs_{digest[:12]}"


class DeterministicAnalysisBackend:
    """Standardized-mean-difference analysis over an assay dataset. Deterministic; emits
    evidence only. The ``trusted`` flag on the produced observation is taken from the
    dataset's provenance (``is_wet_observation``) unless ``force_trusted`` is given for a
    retrospective-but-adjudicated fixture (still honestly labelled)."""

    name = "deterministic-smd-v1"

    def analyse(
        self,
        hypothesis: Hypothesis,
        dataset: AssayDataset,
        *,
        force_trusted: bool | None = None,
    ) -> EvidenceObservation:
        # Independent biological replicates drive the SE; technical replicates are counted
        # but do NOT inflate the effective sample size (red line: no pseudo-replication).
        bio = [r.value for r in dataset.replicates if r.replicate_kind == "biological"]
        n_bio = len(bio)
        n_tech = dataset.n_technical()

        if n_bio == 0:
            effect, se, z = 0.0, 0.0, 0.0
        else:
            mean = sum(bio) / n_bio
            effect = mean - dataset.control_mean
            if n_bio >= 2:
                var = sum((v - mean) ** 2 for v in bio) / (n_bio - 1)
                se = sqrt(var / n_bio) if var > 0 else 1e-9
            else:
                se = 1e-9  # single biological replicate: SE undefined → tiny → not decisive by rep gate
            z = effect / se if se > 0 else 0.0

        trusted = dataset.is_wet_observation if force_trusted is None else force_trusted
        return EvidenceObservation(
            observation_id=_obs_id(dataset, hypothesis),
            perturbation=dataset.perturbation,
            axis=dataset.axis,
            effect=effect,
            se=se,
            z=z,
            n_biological_replicates=n_bio,
            n_technical_replicates=n_tech,
            trusted=trusted,
            dataset_fingerprint=dataset.fingerprint(),
            validation_level=dataset.validation_level,
            is_wet_observation=dataset.is_wet_observation,
            note=(
                f"{self.name}: effect={effect:.3f} se={se:.3g} z={z:.2f} "
                f"n_bio={n_bio} n_tech={n_tech} "
                f"[{dataset.validation_level}; wet={dataset.is_wet_observation}]"
            ),
        )


def make_assay_dataset(
    perturbation: str,
    axis: str,
    *,
    true_effect: float,
    control_mean: float = 0.0,
    n_biological: int = 3,
    n_technical: int = 0,
    noise: float = 0.4,
    seed: int = 0,
    validation_level: str = "simulation",
    is_wet_observation: bool = False,
) -> AssayDataset:
    """Deterministic synthetic assay dataset (honestly labelled ``simulation``).

    Biological replicates are drawn around ``control_mean + true_effect`` with a fixed,
    seed-derived pseudo-noise (NO RNG — a reproducible hash-derived jitter so v0.1 is
    bit-for-bit). Technical replicates duplicate the first biological sample (they add NO
    independent information — that is the point)."""
    reps: list[ReplicateMeasurement] = []
    base = control_mean + true_effect
    for i in range(n_biological):
        # deterministic jitter in [-noise, +noise) from a stable hash.
        h = int(hashlib.sha256(f"{perturbation}|{axis}|{seed}|{i}".encode()).hexdigest(), 16)
        jitter = ((h % 2000) / 1000.0 - 1.0) * noise
        reps.append(
            ReplicateMeasurement(
                perturbation=perturbation,
                axis=axis,
                value=base + jitter,
                replicate_id=f"bio_{i}",
                replicate_kind="biological",
            )
        )
    first_val = reps[0].value if reps else base
    for j in range(n_technical):
        reps.append(
            ReplicateMeasurement(
                perturbation=perturbation,
                axis=axis,
                value=first_val,  # a re-measure of the SAME sample → no new information
                replicate_id=f"tech_{j}",
                replicate_kind="technical",
            )
        )
    return AssayDataset(
        perturbation=perturbation,
        axis=axis,
        control_mean=control_mean,
        replicates=tuple(reps),
        validation_level=validation_level,
        is_wet_observation=is_wet_observation,
    )
