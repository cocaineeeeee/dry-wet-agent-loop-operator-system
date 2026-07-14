"""M27 typed domain objects (charter v0.1 DoD #1: a typed domain object + provider).

The perturbation-biology / virtual-cell domain. v0.1 scope (deliberately small, NOT a
full virtual human cell): a **40-dim cell-state vector**, **gene-knockout** interventions
(the modality the Nat. Methods baseline paper + ``solve_y_axb`` reference use), a response
*distribution* per axis, and a causal claim of the shape "knockout of gene X moves
cell-state axis A in direction D".

Biological semantics live here in the domain layer (charter §4). These objects import
only the adapter-layer numpy carrier (:class:`PerturbationBatch`) -- they never import a
kernel/ledger/claim symbol, and nothing here certifies anything.

PROVENANCE / VALIDATION-LEVEL HONESTY (charter §4/§5, the IRON RULE): the retrospective
replay data these objects carry is ``validation_level='retrospective'`` benchmark /
calibration material -- it is **NEVER a wet observation produced by this run**. Every
dataset carries a :class:`DatasetProvenance` with ``is_wet_observation=False`` and its
scope, and that provenance folds into the dataset fingerprint. A caller that tries to
promote replay data to a trusted observation must be refused upstream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from expos.adapters.models.virtual_cell import PerturbationBatch

#: This domain's input_kind. NOTE (seam to integration owner B, docs/bio_seams/M27.md):
#: the central vocabulary ``expos/adapters/domain_provider.py`` only defines
#: ``molecular_geometry`` / ``sequence_construct`` / ``sequence_features``. Adding
#: ``cell_state_perturbation`` to that module is B's single-writer job; we reference the
#: literal locally so the provider is constructible + testable domain-locally now.
INPUT_KIND_CELL_STATE_PERTURBATION = "cell_state_perturbation"
CELL_STATE_PERTURBATION_SCHEMA_VERSION = "cell_state_perturbation/1"

ValidationLevel = Literal[
    "simulation", "retrospective", "prospective_wet", "physical_autonomous"
]


@dataclass(frozen=True)
class CellState:
    """A cell-state vector: ``dim`` scalar axes (e.g. summarized gene-program readouts).
    v0.1 uses ``dim`` in [20,100]. ``axis_names`` labels each axis (a causal claim is
    stated per named axis)."""

    axis_names: tuple[str, ...]
    values: np.ndarray  # (dim,)

    def __post_init__(self) -> None:
        v = np.asarray(self.values, dtype=float)
        object.__setattr__(self, "values", v)
        if v.ndim != 1 or v.shape[0] != len(self.axis_names):
            raise ValueError(
                f"CellState: values must be ({len(self.axis_names)},); got {v.shape}"
            )

    @property
    def dim(self) -> int:
        return len(self.axis_names)


@dataclass(frozen=True)
class Perturbation:
    """One intervention on the cell. v0.1 modality = ``gene_knockout``. ``target`` is the
    knocked-out gene id; ``embedding`` is its perturbation feature vector (design/frozen-
    embedding -- provenance rides in the dataset fingerprint, never learned here)."""

    pert_id: str
    modality: Literal["gene_knockout", "drug", "cytokine"]
    target: str
    embedding: np.ndarray  # (d,)

    def __post_init__(self) -> None:
        e = np.asarray(self.embedding, dtype=float)
        object.__setattr__(self, "embedding", e)
        if e.ndim != 1:
            raise ValueError(f"Perturbation {self.pert_id}: embedding must be 1-D")


@dataclass(frozen=True)
class ObservedResponse:
    """A trusted, per-perturbation observed cell-state delta-from-control (the response),
    with a biological-replicate count. This is what a *real wet observation* would look
    like; in v0.1 it is materialized from retrospective replay and MUST carry the
    replay's non-wet provenance -- it is used only to score models / update claims within
    the honest simulation/retrospective boundary, never presented as this-run wet data."""

    pert_id: str
    delta: np.ndarray  # (dim,)
    n_replicates: int = 1


@dataclass(frozen=True)
class DatasetProvenance:
    """Where a :class:`PerturbationDataset` came from + its DUAL-ROLE guard (charter §4).

    ``role`` is fixed to benchmark/calibration; ``is_wet_observation`` is hard-False. The
    ``scope`` (e.g. "K562 cancer line; single-gene knockouts") is a first-class context
    boundary (bio_refs §1: the negative result itself must be scoped) and enters the
    fingerprint so a scope change flips provenance identity."""

    source: str
    scope: str
    validation_level: ValidationLevel = "retrospective"
    is_wet_observation: bool = False
    role: Literal["benchmark_calibration"] = "benchmark_calibration"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.is_wet_observation:
            raise ValueError(
                "DatasetProvenance: replay/public data can NEVER be a wet observation "
                "(charter §4 iron rule). is_wet_observation must be False."
            )

    def fingerprint(self) -> str:
        blob = json.dumps(
            {
                "source": self.source,
                "scope": self.scope,
                "validation_level": self.validation_level,
                "is_wet_observation": self.is_wet_observation,
                "role": self.role,
            },
            sort_keys=True,
        ).encode()
        return "prov:sha256:" + hashlib.sha256(blob).hexdigest()[:16]


@dataclass(frozen=True)
class PerturbationDataset:
    """A typed bundle: cell-state axis names + a list of perturbations + their observed
    responses + provenance. Converts to the adapter-layer :class:`PerturbationBatch` that
    backends consume. Carries a content fingerprint (provenance + data bytes)."""

    axis_names: tuple[str, ...]
    perturbations: tuple[Perturbation, ...]
    responses: tuple[ObservedResponse, ...]
    provenance: DatasetProvenance
    control_state: np.ndarray  # (dim,)

    def __post_init__(self) -> None:
        rmap = {r.pert_id for r in self.responses}
        pmap = {p.pert_id for p in self.perturbations}
        if rmap != pmap:
            raise ValueError(
                f"PerturbationDataset: perturbations vs responses id mismatch "
                f"(pert-only={sorted(pmap - rmap)}, resp-only={sorted(rmap - pmap)})"
            )

    @property
    def dim(self) -> int:
        return len(self.axis_names)

    def to_batch(self) -> PerturbationBatch:
        rmap = {r.pert_id: r for r in self.responses}
        ids = tuple(p.pert_id for p in self.perturbations)
        emb = np.stack([p.embedding for p in self.perturbations])
        deltas = np.stack([rmap[p.pert_id].delta for p in self.perturbations])
        return PerturbationBatch(pert_ids=ids, embeddings=emb, deltas=deltas)

    def subset(self, ids: list[str]) -> "PerturbationDataset":
        keep = set(ids)
        return PerturbationDataset(
            axis_names=self.axis_names,
            perturbations=tuple(p for p in self.perturbations if p.pert_id in keep),
            responses=tuple(r for r in self.responses if r.pert_id in keep),
            provenance=self.provenance,
            control_state=self.control_state,
        )

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.provenance.fingerprint().encode())
        for p in sorted(self.perturbations, key=lambda x: x.pert_id):
            h.update(p.pert_id.encode())
            h.update(p.embedding.tobytes())
        for r in sorted(self.responses, key=lambda x: x.pert_id):
            h.update(r.delta.tobytes())
        return "dataset:sha256:" + h.hexdigest()[:16]


# --------------------------------------------------------- causal claim (domain layer)

CausalStatus = Literal["supported", "rejected", "insufficient"]


@dataclass
class PerturbationCausalClaim:
    """A causal claim of the M27 shape: "``perturbation`` moves cell-state ``axis`` in
    ``direction`` (with ``effect_size``)". Mutable so a trusted observation can UPDATE it
    (charter DoD #5/#6). ``status`` starts ``insufficient``; only an observation (not a
    model) may move it (charter §4 -- models propose, trusted observations certify).

    ``evidence`` records the provenance chain (which dataset/observation moved it), and
    ``proposals`` records which backends *proposed* an effect (dry evidence, non-
    certifying) -- kept separate so a proposal can never masquerade as certification."""

    claim_id: str
    pert_id: str
    axis: str
    direction: Literal["up", "down", "none"] = "none"
    effect_size: float = 0.0
    status: CausalStatus = "insufficient"
    evidence: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
