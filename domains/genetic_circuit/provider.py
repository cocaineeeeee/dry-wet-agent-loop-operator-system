"""``genetic_circuit`` DomainProvider (M26 v0.1) -- the FOURTH biology domain and the FIRST
whose phenotype is DYNAMIC (a time-series).

Structural twin of ``CellFreeExpressionScreenProvider``, but its ``compute_targets()`` returns
``circuit_topology`` ComputeTargets (a TYPED CIRCUIT GRAPH payload), NOT a sequence/geometry
one, and its truth faces are the DYNAMIC high/flipped/flat faces. It reuses the SAME
DomainProvider contract + birth-time governance (compute_targets keys == wet_coords keys, faces
non-empty, null declared, seed claims well-formed), so it passes ``check_complete`` domain-locally.

SEAM (docs/bio_seams/M26.md): the shared ``INPUT_KIND_CIRCUIT_TOPOLOGY`` vocabulary constant,
the ``circuit_topology`` ComputeTarget schema-version, and the dynamic-face registration in the
shared truth registry are B (integration-owner) items. Here the provider declares a LOCAL
capability string equal to ``circuit_topology`` (matching CircuitTopologyAdapter.ACCEPTS_INPUT_KINDS)
so governance + capability-probe are exercised now; B converges the literal into the shared vocab.

Biology / circuit semantics stay confined to this domain/provider/adapter layer: the kernel /
planner / evidence-compiler never see a promoter/toggle/Hill literal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from expos.adapters.domain_provider import (
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    SeedClaim,
)
from expos.adapters.wet.timeseries_reader import DYNAMIC_TRUTH_PROFILES

from .library import cassette_ladder, toggle_switch

if TYPE_CHECKING:  # pragma: no cover
    from expos.domain import DomainConfig

#: Local capability literal (SEAM: B folds into domain_provider.INPUT_KIND_*).
INPUT_KIND_CIRCUIT_TOPOLOGY = "circuit_topology"
CIRCUIT_TOPOLOGY_SCHEMA_VERSION = "circuit_topology/1"

_SCREEN_VAR = "circuit"

_DYNAMIC_FACES = ("dynamic_high", "dynamic_flipped", "dynamic_flat")
_NULL_FACES = frozenset({"dynamic_flat"})

#: The genetic-circuit seed family (analogue of the M24 b_strongdesign family): a
#: strong-design circuit reaches the higher settled dynamic phenotype (matches dynamic_high);
#: its rejected mirror is that a weak-design circuit does (matches dynamic_flipped).
_SEED_CLAIMS: tuple[SeedClaim, ...] = (
    SeedClaim(
        claim_id="gc_strongdesign_dynamic_higher",
        status="supported",
        direction="higher",
        statement="stronger-design circuits reach a higher settled dynamic phenotype",
    ),
    SeedClaim(
        claim_id="gc_weakdesign_dynamic_higher",
        status="rejected",
        direction="lower",
        statement="weaker-design circuits reach a higher settled dynamic phenotype",
    ),
)


def _circuit_catalogue() -> dict[str, tuple[float, dict]]:
    """circuit_id -> (design coord, serialised graph payload). The v0.1 candidate pool: the
    three-rung expression-cassette dose ladder + two toggle-switch tunings (the first dynamic
    milestone). All are public design knowledge (NOT truth)."""
    cat: dict[str, tuple[float, dict]] = {}
    for cid, coord, graph in cassette_ladder():
        cat[cid] = (coord, graph.to_payload())
    for coord in (1.0, 0.4):
        tg = toggle_switch(coord)
        cat[tg.circuit_id] = (coord, tg.to_payload())
    return cat


class GeneticCircuitProvider(DomainProvider):
    """DomainProvider for the M26 v0.1 genetic-circuit domain (dynamic phenotype)."""

    domain_name = "genetic_circuit"

    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        return {
            cid: ComputeTarget(
                target_id=cid,
                input_kind=INPUT_KIND_CIRCUIT_TOPOLOGY,
                payload=payload,
                payload_schema_version=CIRCUIT_TOPOLOGY_SCHEMA_VERSION,
                adapter_capability=INPUT_KIND_CIRCUIT_TOPOLOGY,
            )
            for cid, (_coord, payload) in _circuit_catalogue().items()
        }

    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
        return {
            cid: {"coord": float(coord)}
            for cid, (coord, _payload) in _circuit_catalogue().items()
        }

    def truth_profiles(self) -> Mapping[str, float]:
        return {face: DYNAMIC_TRUTH_PROFILES[face] for face in _DYNAMIC_FACES}

    def null_profiles(self) -> frozenset[str]:
        return _NULL_FACES

    def seed_claims(self) -> Sequence[SeedClaim]:
        return _SEED_CLAIMS

    def validate_yaml(self, cfg: "DomainConfig") -> None:
        var = next(
            (v for v in cfg.design_space.variables if v.name == _SCREEN_VAR), None
        )
        if var is None:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: yaml design_space has no screening variable "
                f"{_SCREEN_VAR!r} (declared: "
                f"{sorted(v.name for v in cfg.design_space.variables)})"
            )
        choices = set(var.choices or ())
        levels = set(_circuit_catalogue())
        if choices != levels:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: yaml {_SCREEN_VAR!r} choices must equal the "
                f"provider's circuits (yaml-only={sorted(choices - levels)}, "
                f"provider-only={sorted(levels - choices)})"
            )
