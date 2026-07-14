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

from typing import TYPE_CHECKING, Mapping, Sequence

from expos.adapters.domain_provider import (
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    SeedClaim,
)

from domains.perturbation.objects import (
    CELL_STATE_PERTURBATION_SCHEMA_VERSION,
    INPUT_KIND_CELL_STATE_PERTURBATION,
)

if TYPE_CHECKING:  # pragma: no cover
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


class PerturbationScreenProvider(DomainProvider):
    """DomainProvider for the M27 perturbation-biology domain (cell-state + knockout)."""

    domain_name = "perturbation_screen"

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
