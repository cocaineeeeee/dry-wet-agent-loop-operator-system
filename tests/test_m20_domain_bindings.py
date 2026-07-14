"""M20 domain swappability — B-side landing: minimal but discriminative.

Proves the mcl loop is drivable from domain config (per-variable ``descriptors`` +
an optional ``seed_claims`` block) instead of the hardcoded solvent literals,
WITHOUT disturbing the solvent_screen regression anchor:

  * test_solvent_regression_twin — a real solvent_screen full-loop still yields the
    byte-value decision face (knowledge fingerprint pinned to the M16 substrate +
    the exact proposal order + promoted set). The legacy fallback is byte-identical.
  * test_config_driven_bindings_resolve_from_config — a synthetic domain whose
    categorical variable carries a descriptors map resolves its screening
    variable/pool/coords from CONFIG, not the solvent literals, and builds candidate
    params from the descriptor path (KILL: drop the descriptor branch in
    _domain_bindings and this reverts to solvent values -> red).
  * test_inconsistent_descriptor_coords_fail_loud — a variable whose levels carry
    drifting coord keys is rejected at load (item-1 validation).
  * test_seed_claims_block_drives_seed_ledger — a seed_claims block makes the run's
    seed ledger the declared claims, and the built-in c_polar family is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from expos.domain import DomainConfig, load_domain
from expos.kernel.claims import ledger_to_claim_dicts
from expos.kernel.objects import VariableDef
from expos.kernel.store import RunStore
from expos.mcl import (
    _CLAIM_POLAR_HIGHER,
    _candidate_params,
    _default_claims,
    _domain_bindings,
    _domain_hypotheses,
    _domain_seed_claims,
    _seed_ledger,
    run_mcl_loop,
)

_DOMAIN = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"
_CATALYST = Path(__file__).resolve().parents[1] / "domains" / "catalyst_screen.yaml"


# ---------------------------------------------------------------- synthetic config

def _catalyst_like_config() -> DomainConfig:
    """A synthetic config-driven domain (a catalyst-shaped stand-in): reuses the
    solvent_screen base for the required fields, then makes its categorical variable
    carry a descriptors map and declares its OWN seed_claims. Built via model_validate
    (no adapter/metric coupling needed — these tests exercise binding resolution)."""
    base = load_domain(_DOMAIN).model_dump(mode="json")
    # Replace the categorical `solvent` variable with a descriptor-carrying `ligand`.
    variables = [v for v in base["design_space"]["variables"] if v["name"] != "solvent"]
    variables.insert(0, {
        "name": "ligand", "kind": "categorical",
        "choices": ["lig_a", "lig_b", "lig_c", "lig_d"],
        "descriptors": {
            "lig_a": {"coord": 0.90},
            "lig_b": {"coord": 0.30},
            "lig_c": {"coord": 0.60},
            "lig_d": {"coord": 0.05},
        },
    })
    base["design_space"]["variables"] = variables
    base["sentinel"]["params"] = {"ligand": "lig_a", "concentration": 5.0,
                                  "temperature": 25.0, "incubation_time": 30.0}
    base["seed_claims"] = [
        {"claim_id": "c_reactive_higher",
         "statement": "reactive ligands give a higher yield",
         "status": "supported", "direction": "higher"},
        {"claim_id": "c_unreactive_higher",
         "statement": "unreactive ligands give a higher yield",
         "status": "rejected", "direction": "lower"},
    ]
    return DomainConfig.model_validate(base)


# ---------------------------------------------------------------- E1: regression twin

def test_solvent_regression_twin(tmp_path):
    """A real solvent_screen full-loop (legacy fallback path) reproduces the exact
    byte-value decision face: knowledge fingerprint pinned to the M16 substrate, the
    proposal ordered by descending polarity, and the two in-window solvents promoted."""
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "run")
    store = RunStore(tmp_path / "run", create=False)

    # knowledge fingerprint == the pinned M16 substrate (default claims + hypotheses).
    from expos.kernel.knowledge import compile_knowledge
    m16_fp = compile_knowledge(
        _default_claims(), _domain_hypotheses(load_domain(_DOMAIN))
    ).knowledge_fingerprint
    fps = [e["payload"]["fingerprint"] for e in store.read_events("knowledge_updated")]
    assert fps == [m16_fp]

    # proposal order: polar preferred -> descending polarity, hexane last (out of window).
    props = [d for d in store.list_decisions() if d.kind.value == "prior_proposal"]
    assert len(props) == 1
    assert props[0].content["candidates"] == ["ethanol", "acetonitrile", "acetone", "hexane"]

    # promoted set: top-2 in-window converged (ethanol, acetonitrile), hexane denied.
    promo = store.read_events("promotion_decision")
    assert len(promo) == 1
    promoted = tuple(p["cand_id"] for p in promo[0]["payload"]["promoted"])
    assert promoted == ("cand_ethanol", "cand_acetonitrile")


# ---------------------------------------------------------------- E2: config-driven

def test_config_driven_bindings_resolve_from_config():
    """The synthetic domain resolves bindings from the variable's descriptors, not
    the solvent literals, and builds candidate params via the descriptor path.

    KILL: if _domain_bindings dropped the descriptor branch and always returned the
    legacy fallback, variable/pool/coords below would be the solvent values."""
    cfg = _catalyst_like_config()
    b = _domain_bindings(cfg)

    assert b.variable == "ligand"                      # not "solvent"
    assert b.params_kind == "descriptor"
    assert set(b.candidate_pool) == {"lig_a", "lig_b", "lig_c", "lig_d"}
    assert b.coords == {"lig_a": 0.90, "lig_b": 0.30, "lig_c": 0.60, "lig_d": 0.05}
    assert b.descriptors == {
        "lig_a": {"coord": 0.90}, "lig_b": {"coord": 0.30},
        "lig_c": {"coord": 0.60}, "lig_d": {"coord": 0.05},
    }
    assert b.prefer_higher_default is True
    assert b.higher_hyp_ids == ("hyp_c_reactive_higher",)
    assert b.lower_hyp_ids == ("hyp_c_unreactive_higher",)

    # None of the solvent literals leaked in.
    assert "solvent" not in b.coords and "ethanol" not in b.coords


def test_catalyst_yaml_bindings_use_catalyst_params():
    """The shipped catalyst_screen.yaml resolves the catalyst variable + the five
    ligand descriptor coords, and its candidate params expand via catalyst_params
    (carrying the explicit geometry the unchanged dry adapter reads)."""
    cfg = load_domain(_CATALYST)
    b = _domain_bindings(cfg)
    assert b.variable == "catalyst"
    assert set(b.candidate_pool) == {"pf3", "pme3", "ph3", "pcl3", "nh3"}
    assert b.coords == {"pf3": 0.05, "pme3": 0.30, "ph3": 0.50, "pcl3": 0.75, "nh3": 1.00}
    params = _candidate_params("nh3", b)
    assert params["catalyst"] == "nh3"
    assert "geometry" in params and params["geometry"].strip()  # dry adapter reads this


def test_catalyst_yaml_descriptors_match_source_table():
    """The un-commented yaml descriptors block equals the CATALYST_DESCRIPTORS source
    constant verbatim (the handoff invariant: yaml is the transcription, not a fork)."""
    from expos.adapters.dry.catalysts import CATALYST_DESCRIPTORS
    cfg = load_domain(_CATALYST)
    assert cfg.design_space.var("catalyst").descriptors == CATALYST_DESCRIPTORS


# ---------------------------------------------------------------- E3: validation

def test_inconsistent_descriptor_coords_fail_loud():
    """A variable whose levels carry DIFFERENT coord keys is rejected loudly at load
    (item-1 validation: all levels of one variable must share the same coord axes)."""
    with pytest.raises(Exception) as ei:
        VariableDef(
            name="ligand", kind="categorical", choices=["a", "b"],
            descriptors={"a": {"coord": 0.9}, "b": {"reactivity": 0.3}},
        )
    assert "坐标键不一致" in str(ei.value)


def test_empty_descriptor_level_map_fails_loud():
    """An empty coordinate map for a level is rejected (item-1 validation)."""
    with pytest.raises(Exception) as ei:
        VariableDef(
            name="ligand", kind="categorical", choices=["a"],
            descriptors={"a": {}},
        )
    assert "不可为空" in str(ei.value)


# ---------------------------------------------------------------- E4: seed neutralization

def test_seed_claims_block_drives_seed_ledger():
    """A seed_claims block makes the seed ledger the DECLARED claims; the built-in
    c_polar family is absent. Hypotheses derive one-per-claim referencing them."""
    cfg = _catalyst_like_config()

    seeds = _domain_seed_claims(cfg)
    ids = {c["claim_id"] for c in seeds}
    assert ids == {"c_reactive_higher", "c_unreactive_higher"}
    assert _CLAIM_POLAR_HIGHER not in ids

    # The online ledger built from these seeds carries exactly the declared claims.
    ledger = _seed_ledger(seeds)
    ledger_ids = {c["claim_id"] for c in ledger_to_claim_dicts(ledger)}
    assert ledger_ids == {"c_reactive_higher", "c_unreactive_higher"}
    assert _CLAIM_POLAR_HIGHER not in ledger_ids

    # Hypotheses reference their claims (so compile_knowledge can resteer them).
    hyps = _domain_hypotheses(cfg)
    assert {h.hypothesis_id for h in hyps} == {"hyp_c_reactive_higher", "hyp_c_unreactive_higher"}
    assert all(len(h.evidence_refs) == 1 for h in hyps)


def test_absent_seed_claims_is_byte_identical_legacy():
    """No seed_claims block => the built-in polar family (byte-identical fallback)."""
    cfg = load_domain(_DOMAIN)
    assert _domain_seed_claims(cfg) == _default_claims()
    b = _domain_bindings(cfg)
    assert b.variable == "solvent"
    assert b.params_kind == "solvent"
    assert b.candidate_pool == ("ethanol", "acetonitrile", "acetone", "hexane")
    assert b.higher_hyp_ids == ("hyp_polar_higher",)
