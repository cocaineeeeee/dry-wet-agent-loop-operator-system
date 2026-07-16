"""``generative_construct`` domain provider (M25): the LOADABLE :class:`DomainProvider`
for the Team-M25 "Design" organ (geometry-free, sequence-construct dry leg).

This is the birth-time-governed, ``load_domain``-visible face of the M25 organ. The
generation / lineage / acquisition / simulated-wet LOGIC already lives, domain-local
and fully tested, under ``domains/generative_construct/`` (objects / generation /
acquisition / wet_truth / e2e). This module is the thin *contract* layer B loads: it
consolidates the same leaf tables the M24 sequence dry leg already screens (so the
loadable base pool is byte-identical to ``cell_free_expression_screen``) and adds the
ONE generative seam B folds -- :meth:`propose_candidates` (parent level -> child
``sequence_construct`` ComputeTargets) plus :meth:`operator_fingerprint` (the operator
source hash B folds into ``config_fingerprint`` when generated candidates are used).

Why the loadable base equals the M24 catalogue: a domain's ``design_space`` is a FIXED
categorical (the 11 public catalogue presets), and its ``compute_targets`` /
``wet_coords`` must agree on that level set at birth (``check_complete``). The
generative growth is a RUNTIME act -- B calls :meth:`propose_candidates` each round to
expand a parent into an operator child pool that is merged into the candidate pool
(docs/bio_seams/M25.md seam #1). The catalogue presets are the lineage ROOTS; the
children never appear in the yaml (they are minted per round, content-addressed).

Reuse (consolidation-by-reference, exactly as ``cell_free_expression_screen``):
  * compute_targets  <- ``adapters/dry/constructs.CONSTRUCTS`` wrapped as
                       ``sequence_construct`` ComputeTargets (the geometry-free target
                       builder is shared with the M24 provider; NO Z-matrix).
  * wet_coords       <- ``adapters/dry/constructs.CONSTRUCT_DESCRIPTORS``.
  * truth_profiles   <- ``adapters/wet/sim_reader.TRUTH_PROFILES`` (the expression
                       faces + the shared ``flat`` null); the M25 simulated wet
                       (``domains/generative_construct/wet_truth.py``) realises the
                       SAME faces on the SAME shared ``TruthSurface``.
  * seed_claims      -> the ``b_strongdesign`` biological family (strong-design
                       expresses higher / its rejected mirror) -- the seeded prior the
                       generative proposer's designs are screened against.

Dependency discipline (domain_provider.py): this module imports ONLY leaf adapter
tables and the M25 organ package (which itself imports no ``expos.domain`` / ``expos.
mcl`` symbol), so ``load_domain`` can import it without a domain -> provider -> mcl ->
domain cycle. Biology stays confined to this domain/provider/adapter/leaf layer.

HONEST LABEL: the M25 wet phenotype is a domain-local SIMULATION (wet_truth.py); the
real wet path swaps in the socket ``sim_reader`` truth face (the faces already exist).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from expos.adapters.domain_provider import (
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    SeedClaim,
)
from expos.adapters.dry.constructs import CONSTRUCT_DESCRIPTORS, CONSTRUCTS
from expos.adapters.dry.mutation_operators import module_fingerprint
from expos.adapters.wet.sim_reader import TRUTH_PROFILES

# Reuse the M24 geometry-free target builder + payload schema version (one source of
# truth for the ``sequence_construct`` payload shape {sequence, promoter, rbs, cds}).
from expos.adapters.providers.cell_free_expression_screen import (
    SEQUENCE_CONSTRUCT_SCHEMA_VERSION,
    sequence_construct_target,
)

if TYPE_CHECKING:  # pragma: no cover
    from expos.domain import DomainConfig

#: The yaml categorical screening variable whose choices must equal the provider's
#: preset set (used by :meth:`validate_yaml`). Same name/role as the M24 domain.
_SCREEN_VAR = "construct"

#: The expression faces of the shared ``TRUTH_PROFILES``: the positive signal face, the
#: flipped face, and the cross-domain shared ``flat`` null (read live, by reference).
_EXPRESSION_FACES = ("expression_high", "expression_flipped", "flat")
_NULL_FACES = frozenset({"flat"})

#: The biological ``b_strongdesign`` seed family (analogue of catalyst ``c_highcoord``),
#: identical to the M24 screen's: strong-design constructs express higher (supported /
#: higher, matching ``expression_high``) and its rejected 'lower' mirror. This is the
#: seeded prior the generative proposer's designs are screened against.
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


def _target_for_components(target_id: str, components: Mapping[str, str]) -> ComputeTarget:
    """Wrap a construct's ``{sequence, promoter, rbs, cds}`` components as a
    geometry-free ``sequence_construct`` ComputeTarget (the shared M24 builder)."""
    return sequence_construct_target(
        target_id,
        sequence=components["sequence"],
        promoter=components.get("promoter"),
        rbs=components.get("rbs"),
        cds=components.get("cds"),
    )


class GenerativeConstructProvider(DomainProvider):
    """Loadable DomainProvider for the M25 generative-construct domain (geometry-free).

    Distinct from ``domains.generative_construct.provider.GenerativeConstructProvider``
    (the domain-local *organ* object holding the generation/acquisition logic): this is
    the birth-time-governed *contract* face ``load_domain`` imports. It reuses the same
    leaf tables and delegates the generative seam to the organ package."""

    domain_name = "generative_construct"

    #: The M25 generator identity B stamps into ``DesignProvenance`` so a generated
    #: candidate reads ``generator != mcl_template_agent`` (docs/bio_seams/M25.md #3).
    generator_id = "generative_construct/v0.1"

    # -- the five required DomainProvider hooks --------------------------------

    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        # Loadable base pool = the 11 catalogue presets (lineage roots), each a
        # sequence_construct ComputeTarget. Generated children are minted at RUNTIME
        # by ``propose_candidates`` (never in the yaml).
        return {
            cid: _target_for_components(cid, comp) for cid, comp in CONSTRUCTS.items()
        }

    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
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
        # The categorical screening variable's choices must equal the provider's preset
        # set (the loadable base pool). LOUD. Same shape as the M24 provider's check.
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

    # -- generative seam (B folds this per round; docs/bio_seams/M25.md #1/#5) --

    def propose_candidates(
        self,
        parent_target_id: str,
        *,
        seed: int = 0,
        parent_components: Mapping[str, str] | None = None,
    ) -> Mapping[str, ComputeTarget]:
        """Expand a parent level into a pool of CHILD ``sequence_construct``
        ComputeTargets -- the per-round fold B merges into the mcl candidate pool.

        ``parent_target_id`` is a catalogue preset id (the default: a lineage root),
        or any prior candidate id when ``parent_components`` supplies its components
        (so B can grow deeper generations). Deterministic given ``(parent, seed)``.
        Children are content-addressed and NEVER reuse the parent/observation id
        (M24-B technical-replicate discipline); the child payload is the SAME
        ``{sequence, promoter, rbs, cds}`` ``sequence_construct`` kind M24 already
        routes to ``SequenceProxyAdapter`` -- no new input_kind.

        Delegates the operator expansion to the domain-local organ
        (``domains.generative_construct.generation.generate_children``); this module
        only wraps the resulting components as ComputeTargets."""
        # Local imports keep the organ (and its expos.kernel imports) off the
        # load_domain import path unless the generative seam is actually exercised.
        from domains.generative_construct.generation import generate_children, seed_design
        from domains.generative_construct.objects import ConstructDesign

        if parent_components is None:
            parent = seed_design(parent_target_id)
        else:
            parent = ConstructDesign(
                design_id=parent_target_id,
                components=dict(parent_components),
                origin="parent",
                proxy=None,
                generation=0,
            )
        children = generate_children(parent, seed=seed)
        return {
            child.design_id: _target_for_components(child.design_id, child.components)
            for child in children
        }

    def operator_fingerprint(self) -> str:
        """Source hash of the five mutation operators. B folds this into
        ``config_fingerprint`` WHEN generated candidates are used, so a change to any
        operator flips run identity (docs/bio_seams/M25.md #5). This is SEPARATE from
        :meth:`provider_fingerprint` (this provider module's own hash): the operators
        live in ``adapters/dry/mutation_operators.py``, imported here by reference."""
        return module_fingerprint()


#: Re-export the shared payload schema version so B (and tests) can reference the
#: ``sequence_construct`` payload shape from one import site.
__all__ = ["GenerativeConstructProvider", "SEQUENCE_CONSTRUCT_SCHEMA_VERSION"]
