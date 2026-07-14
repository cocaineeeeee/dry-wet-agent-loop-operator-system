"""DomainProvider contract v2 tests (M21 A-side).

Covers: (a) ABC completeness -- a subclass missing a hook cannot be instantiated;
(b) round-trip drift anchors -- each provider hook reproduces the scattered legacy
tables verbatim (consolidation-by-reference, no drift); (c) cross-hook consistency
negative -- check_complete loudly rejects an inconsistent provider; (d)
provider_fingerprint stability -- same source => same value, one byte change =>
different value.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from expos.adapters.domain_provider import (
    INPUT_KIND_MOLECULAR_GEOMETRY,
    MOLECULAR_GEOMETRY_SCHEMA_VERSION,
    ComputeTarget,
    DomainProvider,
    DomainProviderError,
    DrySpecies,
    SeedClaim,
    adapter_accepts_capability,
    molecular_geometry_target,
)
from expos.adapters.dry.catalysts import CATALYST_DESCRIPTORS, CATALYSTS
from expos.adapters.dry.solvents import SOLVENTS
from expos.adapters.providers.catalyst_screen import CatalystScreenProvider
from expos.adapters.providers.solvent_screen import SolventScreenProvider
from expos.adapters.wet.screen import SOLVENT_POLARITY
from expos.adapters.wet.sim_reader import TRUTH_PROFILES

_COORD = "coord"


# --------------------------------------------------------------- (a) completeness


def test_missing_hook_subclass_cannot_instantiate():
    """A DomainProvider subclass that omits any of the five required hooks is
    abstract -- instantiating it raises TypeError at birth (free ABC governance)."""

    class Partial(DomainProvider):
        # implements only four of the five hooks (validate_yaml missing)
        def compute_targets(self):
            return {}

        def wet_coords(self):
            return {}

        def truth_profiles(self):
            return {"flat": 0.5}

        def seed_claims(self):
            return []

    with pytest.raises(TypeError):
        Partial()  # abstractmethod validate_yaml unimplemented


def test_both_providers_pass_check_complete():
    """The two real providers pass the birth-time completeness + cross-hook gate."""
    assert isinstance(SolventScreenProvider.check_complete(), SolventScreenProvider)
    assert isinstance(CatalystScreenProvider.check_complete(), CatalystScreenProvider)


# --------------------------------------------------------- (b) drift anchors


def test_solvent_provider_hooks_match_legacy_tables():
    p = SolventScreenProvider()

    # compute_targets reproduces SOLVENTS verbatim as molecular_geometry targets.
    targets = p.compute_targets()
    assert set(targets) == set(SOLVENTS)
    for name, (zmat, charge, spin) in SOLVENTS.items():
        assert targets[name] == molecular_geometry_target(name, zmat, charge, spin)
        assert targets[name].input_kind == INPUT_KIND_MOLECULAR_GEOMETRY

    # wet_coords lifts flat SOLVENT_POLARITY into the {level:{coord:value}} shape.
    wet = p.wet_coords()
    assert set(wet) == set(SOLVENT_POLARITY)
    for level, polarity in SOLVENT_POLARITY.items():
        assert wet[level] == {_COORD: float(polarity)}

    # truth_profiles are the solvent faces, read live from the shared registry.
    faces = p.truth_profiles()
    assert set(faces) == {"polar_high", "nonpolar_high", "flat", "polar_high_strong"}
    for face, mu in faces.items():
        assert mu == TRUTH_PROFILES[face]
    assert p.null_profiles() == frozenset({"flat"})


def test_catalyst_provider_hooks_match_legacy_tables():
    p = CatalystScreenProvider()

    targets = p.compute_targets()
    assert set(targets) == set(CATALYSTS)
    for name, (zmat, charge, spin) in CATALYSTS.items():
        assert targets[name] == molecular_geometry_target(name, zmat, charge, spin)
        assert targets[name].input_kind == INPUT_KIND_MOLECULAR_GEOMETRY

    # CATALYST_DESCRIPTORS is already {level:{coord:value}} -- reproduced verbatim.
    wet = p.wet_coords()
    assert set(wet) == set(CATALYST_DESCRIPTORS)
    for level, cmap in CATALYST_DESCRIPTORS.items():
        assert wet[level] == {k: float(v) for k, v in cmap.items()}

    faces = p.truth_profiles()
    assert set(faces) == {"catalyst_high", "flat"}
    for face, mu in faces.items():
        assert mu == TRUTH_PROFILES[face]
    assert p.null_profiles() == frozenset({"flat"})


def test_solvent_seed_claims_match_builtin_c_polar_family():
    """The provider's seed claims reproduce the built-in c_polar family
    (claim_id + status) held in mcl._default_claims() -- the no-drift anchor for the
    seed hook, which is defined inline in the provider to keep it a leaf module."""
    from expos.mcl import _default_claims

    got = {(c.claim_id, c.status) for c in SolventScreenProvider().seed_claims()}
    want = {(d["claim_id"], d["status"]) for d in _default_claims()}
    assert got == want
    # every seed claim is well-formed (direction in the allowed set)
    for c in SolventScreenProvider().seed_claims():
        assert c.direction in ("higher", "lower")


# ------------------------------------------- (b2) ComputeTarget shape (Contract v3)


def test_input_kind_constants_are_fixed_literals():
    """The input-kind vocabulary is the fixed set of literals both agents share."""
    from expos.adapters.domain_provider import (
        INPUT_KIND_SEQUENCE_CONSTRUCT,
        INPUT_KIND_SEQUENCE_FEATURES,
    )

    assert INPUT_KIND_MOLECULAR_GEOMETRY == "molecular_geometry"
    assert INPUT_KIND_SEQUENCE_CONSTRUCT == "sequence_construct"
    assert INPUT_KIND_SEQUENCE_FEATURES == "sequence_features"


def test_molecular_geometry_target_shape_roundtrip():
    """molecular_geometry_target packs (zmatrix,charge,spin) into a molecular_geometry
    ComputeTarget: payload keys/values, schema version, capability == input_kind."""
    ct = molecular_geometry_target("water", "O\nH 1 0.96\nH 1 0.96 2 104.5", charge=1, spin=2)
    assert isinstance(ct, ComputeTarget)
    assert ct.target_id == "water"
    assert ct.input_kind == INPUT_KIND_MOLECULAR_GEOMETRY
    assert ct.payload == {
        "zmatrix": "O\nH 1 0.96\nH 1 0.96 2 104.5",
        "charge": 1,
        "spin": 2,
    }
    assert ct.payload_schema_version == MOLECULAR_GEOMETRY_SCHEMA_VERSION
    # a target's required capability is drawn from the input-kind namespace, so an
    # adapter's ACCEPTS_INPUT_KINDS is directly comparable to it.
    assert ct.adapter_capability == INPUT_KIND_MOLECULAR_GEOMETRY
    assert ct.metadata == {}


def test_dry_species_compat_projection_equals_helper():
    """DrySpecies is retained as the chemistry payload shape only: its as_compute_target
    projection equals the standalone molecular_geometry_target constructor, and carries
    meta into ComputeTarget.metadata."""
    ds = DrySpecies(zmatrix="H\n", charge=0, spin=0, meta={"source": "unit-test"})
    projected = ds.as_compute_target("h_atom")
    assert projected == molecular_geometry_target(
        "h_atom", "H\n", 0, 0, metadata={"source": "unit-test"}
    )
    assert projected.metadata == {"source": "unit-test"}


def test_providers_compute_target_keys_equal_wet_coords_keys():
    """The v3 cross-hook invariant, checked directly on the real providers:
    compute_targets() keys == wet_coords() keys (domain-neutral level identity)."""
    for prov in (SolventScreenProvider(), CatalystScreenProvider()):
        assert set(prov.compute_targets()) == set(prov.wet_coords())


def test_pyscf_adapter_declares_molecular_geometry_capability():
    """Reference adapter-capability convention: PySCFDryAdapter accepts only
    molecular_geometry; the neutral probe honors ACCEPTS_INPUT_KINDS / accepts_capability."""
    from expos.adapters.domain_provider import INPUT_KIND_SEQUENCE_CONSTRUCT
    from expos.adapters.dry.adapter import PySCFDryAdapter

    assert PySCFDryAdapter.ACCEPTS_INPUT_KINDS == (INPUT_KIND_MOLECULAR_GEOMETRY,)
    assert PySCFDryAdapter.accepts_capability(INPUT_KIND_MOLECULAR_GEOMETRY)
    assert not PySCFDryAdapter.accepts_capability(INPUT_KIND_SEQUENCE_CONSTRUCT)

    adapter = PySCFDryAdapter()
    ct = molecular_geometry_target("h", "H\n")
    assert adapter_accepts_capability(adapter, ct.adapter_capability)
    assert not adapter_accepts_capability(adapter, INPUT_KIND_SEQUENCE_CONSTRUCT)


# --------------------------------------------- (c) cross-hook consistency negative


class _BadWetProvider(DomainProvider):
    """A provider whose wet_coords drops one compute_targets level -- the exact
    inconsistency screen._validate_polarity_table guards, now at contract level."""

    domain_name = "bad_wet"

    def compute_targets(self):
        return {
            "a": molecular_geometry_target("a", "H\n"),
            "b": molecular_geometry_target("b", "H\n"),
        }

    def wet_coords(self):
        return {"a": {_COORD: 0.5}}  # missing 'b'

    def truth_profiles(self):
        return {"flat": 0.5}

    def seed_claims(self):
        return []

    def validate_yaml(self, cfg):
        return None


def test_check_complete_rejects_wet_dry_level_mismatch():
    with pytest.raises(DomainProviderError, match="wet_coords levels must equal"):
        _BadWetProvider.check_complete()


class _BadDirectionProvider(_BadWetProvider):
    domain_name = "bad_direction"

    def wet_coords(self):
        return {"a": {_COORD: 0.5}, "b": {_COORD: 0.6}}

    def seed_claims(self):
        return [SeedClaim(claim_id="c_x", status="supported", direction="sideways")]


def test_check_complete_rejects_bad_seed_direction():
    with pytest.raises(DomainProviderError, match="direction"):
        _BadDirectionProvider.check_complete()


# ---------------------------------------------------- validate_yaml (domain check)


def test_validate_yaml_accepts_real_domain_yaml():
    """The real domain yamls declare screening choices equal to the provider tables,
    so validate_yaml passes (via load_domain)."""
    from expos.domain import load_domain

    SolventScreenProvider().validate_yaml(load_domain("domains/solvent_screen.yaml"))
    CatalystScreenProvider().validate_yaml(load_domain("domains/catalyst_screen.yaml"))


def test_validate_yaml_rejects_choice_mismatch():
    """A yaml whose screening variable declares a level the provider cannot realise
    is rejected loudly."""
    fake_cfg = SimpleNamespace(
        design_space=SimpleNamespace(
            variables=[
                SimpleNamespace(name="solvent", choices=["water", "unobtanium"]),
            ]
        )
    )
    with pytest.raises(DomainProviderError, match="choices must equal"):
        SolventScreenProvider().validate_yaml(fake_cfg)


# ------------------------------------------------ (d) provider_fingerprint stability

_FAKE_PROVIDER_SRC = '''
from expos.adapters.domain_provider import (
    DomainProvider,
    SeedClaim,
    molecular_geometry_target,
)


class FakeProvider(DomainProvider):
    domain_name = "fake"

    def compute_targets(self):
        return {"a": molecular_geometry_target("a", "H\\n")}

    def wet_coords(self):
        return {"a": {"coord": 0.5}}

    def truth_profiles(self):
        return {"flat": 0.5}

    def seed_claims(self):
        return [SeedClaim(claim_id="c_x", status="supported", direction="higher")]

    def validate_yaml(self, cfg):
        return None
'''


def _load_fake(tmp_path, mod_name, src):
    import sys

    path = tmp_path / f"{mod_name}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # as importlib.import_module does (real loader path)
    spec.loader.exec_module(mod)
    return mod.FakeProvider()


def _sha_part(fp: str) -> str:
    return fp.split("@sha256:", 1)[1]


def test_fingerprint_stable_for_same_source(tmp_path):
    prov = _load_fake(tmp_path, "fp_stable", _FAKE_PROVIDER_SRC)
    assert prov.provider_fingerprint() == prov.provider_fingerprint()


def test_fingerprint_changes_on_source_byte_change(tmp_path):
    p1 = _load_fake(tmp_path, "fp_v1", _FAKE_PROVIDER_SRC)
    # one-byte change: mu 0.5 -> 0.6 inside truth_profiles
    changed = _FAKE_PROVIDER_SRC.replace('{"flat": 0.5}', '{"flat": 0.6}')
    assert changed != _FAKE_PROVIDER_SRC
    p2 = _load_fake(tmp_path, "fp_v2", changed)
    assert _sha_part(p1.provider_fingerprint()) != _sha_part(p2.provider_fingerprint())
