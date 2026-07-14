"""``catalyst_screen`` domain provider (M21 A-side).

Consolidates the M20 catalyst-screen domain's scattered tables into one
:class:`DomainProvider`, BY REFERENCE (imports the live module dicts; the originals
stay put as the regression anchor):

  * compute_targets <- ``adapters/dry/catalysts.CATALYSTS`` (name -> (zmatrix, charge,
                     spin)), each projected into a ``molecular_geometry`` ComputeTarget
                     (Contract v3; the chemistry payload shape)
  * wet_coords    <- ``adapters/dry/catalysts.CATALYST_DESCRIPTORS`` (already the
                     native {level: {coord: value}} descriptor shape)
  * truth_profiles<- ``adapters/wet/sim_reader.TRUTH_PROFILES`` (the catalyst faces:
                     catalyst_high + the shared ``flat`` null face)
  * seed_claims   -> the ``c_highcoord`` family (the catalyst analogue of the seeded
                     "higher-coord-wins" claim; positive coord->response, matching the
                     ``catalyst_high`` face). catalyst_screen has no committed built-in
                     seed family (its yaml carries no ``seed_claims`` block yet), so
                     this is the provider-declared prior -- a DRAFT for B's loader line
                     to finalize (mailbox 116: contract form = draft, loader = final).

Cross-domain ``flat``: the polarity/coordinate-INDEPENDENT null face is domain-neutral
(zero amplitude collapses the Gaussian to the baseline regardless of what the
coordinate means physically), so ``sim_reader``'s single ``flat`` entry serves BOTH
domains. Each provider therefore lists ``flat`` among its faces and its null set;
they read the SAME shared ``TRUTH_PROFILES['flat']`` value, so there is one source of
truth, not a copy per domain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from expos.adapters.domain_provider import (
    ComputeTarget,
    DomainProvider,
    SeedClaim,
    molecular_geometry_target,
)
from expos.adapters.dry.catalysts import CATALYST_DESCRIPTORS, CATALYSTS
from expos.adapters.providers.solvent_screen import _validate_screen_choices
from expos.adapters.wet.sim_reader import TRUTH_PROFILES

if TYPE_CHECKING:  # pragma: no cover
    from expos.domain import DomainConfig

#: This domain's categorical screening variable (yaml var whose choices must equal
#: the provider's level tables). Used by :meth:`validate_yaml`.
_SCREEN_VAR = "catalyst"

#: The catalyst faces of the shared ``TRUTH_PROFILES`` registry: the signal face
#: ``catalyst_high`` (positive coord->response) + the cross-domain shared ``flat``
#: null face. Consolidated by reference (values read live from the shared registry).
_CATALYST_FACES = ("catalyst_high", "flat")
_NULL_FACES = frozenset({"flat"})

#: The catalyst ``c_highcoord`` seed family (DRAFT; no committed built-in exists for
#: this domain). Direction 'higher' matches the ``catalyst_high`` positive-sign face:
#: high-coordinate ligands respond highest, so "higher-coordinate wins" is the seeded
#: prior the K-D discriminator tests.
_SEED_CLAIMS: tuple[SeedClaim, ...] = (
    SeedClaim(
        claim_id="c_highcoord_responds_higher",
        status="supported",
        direction="higher",
        statement="high-coordinate ligands give a higher plate-reader response",
    ),
    SeedClaim(
        claim_id="c_lowcoord_responds_higher",
        status="rejected",
        direction="lower",
        statement="low-coordinate ligands give a higher plate-reader response",
    ),
)


class CatalystScreenProvider(DomainProvider):
    """DomainProvider for the M20 catalyst-screen domain."""

    domain_name = "catalyst_screen"

    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        # Chemistry domain: each catalyst level is a molecular_geometry ComputeTarget
        # wrapping its (zmatrix, charge, spin) -- Contract v3 compatibility projection.
        return {
            name: molecular_geometry_target(name, zmat, charge, spin)
            for name, (zmat, charge, spin) in CATALYSTS.items()
        }

    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
        # CATALYST_DESCRIPTORS is already {level: {coord: value}} -- return a shallow
        # copy in the same shape (float-normalized to match the contract type).
        return {
            level: {k: float(v) for k, v in cmap.items()}
            for level, cmap in CATALYST_DESCRIPTORS.items()
        }

    def truth_profiles(self) -> Mapping[str, float]:
        return {face: TRUTH_PROFILES[face] for face in _CATALYST_FACES}

    def null_profiles(self) -> frozenset[str]:
        return _NULL_FACES

    def seed_claims(self) -> Sequence[SeedClaim]:
        return _SEED_CLAIMS

    def validate_yaml(self, cfg: "DomainConfig") -> None:
        _validate_screen_choices(cfg, _SCREEN_VAR, set(CATALYSTS), self.domain_name)
