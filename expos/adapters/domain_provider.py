"""Domain provider contract v2 (M21 A-side): the explicit contract for "what a
domain must supply".

Today a new domain is "edit four places" (INDEX_M21_DOMAINPLUGIN §0): the dry
molecule/geometry table (``adapters/dry/*.py``), the wet ``level -> physical
coordinate`` descriptor table (``adapters/wet/screen.py`` / ``dry/catalysts.py``),
the hidden truth faces (``adapters/wet/sim_reader.py``), the seed-claim family
(``expos/mcl.py``), and the domain-yaml validation. This module fixes those five
seams into one contract so a third domain becomes "install one domain package".

Design (INDEX_M21_DOMAINPLUGIN §4/§6): borrow only pluggy's two innermost ideas
-- **spec/impl separation** (the contract here vs the concrete providers in
``adapters/providers/``) and **birth-time governance** (a provider that is
incomplete or internally inconsistent fails LOUDLY at load, never mid-run). We do
NOT depend on pluggy, do NOT use entry_points, and do NOT do 1:N multicall: an
expos run locks exactly one domain to exactly one provider (1:1).

The contract is a stdlib ``abc.ABC`` with five ``@abstractmethod`` hooks, so a
subclass that forgets a hook cannot even be instantiated (``TypeError`` at birth,
free of charge). Two concrete (non-hook) methods ride on top:

  * :meth:`DomainProvider.provider_fingerprint` -- the provenance hook B's
    ``config_fingerprint`` consumes (provider module path + source sha256), so a
    domain-implementation drift also trips resume drift-rejection (the dimension
    ``config_fingerprint`` currently misses -- see ``domain.py`` fingerprint note).
  * :meth:`DomainProvider.check_complete` -- the birth-time cross-hook consistency
    gate (wet_coords levels == compute_targets keys, non-empty truth faces, null
    faces are declared truth faces, seed claims well-formed). ABC already gives
    "missing hook => cannot instantiate"; this adds the cross-hook governance on top.

Dependency discipline: a provider imports ONLY leaf adapter tables
(``adapters/dry/*``, ``adapters/wet/*``) -- never ``expos.domain`` or ``expos.mcl``
-- so B can wire ``load_domain`` (in ``domain.py``) to import a provider without a
``domain -> provider -> mcl -> domain`` import cycle. This contract module itself
likewise imports no domain/mcl/kernel symbol.
"""

from __future__ import annotations

import abc
import hashlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence

from expos.errors import ExposError

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime import cycle
    from expos.domain import DomainConfig


class DomainProviderError(ExposError):
    """Loud rejection from the domain-provider contract (incomplete/inconsistent
    provider, or a yaml that a provider cannot realise). Load-time governance --
    it must never be swallowed into a silent fallback."""


# --------------------------------------------------------------------------- shapes

# -- input-kind vocabulary (M24 Contract v3) ---------------------------------
#
# The FIXED string literals a :class:`ComputeTarget` may declare as its
# ``input_kind``. Defined as module constants so BOTH agents (and every provider /
# adapter across the project) reference one vocabulary instead of re-typing the
# literals. The ``adapter_capability`` a target requires is drawn from this SAME
# namespace, so a dry adapter's ``ACCEPTS_INPUT_KINDS`` (see convention below) and a
# target's ``adapter_capability`` are directly comparable.
#
#   molecular_geometry -> chemistry payload {zmatrix, charge, spin} (PySCF dry leg)
#   sequence_construct -> biology "the dry leg really computes" payload
#                         {sequence, promoter, rbs, cds, parent_construct,
#                          sequence_version}
#   sequence_features  -> biology lighter payload {gc_fraction, cai, rbs_strength,
#                         folding_proxy, transcript_length}
INPUT_KIND_MOLECULAR_GEOMETRY = "molecular_geometry"
INPUT_KIND_SEQUENCE_CONSTRUCT = "sequence_construct"
INPUT_KIND_SEQUENCE_FEATURES = "sequence_features"

#: payload_schema_version stamped on a ``molecular_geometry`` ComputeTarget. Bump
#: (additively) if the chemistry payload shape {zmatrix, charge, spin} ever changes.
MOLECULAR_GEOMETRY_SCHEMA_VERSION = "molecular_geometry/1"


@dataclass(frozen=True)
class ComputeTarget:
    """One discrete design level's dry-leg input, GENERALIZED (M24 Contract v3): a
    domain-neutral request that "the dry leg compute a descriptor for this level".

    v2's ``DrySpecies`` baked the chemistry assumption "every design level has a
    molecular geometry" into the contract (``zmatrix`` was a required field on the
    one and only dry-input shape, forced by an ``@abstractmethod`` + a birth-time
    ``dry_keys == wet_keys`` gate). Biology -- the first structurally geometry-free
    domain -- had no Z-matrix to supply, exposing the leak. ``ComputeTarget`` fixes
    it: ``input_kind`` selects a payload SHAPE (molecular_geometry | sequence_* ),
    and ``DrySpecies`` demotes to merely *one* payload form (chemistry) rather than
    the contract's universal body.

    Fields:
      * ``target_id`` -- stable identity of this level (usually the level name).
      * ``input_kind`` -- one of the ``INPUT_KIND_*`` module constants; picks the
        ``payload`` shape.
      * ``payload`` -- the dry-input body, shaped per ``input_kind``
        (``molecular_geometry`` -> ``{zmatrix, charge, spin}``).
      * ``payload_schema_version`` -- version of the payload shape (drift/versioning
        hook; e.g. :data:`MOLECULAR_GEOMETRY_SCHEMA_VERSION`).
      * ``adapter_capability`` -- which dry-adapter capability this target needs
        (drawn from the ``INPUT_KIND_*`` namespace); an adapter whose
        ``ACCEPTS_INPUT_KINDS`` contains it can execute the target.
      * ``metadata`` -- optional free-form provenance (source, notes); not part of
        the load-bearing payload, excluded from drift-anchor comparison by callers.
    """

    target_id: str
    input_kind: str
    payload: Mapping[str, object]
    payload_schema_version: str
    adapter_capability: str
    metadata: Mapping[str, object] = field(default_factory=dict)


def molecular_geometry_target(
    target_id: str,
    zmatrix: str,
    charge: int = 0,
    spin: int = 0,
    *,
    metadata: Mapping[str, object] | None = None,
) -> ComputeTarget:
    """Construct a chemistry ``ComputeTarget`` (``input_kind='molecular_geometry'``)
    from a ``(zmatrix, charge, spin)`` geometry -- the compatibility projection that
    keeps the two chemistry domains (solvent/catalyst) expressible under Contract v3
    without inventing a new shape. ``payload = {zmatrix, charge, spin}``; the required
    capability equals :data:`INPUT_KIND_MOLECULAR_GEOMETRY`."""
    return ComputeTarget(
        target_id=target_id,
        input_kind=INPUT_KIND_MOLECULAR_GEOMETRY,
        payload={"zmatrix": zmatrix, "charge": int(charge), "spin": int(spin)},
        payload_schema_version=MOLECULAR_GEOMETRY_SCHEMA_VERSION,
        adapter_capability=INPUT_KIND_MOLECULAR_GEOMETRY,
        metadata=dict(metadata or {}),
    )


@dataclass(frozen=True)
class DrySpecies:
    """A small-molecule geometry (``zmatrix`` + charge/spin) that the dry (PySCF) leg
    turns into a descriptor (dipole proxy) carrying REAL method error. Mirrors the
    ``(zmatrix, charge, spin)`` triples in ``adapters/dry/solvents.py`` /
    ``adapters/dry/catalysts.py`` (``spin`` = 2S).

    Under Contract v3 ``DrySpecies`` is **no longer the universal contract body** --
    it is the *chemistry* payload shape only. Use :meth:`as_compute_target` (or
    :func:`molecular_geometry_target`) to project it into the neutral
    :class:`ComputeTarget` the ``compute_targets()`` hook returns.

    ``meta`` carries optional free-form provenance (source, notes); it is not part
    of the load-bearing geometry and is excluded from the drift-anchor comparison.
    """

    zmatrix: str
    charge: int = 0
    spin: int = 0
    meta: Mapping[str, object] = field(default_factory=dict)

    def as_compute_target(self, target_id: str) -> ComputeTarget:
        """Project this chemistry geometry into a ``molecular_geometry``
        :class:`ComputeTarget` (the v3 compatibility projection). ``meta`` rides
        along as ``ComputeTarget.metadata``."""
        return molecular_geometry_target(
            target_id, self.zmatrix, self.charge, self.spin, metadata=self.meta
        )


# -- dry-adapter capability convention (M24 Contract v3) ---------------------
#
# CONVENTION (for B's mcl dry-leg dispatch): a dry adapter DECLARES the input kinds
# it can execute via a class attribute
#
#     ACCEPTS_INPUT_KINDS: tuple[str, ...] = (INPUT_KIND_MOLECULAR_GEOMETRY, ...)
#
# and exposes ``accepts_capability(kind) -> bool`` (default = ``kind in
# ACCEPTS_INPUT_KINDS``). :func:`adapter_accepts_capability` below is the neutral
# reader B's mcl calls to pick an adapter for a ComputeTarget:
#
#     adapter_accepts_capability(dry_adapter, target.adapter_capability)  # -> bool
#
# Reference impl: ``PySCFDryAdapter.ACCEPTS_INPUT_KINDS = (
# INPUT_KIND_MOLECULAR_GEOMETRY,)`` (adapters/dry/adapter.py). A SequenceProxyAdapter
# would declare ``(INPUT_KIND_SEQUENCE_CONSTRUCT, INPUT_KIND_SEQUENCE_FEATURES)``.
# The contract layer only DEFINES the convention; it does NOT dispatch (that is B's
# mcl work) -- so no domain/mcl import is pulled in here.


def adapter_accepts_capability(adapter: object, capability: str) -> bool:
    """Neutral capability probe following the ``ACCEPTS_INPUT_KINDS`` convention:
    honor an explicit ``accepts_capability`` method if the adapter defines one, else
    fall back to membership in ``ACCEPTS_INPUT_KINDS`` (empty tuple if undeclared)."""
    fn = getattr(adapter, "accepts_capability", None)
    if callable(fn):
        return bool(fn(capability))
    return capability in tuple(getattr(adapter, "ACCEPTS_INPUT_KINDS", ()))


@dataclass(frozen=True)
class SeedClaim:
    """A domain's prior ledger entry -- the K-D discriminator's "hypothesis under
    test" entry point. Field names are transcribed verbatim from
    ``expos.domain.SeedClaimSpec`` (claim_id / statement / status / direction) so B's
    yaml ``seed_claims`` block and this provider hook stay a single vocabulary.

    ``direction`` ('higher'|'lower') is the acquisition direction the claim asserts
    (consumed by mcl to steer preference); it is validated by
    :meth:`DomainProvider.check_complete`.
    """

    claim_id: str
    status: str
    direction: str
    statement: str = ""


# ----------------------------------------------------------------------- contract


class DomainProvider(abc.ABC):
    """The five-hook contract a domain package must implement, plus two concrete
    governance methods. Providers are zero-arg constructible (they consolidate
    static tables by reference; see ``adapters/providers/``)."""

    #: Human-facing domain name (the ``name:`` field of the domain yaml). Subclasses
    #: set this; used only in error messages / fingerprints.
    domain_name: str = "<unnamed-domain>"

    # -- the five required hooks (INDEX_M21_DOMAINPLUGIN §4) -------------------

    @abc.abstractmethod
    def compute_targets(self) -> Mapping[str, ComputeTarget]:
        """level -> :class:`ComputeTarget`: the dry leg's discrete-level input table
        (INDEX §0 #1), GENERALIZED in Contract v3 from the chemistry-only
        ``dry_species()`` (name -> DrySpecies geometry) to a domain-neutral
        ``ComputeTarget`` whose ``input_kind`` selects the payload shape. Chemistry
        domains return ``molecular_geometry`` targets (see
        :func:`molecular_geometry_target` / :meth:`DrySpecies.as_compute_target`); a
        geometry-free domain (e.g. biology) returns ``sequence_*`` targets without
        fabricating a Z-matrix."""

    @abc.abstractmethod
    def wet_coords(self) -> Mapping[str, Mapping[str, float]]:
        """level -> {coord_name: value}: the "categorical level -> physical
        coordinate" descriptor table the wet leg realises (INDEX §0 #2). Always the
        nested ``{level: {coord: value}}`` descriptor shape (``screen.target_coord``
        input), even when the underlying legacy table is flat."""

    @abc.abstractmethod
    def truth_profiles(self) -> Mapping[str, float]:
        """face name -> mu (peak coordinate) for this domain's hidden truth faces
        (INDEX §0 #3). Values are consumed ONLY inside the reader/server process;
        the planner/qc-facing view must never see them. Null (no-signal) faces are
        reported separately by :meth:`null_profiles`."""

    @abc.abstractmethod
    def seed_claims(self) -> Sequence[SeedClaim]:
        """The domain's prior claim ledger (INDEX §0 #4). Field names align with
        ``domain.SeedClaimSpec``."""

    @abc.abstractmethod
    def validate_yaml(self, cfg: "DomainConfig") -> None:
        """Domain-specialized yaml validation (INDEX §0 #5): raise
        :class:`DomainProviderError` LOUDLY on any domain-specific violation (e.g. a
        declared screening level the provider's tables cannot realise). ``None`` on
        success. ``cfg`` is duck-typed (a ``domain.DomainConfig``) so this module
        needs no runtime import of ``expos.domain``."""

    # -- optional hook (INDEX §4 "5 required + optional") ---------------------

    def null_profiles(self) -> frozenset[str]:
        """The subset of :meth:`truth_profiles` faces whose amplitude is zeroed (a
        coordinate-INDEPENDENT no-signal face; mirrors
        ``sim_reader._NULL_PROFILES``). Default: none. Providers override to declare
        their null faces (both current domains share ``flat``)."""
        return frozenset()

    # -- concrete governance methods (NOT hooks) ------------------------------

    def provider_fingerprint(self) -> str:
        """Provenance token = provider module path + sha256 of the provider module's
        source. Stable across calls for one source; any byte change flips it. B's
        ``config_fingerprint`` folds this in so a domain-implementation drift trips
        resume drift-rejection (the dimension ``domain.config_fingerprint`` currently
        misses). Format: ``<module>:<qualname>@sha256:<hex>``."""
        cls = type(self)
        module = sys.modules.get(cls.__module__)
        src_path = inspect.getsourcefile(cls) or getattr(module, "__file__", None)
        if src_path is None:
            raise DomainProviderError(
                f"cannot locate source of provider {cls.__module__}.{cls.__qualname__} "
                "to compute its fingerprint"
            )
        source = Path(src_path).read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        return f"{cls.__module__}:{cls.__qualname__}@sha256:{digest}"

    @classmethod
    def check_complete(cls) -> "DomainProvider":
        """Birth-time completeness + cross-hook consistency gate. Instantiating the
        ABC already enforces "every required hook implemented" (missing => TypeError
        at construction). This adds the cross-hook invariants that no single hook can
        see:

          * ``wet_coords()`` level set == ``compute_targets()`` key set (the
            load-bearing invariant ``screen._validate_polarity_table`` enforces
            today, lifted to contract level: a level the dry leg can compute is one
            the wet leg can prepare, and vice versa). Domain-neutral: it compares
            level KEYS, indifferent to whether the dry input is a geometry or a
            sequence.
          * ``truth_profiles()`` is non-empty.
          * every ``null_profiles()`` face is a declared ``truth_profiles()`` face.
          * every ``seed_claims()`` entry has a non-empty ``claim_id`` and a
            ``direction`` in {'higher','lower'}.

        Returns the validated instance (handy for load-time wiring). Any violation is
        a LOUD :class:`DomainProviderError`."""
        self = cls()  # ABC: raises TypeError here if any hook is unimplemented
        name = getattr(cls, "domain_name", cls.__qualname__)

        target_keys = set(self.compute_targets())
        wet_keys = set(self.wet_coords())
        if target_keys != wet_keys:
            raise DomainProviderError(
                f"domain {name!r}: wet_coords levels must equal compute_targets keys "
                f"(dry-only={sorted(target_keys - wet_keys)}, "
                f"wet-only={sorted(wet_keys - target_keys)})"
            )

        faces = self.truth_profiles()
        if not faces:
            raise DomainProviderError(
                f"domain {name!r}: truth_profiles() is empty (need >=1 hidden face)"
            )

        stray_null = set(self.null_profiles()) - set(faces)
        if stray_null:
            raise DomainProviderError(
                f"domain {name!r}: null_profiles {sorted(stray_null)} are not "
                f"declared truth faces {sorted(faces)}"
            )

        for sc in self.seed_claims():
            if not sc.claim_id:
                raise DomainProviderError(
                    f"domain {name!r}: a seed claim has an empty claim_id"
                )
            if sc.direction not in ("higher", "lower"):
                raise DomainProviderError(
                    f"domain {name!r}: seed claim {sc.claim_id!r} has direction "
                    f"{sc.direction!r} (must be 'higher' or 'lower')"
                )
        return self
