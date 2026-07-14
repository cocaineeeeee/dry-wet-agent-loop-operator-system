"""``solvent_screen`` domain provider (M21 A-side).

Consolidates the M16 solvent-screen domain's scattered tables into one
:class:`DomainProvider`, BY REFERENCE (imports the live module dicts; the originals
stay put as the regression anchor):

  * compute_targets <- ``adapters/dry/solvents.SOLVENTS`` (name -> (zmatrix, charge,
                     spin)), each projected into a ``molecular_geometry`` ComputeTarget
                     (Contract v3; the chemistry payload shape)
  * wet_coords    <- ``adapters/wet/screen.SOLVENT_POLARITY`` (flat {level: polarity},
                     lifted into the generic {level: {coord: value}} descriptor shape)
  * truth_profiles<- ``adapters/wet/sim_reader.TRUTH_PROFILES`` (the solvent faces:
                     polar_high / nonpolar_high / flat / polar_high_strong)
  * seed_claims   -> the built-in ``c_polar`` family (claim_id + status must stay in
                     sync with ``mcl._default_claims()``; guarded by
                     tests/test_domain_provider.py, since importing mcl here would
                     create a domain->provider->mcl->domain cycle)

Truth-leakage boundary is unchanged: a solvent's polarity is PUBLIC design
knowledge; the hidden truth (response-vs-polarity) lives only in the reader.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from expos.adapters.domain_provider import (
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    SeedClaim,
    molecular_geometry_target,
)
from expos.adapters.dry.solvents import SOLVENTS
from expos.adapters.wet.screen import SOLVENT_POLARITY
from expos.adapters.wet.sim_reader import TRUTH_PROFILES

if TYPE_CHECKING:  # pragma: no cover
    from expos.domain import DomainConfig

#: The coordinate-axis key inside a level's descriptor map (matches
#: ``screen.target_coord``'s default and ``mcl._COORD_NAME``).
_COORD_NAME = "coord"

#: This domain's categorical screening variable (the yaml var whose choices must
#: equal the provider's level tables). Used by :meth:`validate_yaml`.
_SCREEN_VAR = "solvent"

#: The solvent faces of the shared ``TRUTH_PROFILES`` registry. ``flat`` is the
#: cross-domain shared NULL face (both domains list it); the signal faces are the
#: solvent-specific ones. Consolidated by reference (values read live from the
#: shared registry, so a drift there is caught by the round-trip test).
_SOLVENT_FACES = ("polar_high", "nonpolar_high", "flat", "polar_high_strong")
_NULL_FACES = frozenset({"flat"})

#: The built-in ``c_polar`` seed family, in SeedClaim shape. claim_id + status are
#: transcribed from ``mcl._default_claims()`` and MUST stay identical to it (drift
#: anchor: tests/test_domain_provider.py asserts the equality); ``direction`` /
#: ``statement`` come from ``mcl._default_hypotheses`` + the higher/lower hyp-id
#: mapping. Defined inline (not imported from mcl) to keep this provider a leaf.
_SEED_CLAIMS: tuple[SeedClaim, ...] = (
    SeedClaim(
        claim_id="c_polar_responds_higher",
        status="supported",
        direction="higher",
        statement="polar solvents give a higher plate-reader response",
    ),
    SeedClaim(
        claim_id="c_nonpolar_responds_higher",
        status="rejected",
        direction="lower",
        statement="nonpolar solvents give a higher plate-reader response",
    ),
)


class SolventScreenProvider(DomainProvider):
    """DomainProvider for the M16 solvent-screen domain."""

    domain_name = "solvent_screen"

    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        # Chemistry domain: each solvent level is a molecular_geometry ComputeTarget
        # wrapping its (zmatrix, charge, spin) -- Contract v3's compatibility
        # projection (no leaked assumption that every domain has a geometry).
        return {
            name: molecular_geometry_target(name, zmat, charge, spin)
            for name, (zmat, charge, spin) in SOLVENTS.items()
        }

    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
        # SOLVENT_POLARITY is a flat {level: polarity}; lift into the generic
        # descriptor shape {level: {coord: value}} so every domain's wet leg is
        # prepared identically (the shape catalyst_screen already uses natively).
        return {
            level: {_COORD_NAME: float(polarity)}
            for level, polarity in SOLVENT_POLARITY.items()
        }

    def truth_profiles(self) -> Mapping[str, float]:
        return {face: TRUTH_PROFILES[face] for face in _SOLVENT_FACES}

    def null_profiles(self) -> frozenset[str]:
        return _NULL_FACES

    def seed_claims(self) -> Sequence[SeedClaim]:
        return _SEED_CLAIMS

    def validate_yaml(self, cfg: "DomainConfig") -> None:
        _validate_screen_choices(cfg, _SCREEN_VAR, set(SOLVENTS), self.domain_name)


def _validate_screen_choices(
    cfg: "DomainConfig", var_name: str, level_keys: set[str], domain_name: str
) -> None:
    """Shared domain-yaml check: the categorical screening variable's declared
    ``choices`` must equal the provider's level tables, so the yaml can never
    declare a level the provider cannot realise (nor omit one it can). LOUD."""
    var = next(
        (v for v in cfg.design_space.variables if v.name == var_name), None
    )
    if var is None:
        raise DomainProviderError(
            f"domain {domain_name!r}: yaml design_space has no screening variable "
            f"{var_name!r} (declared: {sorted(v.name for v in cfg.design_space.variables)})"
        )
    choices = set(var.choices or ())
    if choices != level_keys:
        raise DomainProviderError(
            f"domain {domain_name!r}: yaml {var_name!r} choices must equal the "
            f"provider's levels (yaml-only={sorted(choices - level_keys)}, "
            f"provider-only={sorted(level_keys - choices)})"
        )
