"""M21 B-side wave-2 tests: the domain-provider loading line + fingerprint fold.

Covers the consumption of A-side's landed ``DomainProvider`` contract (mailbox 120)
from B-side ``expos.domain``:

  * the loading line -- ``provider:`` yaml field -> importlib -> ``check_complete()``
    -> ``validate_yaml(cfg)`` (provider loads; validate_yaml is actually called);
  * loud rejection of a non-``expos.`` module path and of a bogus import path;
  * ``provider_fingerprint`` folded into ``config_fingerprint`` (declaring a provider
    changes the fingerprint; it is stable across loads);
  * byte-identity: removing the ``provider:`` field loads exactly as before, minus
    that one field (the fold is the ONLY change a provider introduces);
  * the two assertions promised in mailbox 113: (1) providers never import
    ``expos.domain``/``expos.mcl`` (no import cycle), (2) both providers' ``flat``
    truth face is the SAME shared object as ``TRUTH_PROFILES['flat']`` (single source).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from expos.adapters.domain_provider import DomainProviderError
from expos.adapters.providers.catalyst_screen import CatalystScreenProvider
from expos.adapters.providers.solvent_screen import SolventScreenProvider
from expos.adapters.wet.sim_reader import TRUTH_PROFILES
from expos.domain import (
    DomainConfig,
    DomainError,
    config_fingerprint,
    load_domain,
    load_provider,
)

_REPO = Path(__file__).resolve().parents[1]
_SOLVENT_YAML = _REPO / "domains" / "solvent_screen.yaml"
_CATALYST_YAML = _REPO / "domains" / "catalyst_screen.yaml"


def _write_variant(tmp_path: Path, base: Path, mutate) -> Path:
    """Load ``base`` yaml as a raw dict, apply ``mutate(raw)`` in place, write it to a
    temp file, and return the path (so ``load_domain`` runs its full real pipeline on
    the variant)."""
    raw = yaml.safe_load(base.read_text(encoding="utf-8"))
    mutate(raw)
    out = tmp_path / base.name
    out.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return out


# --------------------------------------------------------------- loading line


def test_provider_loads_and_validate_yaml_is_called(monkeypatch):
    """A declaring yaml loads its provider; the loading line actually calls
    ``inst.validate_yaml(cfg)`` with the loaded DomainConfig (spy)."""
    seen: list[object] = []
    real_validate = SolventScreenProvider.validate_yaml

    def spy(self, cfg):
        seen.append(cfg)
        return real_validate(self, cfg)

    monkeypatch.setattr(SolventScreenProvider, "validate_yaml", spy)
    cfg = load_domain(_SOLVENT_YAML)
    assert isinstance(cfg._provider, SolventScreenProvider)
    assert len(seen) == 1 and seen[0] is cfg  # validate_yaml called once, with cfg


def test_bogus_config_makes_provider_reject_loudly(tmp_path):
    """A yaml whose screening choices the provider cannot realise fails LOUD via the
    provider's ``validate_yaml`` (birth-time governance, not a silent fallback)."""

    def mutate(raw):
        # add a screening choice the provider's SOLVENTS table cannot realise (the
        # sentinel solvent stays valid, so this reaches the provider's validate_yaml
        # rather than tripping design-space/sentinel validation first).
        raw["design_space"]["variables"][0]["choices"].append("unobtainium")

    variant = _write_variant(tmp_path, _SOLVENT_YAML, mutate)
    with pytest.raises(DomainProviderError):
        load_domain(variant)


def test_non_expos_provider_path_rejected_loudly():
    """A provider module path outside the ``expos.`` package is refused before any
    import (no filesystem/entry_point discovery)."""
    cfg = load_domain(_SOLVENT_YAML)
    off_tree = cfg.model_copy(update={"provider": "os.path:join"})
    with pytest.raises(DomainError, match="expos."):
        load_provider(off_tree)


def test_bogus_module_path_rejected_loudly():
    """An ``expos.``-prefixed but non-importable module path is a loud DomainError."""
    cfg = load_domain(_SOLVENT_YAML)
    bogus = cfg.model_copy(update={"provider": "expos.adapters.providers.nope:Foo"})
    with pytest.raises(DomainError, match="could not be imported"):
        load_provider(bogus)


def test_bogus_class_name_rejected_loudly():
    """A real module but a missing class name is a loud DomainError."""
    cfg = load_domain(_SOLVENT_YAML)
    bad = cfg.model_copy(
        update={"provider": "expos.adapters.providers.solvent_screen:NoSuchClass"}
    )
    with pytest.raises(DomainError, match="not found in module"):
        load_provider(bad)


@pytest.mark.parametrize("bad", ["no_colon", "a:b:c", "expos.mod:", ":Cls"])
def test_malformed_provider_locator_rejected_at_field(bad):
    """The ``provider`` field validator rejects a malformed ``<module>:<Class>``
    locator loudly at validation (before any import)."""
    from pydantic import ValidationError

    valid = load_domain(_SOLVENT_YAML).model_dump()
    with pytest.raises(ValidationError):
        DomainConfig.model_validate({**valid, "provider": bad})


# --------------------------------------------------------- fingerprint fold


def test_provider_fingerprint_folded_into_config_fingerprint(tmp_path):
    """Declaring a provider changes ``config_fingerprint`` (the source hash is folded
    in); removing it returns to the provider-less fingerprint; both are stable."""
    with_provider = load_domain(_SOLVENT_YAML)
    fp_with = config_fingerprint(with_provider)

    def drop_provider(raw):
        raw.pop("provider", None)

    no_provider_yaml = _write_variant(tmp_path, _SOLVENT_YAML, drop_provider)
    without = load_domain(no_provider_yaml)
    assert without._provider is None
    fp_without = config_fingerprint(without)

    # the provider source hash makes the fingerprints differ ...
    assert fp_with != fp_without
    # ... and each is stable across an independent reload.
    assert config_fingerprint(load_domain(_SOLVENT_YAML)) == fp_with
    assert config_fingerprint(load_domain(no_provider_yaml)) == fp_without


def test_provider_drift_flips_fingerprint():
    """A byte change in the provider source flips ``provider_fingerprint`` and thus
    the folded ``config_fingerprint`` (drift-rejection dimension)."""
    cfg = load_domain(_SOLVENT_YAML)
    base = config_fingerprint(cfg)
    # simulate a source drift by swapping in a fingerprint that differs by one byte
    real_fp = cfg._provider.provider_fingerprint()

    class _Drifted:
        def provider_fingerprint(self):
            return real_fp[:-1] + ("0" if real_fp[-1] != "0" else "1")

    cfg._provider = _Drifted()
    assert config_fingerprint(cfg) != base


def test_removing_provider_is_byte_identical_minus_the_field(tmp_path):
    """Byte-identity gate: a yaml with ``provider:`` removed loads to exactly the same
    DomainConfig as the declaring yaml, MINUS the ``provider`` field itself. Declaring
    a provider changes nothing else about the loaded config."""
    with_provider = load_domain(_SOLVENT_YAML)

    def drop_provider(raw):
        raw.pop("provider", None)

    without = load_domain(_write_variant(tmp_path, _SOLVENT_YAML, drop_provider))

    dump_with = with_provider.model_dump(mode="json", exclude={"provider"})
    dump_without = without.model_dump(mode="json", exclude={"provider"})
    assert dump_with == dump_without
    # and the provider-less dump equals the provider-less fingerprint base material,
    # i.e. config_fingerprint of the provider-less load is the pure-material hash.
    assert without.provider is None


def test_provider_less_domains_unchanged(tmp_path):
    """A provider-less domain's fingerprint excludes the (absent) provider field, so
    it is byte-identical to the pure model_dump hash -- crystal/coating/flipped are
    untouched by this wave."""
    import hashlib
    import json

    for name in ("crystal", "coating", "solvent_screen_flipped"):
        cfg = load_domain(_REPO / "domains" / f"{name}.yaml")
        assert cfg.provider is None and cfg._provider is None
        material = json.dumps(
            cfg.model_dump(mode="json", exclude={"provider"}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert config_fingerprint(cfg) == hashlib.sha256(material).hexdigest()


def test_stored_dict_fingerprint_drops_provider_key():
    """The dict path of ``config_fingerprint`` (a stored ``domain_config`` from
    config.json) drops a serialized ``provider`` key so it matches the provider-less
    base material -- an old stored dict without the key and a new one with it hash the
    same base."""
    cfg = load_domain(_SOLVENT_YAML)
    dumped = cfg.model_dump(mode="json")
    with_key = dict(dumped)  # carries provider: "expos...."
    without_key = {k: v for k, v in dumped.items() if k != "provider"}
    assert config_fingerprint(with_key) == config_fingerprint(without_key)


# ------------------------------------------------- promised assertions (mailbox 113)


def _is_type_checking_guard(node: ast.If) -> bool:
    """True if an ``if`` guards a ``TYPE_CHECKING`` block (its imports never execute at
    runtime, so they cannot form a runtime import cycle)."""
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _provider_source_imports(cls) -> set[str]:
    """Collect every module dotted-name imported at RUNTIME by a provider module's
    source (AST), skipping ``if TYPE_CHECKING:`` blocks (typing-only, never executed)."""
    src_file = inspect.getsourcefile(cls)
    assert src_file is not None
    tree = ast.parse(Path(src_file).read_text(encoding="utf-8"))
    mods: set[str] = set()

    def visit(body) -> None:
        for node in body:
            if isinstance(node, ast.If) and _is_type_checking_guard(node):
                continue  # typing-only imports: not a runtime dependency
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    mods.add(node.module)
            for child_body_attr in ("body", "orelse", "finalbody"):
                child = getattr(node, child_body_attr, None)
                if child:
                    visit(child)

    visit(tree.body)
    return mods


@pytest.mark.parametrize("cls", [SolventScreenProvider, CatalystScreenProvider])
def test_providers_do_not_import_domain_or_mcl(cls):
    """Assertion (1): a provider never imports ``expos.domain``/``expos.mcl`` -- the
    ``domain -> provider`` wiring can therefore never form an import cycle
    (``domain -> provider -> mcl -> domain``)."""
    mods = _provider_source_imports(cls)
    for banned in ("expos.domain", "expos.mcl"):
        offenders = {m for m in mods if m == banned or m.startswith(banned + ".")}
        assert not offenders, f"{cls.__name__} imports {offenders} (import cycle risk)"


def test_flat_face_is_single_shared_source():
    """Assertion (2): both providers' ``flat`` truth face IS the same object as the
    shared ``TRUTH_PROFILES['flat']`` -- one cross-domain source of truth, not a copy
    per domain (consolidation by reference)."""
    sp = SolventScreenProvider().truth_profiles()
    cp = CatalystScreenProvider().truth_profiles()
    assert sp["flat"] is TRUTH_PROFILES["flat"]
    assert cp["flat"] is TRUTH_PROFILES["flat"]
    assert sp["flat"] is cp["flat"]
