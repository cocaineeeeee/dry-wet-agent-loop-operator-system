"""Typed objects for M28 analysis (v0.1).

An :class:`AssayDataset` is a labelled bundle of replicate measurements for one
(perturbation, axis). An :class:`EvidenceObservation` is the SOLE output of an analysis
backend: a self-sufficient statistic (effect / se / z) plus honest provenance (dataset
fingerprint, validation level, whether the replicates are independent BIOLOGICAL replicates
or merely technical). It carries a ``trusted`` flag — only trusted evidence may drive a
decisive verdict (charter §4) — but it CANNOT itself certify: it is data, not a claim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReplicateMeasurement:
    """One measured effect for a (perturbation, axis).

    ``replicate_kind`` is either ``"biological"`` (an independent biological replicate) or
    ``"technical"`` (a re-measure of the same biological sample). The distinction is
    load-bearing: technical replicates never count toward independent-evidence strength
    (charter §4 / M24-B collapse mechanism)."""

    perturbation: str
    axis: str
    value: float
    replicate_id: str
    replicate_kind: str = "biological"  # "biological" | "technical"


@dataclass(frozen=True)
class AssayDataset:
    """A labelled set of replicate measurements for one (perturbation, axis).

    Honest provenance is mandatory: ``validation_level`` (simulation / retrospective /
    prospective_wet / physical_autonomous) and ``is_wet_observation`` travel with the data
    so no downstream code can mistake a simulated measurement for a wet one."""

    perturbation: str
    axis: str
    control_mean: float
    replicates: tuple[ReplicateMeasurement, ...] = field(default_factory=tuple)
    validation_level: str = "simulation"
    is_wet_observation: bool = False
    source: str = "synthetic-deterministic"

    def fingerprint(self) -> str:
        payload = {
            "perturbation": self.perturbation,
            "axis": self.axis,
            "control_mean": self.control_mean,
            "validation_level": self.validation_level,
            "is_wet_observation": self.is_wet_observation,
            "source": self.source,
            "replicates": [
                [r.replicate_id, r.replicate_kind, r.value] for r in self.replicates
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def n_biological(self) -> int:
        return sum(1 for r in self.replicates if r.replicate_kind == "biological")

    def n_technical(self) -> int:
        return sum(1 for r in self.replicates if r.replicate_kind == "technical")


@dataclass(frozen=True)
class EvidenceObservation:
    """The sole output of an analysis backend: a statistic over an assay dataset, with
    honest provenance. NOT a claim — it can only ever be *recorded* as evidence; the ledger
    (via the bridge + kernel gate) decides what verdict it supports.

    ``trusted`` marks certified/wet-adjudicated evidence. ``n_biological_replicates`` is the
    independent-evidence count the replication policy reads (technical replicates excluded).
    """

    observation_id: str
    perturbation: str
    axis: str
    effect: float  # signed effect vs control
    se: float  # standard error of the effect
    z: float  # effect / se
    n_biological_replicates: int
    n_technical_replicates: int
    trusted: bool
    dataset_fingerprint: str
    validation_level: str
    is_wet_observation: bool
    note: str = ""

    def content_fingerprint(self) -> str:
        payload = {
            "observation_id": self.observation_id,
            "perturbation": self.perturbation,
            "axis": self.axis,
            "effect": self.effect,
            "se": self.se,
            "n_biological_replicates": self.n_biological_replicates,
            "n_technical_replicates": self.n_technical_replicates,
            "trusted": self.trusted,
            "dataset_fingerprint": self.dataset_fingerprint,
            "validation_level": self.validation_level,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
