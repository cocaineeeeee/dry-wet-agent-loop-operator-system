"""M23 Phase 0 unit-metadata tests (REF-U §Convergence(c) T1-T4 + zero-change gate).

REF-U ruling: units live as SCHEMA METADATA (astropy ECSV posture -- bare numeric +
a unit string validated against a controlled vocabulary on read; unknown => loud
raise) with ZERO runtime Quantity types and NO automatic conversion anywhere. These
tests pin the discriminative failures:

  * T1 -- an unknown unit in a domain declaration is LOUD at load_domain.
  * T2 -- a cross-record unit mismatch (debye vs microliter) is refused before any
          compare, never silently coerced (Mars-Climate-Orbiter guard).
  * T3 -- offset-unit discipline: celsius is a nameable vocabulary member but NO
          conversion path exists; the check is strict equality only.
  * T4 -- a declared-but-missing unit on a required (declared) metric is refused,
          never defaulted/guessed.

Plus the M23 Phase 0 HARD GATE: every shipped yaml (none of which declares
``metric_units`` yet) loads byte-unchanged, and provider-less fingerprints are
byte-identical to their pure model_dump hash.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest
import yaml

from expos.domain import (
    UNIT_VOCABULARY,
    DomainError,
    check_unit_consistency,
    config_fingerprint,
    load_domain,
)
from expos.kernel.claims import StatisticSnapshot

_REPO = Path(__file__).resolve().parents[1]
_SOLVENT_YAML = _REPO / "domains" / "solvent_screen.yaml"
_SHIPPED = sorted((_REPO / "domains").glob("*.yaml"))


def _synthetic_metric_yaml(tmp_path: Path, mutate) -> Path:
    """Build a provider-free synthetic domain yaml with a ``metrics`` block from the
    solvent_screen base: drop the provider (so the vocabulary machinery is exercised
    in isolation of provider loading), apply ``mutate(raw)`` in place, write it out,
    and return the path for ``load_domain`` to run its full real pipeline."""
    raw = yaml.safe_load(_SOLVENT_YAML.read_text(encoding="utf-8"))
    raw.pop("provider", None)
    mutate(raw)
    out = tmp_path / "synthetic_domain.yaml"
    out.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return out


# --------------------------------------------------------------- T1: unknown unit

def test_t1_unknown_unit_is_loud_at_load(tmp_path):
    """A metric unit outside UNIT_VOCABULARY raises DomainError at load -- never a
    silent free string (astropy parse_strict='raise' posture)."""

    def mutate(raw):
        raw["metric_units"] = {"solvent_response": "foobar"}

    with pytest.raises(DomainError) as exc:
        load_domain(_synthetic_metric_yaml(tmp_path, mutate))
    assert "foobar" in str(exc.value)


def test_t1_valid_unit_loads_and_round_trips(tmp_path):
    """A metric unit that IS in the vocabulary loads cleanly and is carried on the
    DomainConfig (the positive control for T1)."""

    def mutate(raw):
        raw["metric_units"] = {"solvent_response": "arbitrary_unit", "polarity_proxy": "debye"}

    cfg = load_domain(_synthetic_metric_yaml(tmp_path, mutate))
    assert cfg.metric_units == {"solvent_response": "arbitrary_unit", "polarity_proxy": "debye"}


def test_t1_unit_for_undeclared_metric_is_loud(tmp_path):
    """A unit attached to a metric outside the domain's ``metrics`` vocabulary is a
    loud load error (a unit can only be declared for a declared metric)."""

    def mutate(raw):
        raw["metric_units"] = {"not_a_metric": "debye"}

    with pytest.raises(DomainError):
        load_domain(_synthetic_metric_yaml(tmp_path, mutate))


def test_t1_metric_units_without_metrics_is_loud(tmp_path):
    """metric_units with no ``metrics`` block at all is loud -- there is nothing to
    attach a unit to."""

    def mutate(raw):
        raw.pop("metrics", None)
        raw.pop("observables", None)  # observables reference the metrics vocabulary
        raw["metric_units"] = {"solvent_response": "debye"}

    with pytest.raises(DomainError):
        load_domain(_synthetic_metric_yaml(tmp_path, mutate))


# ------------------------------------------------ T2: cross-record dimension mismatch

def test_t2_dimension_mismatch_refused_before_compare():
    """debye vs microliter: the check function REJECTS before any compare/aggregate;
    it never silently treats one dimension as the other (Mars-Climate-Orbiter)."""
    with pytest.raises(DomainError) as exc:
        check_unit_consistency("debye", "microliter", metric="reactivity_proxy")
    msg = str(exc.value)
    assert "debye" in msg and "microliter" in msg


def test_t2_matching_units_pass():
    """The positive control for T2: equal units compare fine (no raise)."""
    check_unit_consistency("microliter", "microliter", metric="wet_volume")
    check_unit_consistency("debye", "debye")


# ------------------------------------------------------- T3: offset-unit discipline

def test_t3_celsius_in_vocabulary_but_no_conversion_path():
    """celsius is a nameable vocabulary member, but the check is STRICT EQUALITY: it
    refuses a mismatch rather than converting. celsius is an offset unit, so any
    scalar-factor 'conversion' would silently corrupt -- assert none exists."""
    assert "celsius" in UNIT_VOCABULARY
    # equal -> passes (pure equality, no transformation)
    check_unit_consistency("celsius", "celsius", metric="temp")
    # different -> REFUSES, does not 'convert' celsius into anything else
    with pytest.raises(DomainError):
        check_unit_consistency("celsius", "arbitrary_unit", metric="temp")


def test_t3_no_conversion_parameter_exists():
    """Kill-guard: the check function's signature carries NO conversion/factor/offset
    parameter. Anyone adding a scalar-factor conversion path breaks this test's intent
    by design (REF-U reject #3: implicit conversion silently corrupts offset units)."""
    params = list(inspect.signature(check_unit_consistency).parameters)
    assert params == ["observed_unit", "declared_unit", "metric"]
    for forbidden in ("convert", "factor", "offset", "to_unit", "scale"):
        assert forbidden not in params


# --------------------------------------------------- T4: required-but-missing unit

def test_t4_declared_unit_missing_observed_is_refused():
    """A declared metric unit IS a requirement: an observation that drops its unit is
    refused, never defaulted or guessed (the wet volume-like high-risk face)."""
    for missing in (None, "", "   "):
        with pytest.raises(DomainError) as exc:
            check_unit_consistency(missing, "microliter", metric="wet_volume")
        assert "microliter" in str(exc.value)


def test_t4_no_declared_unit_means_no_requirement():
    """No declared unit => no requirement: a unit-free legacy metric is not forced to
    carry a unit (declared_unit=None returns quietly)."""
    check_unit_consistency(None, None)
    check_unit_consistency("anything", None)  # nothing declared -> nothing enforced


# ------------------------------------------- StatisticSnapshot effect_unit (deliv. A)

def test_effect_unit_field_is_additive_optional():
    """StatisticSnapshot.effect_unit exists, defaults None, and accepts a unit string;
    one field covers effect_estimate/se/ci_low/ci_high (shared unit by construction)."""
    snap = StatisticSnapshot()
    assert snap.effect_unit is None
    snap2 = StatisticSnapshot(effect_estimate=0.3, effect_se=0.1, effect_unit="arbitrary_unit")
    assert snap2.effect_unit == "arbitrary_unit"
    # frozen: provenance-only, immutable after construction
    with pytest.raises(Exception):
        snap2.effect_unit = "debye"


def test_effect_unit_absent_snapshot_serializes_with_null():
    """A snapshot that omits effect_unit round-trips with the field present-but-null
    (schema-additive; no arithmetic is ever performed on it)."""
    dumped = StatisticSnapshot(effect_estimate=1.0).model_dump()
    assert dumped["effect_unit"] is None


# ---------------------------------------------- HARD GATE: shipped-yaml zero change

def test_all_shipped_yamls_load_unchanged():
    """Every shipped domain yaml loads. The two FLAGSHIP domains now declare a
    ``metric_units`` block (M23 Phase 1/2 landing, mailbox 128: solvent_screen +
    catalyst_screen), all vocabulary-valid; every OTHER shipped domain carries
    metric_units=None -- the additive schema stays invisible to a domain that does not
    opt in. (Updated from the Phase 0 premise "none declares units yet": the field is
    additive-optional, and declaring it is the ratified path, not a break.)"""
    assert _SHIPPED, "no shipped domain yamls found"
    # M24: the biological cell_free_expression_screen domain also declares a (vocabulary-
    # valid) metric_units block (expression_fluorescence -> arbitrary_unit, expression_proxy
    # -> dimensionless), so it joins the flagship domains that opt into the additive schema.
    _declares_units = {"solvent_screen", "catalyst_screen", "cell_free_expression_screen"}
    for path in _SHIPPED:
        cfg = load_domain(path)
        if cfg.name in _declares_units:
            # a declared block loads and is vocabulary-valid (load_domain would raise T1
            # otherwise); every declared unit is a UNIT_VOCABULARY member.
            assert cfg.metric_units is not None
            assert set(cfg.metric_units.values()) <= UNIT_VOCABULARY
        else:
            assert cfg.metric_units is None


def test_provider_less_fingerprints_byte_identical():
    """Provider-less domains' config_fingerprint stays byte-identical to their pure
    model_dump hash after the additive field lands (self-consistency gate)."""
    for name in ("crystal", "coating", "solvent_screen_flipped"):
        cfg = load_domain(_REPO / "domains" / f"{name}.yaml")
        assert cfg.provider is None
        material = json.dumps(
            cfg.model_dump(mode="json", exclude={"provider"}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert config_fingerprint(cfg) == hashlib.sha256(material).hexdigest()


def test_unit_vocabulary_contents():
    """Pin the seeded controlled unit vocabulary (honest set actually in play)."""
    assert UNIT_VOCABULARY == frozenset(
        {"arbitrary_unit", "debye", "dimensionless", "celsius", "microliter"}
    )
