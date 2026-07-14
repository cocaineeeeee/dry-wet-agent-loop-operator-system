"""``cell_free_expression_screen`` domain provider (M24 A-side): the THIRD domain, and
the FIRST structurally geometry-free one.

Consolidates the M24 biological-screen domain's scattered leaf tables into one
:class:`DomainProvider`, BY REFERENCE (imports the live module dicts; the originals stay
put as the regression anchor), exactly as ``catalyst_screen`` does -- but its
``compute_targets()`` returns ``sequence_construct`` ComputeTargets (Contract v3), NOT
molecular-geometry ones. There is no Z-matrix and none is fabricated: the dry input of a
construct is its sequence.

  * compute_targets <- ``adapters/dry/constructs.CONSTRUCTS`` (construct_id ->
                     {sequence, promoter, rbs, cds}), each wrapped in an
                     ``input_kind='sequence_construct'`` ComputeTarget whose
                     ``adapter_capability`` == INPUT_KIND_SEQUENCE_CONSTRUCT (consumed by
                     ``SequenceProxyAdapter``, whose ACCEPTS_INPUT_KINDS contains it).
  * wet_coords    <- ``adapters/dry/constructs.CONSTRUCT_DESCRIPTORS`` (already the
                     native {level: {coord: value}} descriptor shape).
  * truth_profiles<- ``adapters/wet/sim_reader.TRUTH_PROFILES`` (the expression faces:
                     expression_high + expression_flipped + the shared ``flat`` null).
  * seed_claims   -> the ``b_strongdesign`` family (the biological analogue of the
                     catalyst ``c_highcoord`` family): "strong-design constructs express
                     higher" (supported/higher, matching ``expression_high``) and its
                     rejected mirror (lower, matching ``expression_flipped``).

Cross-domain ``flat``: the coordinate-INDEPENDENT null face is domain-neutral (zero
amplitude collapses the Gaussian to the baseline whatever the coordinate means), so
``sim_reader``'s single ``flat`` entry serves chemistry AND biology; this provider lists
it among its faces and its null set, reading the SAME shared value (one source of truth).

Biology stays confined to this domain/provider/adapter/leaf layer: the kernel /
planner / evidence-compiler / claim-ledger never see a construct/promoter/rbs literal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from expos.adapters.domain_provider import (
    INPUT_KIND_SEQUENCE_CONSTRUCT,
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    SeedClaim,
)
from expos.adapters.dry.constructs import CONSTRUCT_DESCRIPTORS, CONSTRUCTS
from expos.adapters.wet.sim_reader import TRUTH_PROFILES

if TYPE_CHECKING:  # pragma: no cover
    from expos.domain import DomainConfig

#: This domain's categorical screening variable (yaml var whose choices must equal the
#: provider's construct set). Used by :meth:`validate_yaml`.
_SCREEN_VAR = "construct"

#: payload_schema_version stamped on a ``sequence_construct`` ComputeTarget. Bump
#: (additively) if the biology payload shape {sequence, promoter, rbs, cds} ever changes.
SEQUENCE_CONSTRUCT_SCHEMA_VERSION = "sequence_construct/1"

#: The expression faces of the shared ``TRUTH_PROFILES`` registry: the positive signal
#: face ``expression_high``, the flipped face ``expression_flipped``, and the
#: cross-domain shared ``flat`` null face. Consolidated by reference (values read live).
_EXPRESSION_FACES = ("expression_high", "expression_flipped", "flat")
_NULL_FACES = frozenset({"flat"})

#: The biological ``b_strongdesign`` seed family (the analogue of catalyst
#: ``c_highcoord``). Direction 'higher' matches the ``expression_high`` positive-sign
#: face: strong-design constructs (strong promoter/RBS + codon-optimized ORF) express
#: highest, so "strong-design wins" is the seeded prior the K-D discriminator tests; its
#: 'lower' mirror is the rejected claim that low-design constructs express higher
#: (the ``expression_flipped`` face).
_SEED_CLAIMS: tuple[SeedClaim, ...] = (
    SeedClaim(
        claim_id="b_strongdesign_expresses_higher",
        status="supported",
        direction="higher",
        statement="strong-design constructs give a higher plate-reader fluorescence",
    ),
    SeedClaim(
        claim_id="b_weakdesign_expresses_higher",
        status="rejected",
        direction="lower",
        statement="weak-design constructs give a higher plate-reader fluorescence",
    ),
)


def sequence_construct_target(
    target_id: str,
    sequence: str,
    promoter: str | None = None,
    rbs: str | None = None,
    cds: str | None = None,
) -> ComputeTarget:
    """Construct a biology ``ComputeTarget`` (``input_kind='sequence_construct'``) from a
    construct's sequence components -- the geometry-FREE analogue of
    :func:`molecular_geometry_target`. ``payload = {sequence, promoter, rbs, cds}`` (None
    components omitted); the required capability equals
    :data:`INPUT_KIND_SEQUENCE_CONSTRUCT` (what ``SequenceProxyAdapter`` accepts)."""
    payload: dict[str, object] = {"sequence": sequence}
    if promoter is not None:
        payload["promoter"] = promoter
    if rbs is not None:
        payload["rbs"] = rbs
    if cds is not None:
        payload["cds"] = cds
    return ComputeTarget(
        target_id=target_id,
        input_kind=INPUT_KIND_SEQUENCE_CONSTRUCT,
        payload=payload,
        payload_schema_version=SEQUENCE_CONSTRUCT_SCHEMA_VERSION,
        adapter_capability=INPUT_KIND_SEQUENCE_CONSTRUCT,
    )


class CellFreeExpressionScreenProvider(DomainProvider):
    """DomainProvider for the M24 cell-free-expression-screen domain (geometry-free)."""

    domain_name = "cell_free_expression_screen"

    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        # Biology domain: each construct level is a sequence_construct ComputeTarget
        # wrapping its {sequence, promoter, rbs, cds} -- NO molecular geometry.
        return {
            cid: sequence_construct_target(
                cid,
                sequence=comp["sequence"],
                promoter=comp.get("promoter"),
                rbs=comp.get("rbs"),
                cds=comp.get("cds"),
            )
            for cid, comp in CONSTRUCTS.items()
        }

    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
        # CONSTRUCT_DESCRIPTORS is already {level: {coord: value}} -- return a shallow
        # float-normalized copy in the same shape (keys == compute_targets keys).
        return {
            level: {k: float(v) for k, v in cmap.items()}
            for level, cmap in CONSTRUCT_DESCRIPTORS.items()
        }

    def truth_profiles(self) -> Mapping[str, float]:
        return {face: TRUTH_PROFILES[face] for face in _EXPRESSION_FACES}

    def null_profiles(self) -> frozenset[str]:
        return _NULL_FACES

    def seed_claims(self) -> Sequence[SeedClaim]:
        return _SEED_CLAIMS

    def validate_yaml(self, cfg: "DomainConfig") -> None:
        # The categorical screening variable's choices must equal the provider's
        # construct set (a yaml can neither declare a construct the provider cannot
        # realise nor omit one it can). LOUD. Kept inline (leaf discipline: this module
        # imports no domain/mcl symbol, so it does not reuse the chemistry providers'
        # shared helper -- the check is tiny and identical in shape).
        var = next(
            (v for v in cfg.design_space.variables if v.name == _SCREEN_VAR), None
        )
        if var is None:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: yaml design_space has no screening "
                f"variable {_SCREEN_VAR!r} (declared: "
                f"{sorted(v.name for v in cfg.design_space.variables)})"
            )
        choices = set(var.choices or ())
        levels = set(CONSTRUCTS)
        if choices != levels:
            raise DomainProviderError(
                f"domain {self.domain_name!r}: yaml {_SCREEN_VAR!r} choices must equal "
                f"the provider's constructs (yaml-only={sorted(choices - levels)}, "
                f"provider-only={sorted(levels - choices)})"
            )
