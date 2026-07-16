"""M25 generative-construct v0.1 acceptance tests (Team M25 "Design").

Covers the v0.1 discriminative contract: five deterministic operators, lineage
append-only + PROV, diversity-aware acquisition, the three acceptance faces, and
the headline 'dry ranking overturned by wet phenotype' case -- all domain-local
(no mcl, no network, no central-file edits).
"""

from __future__ import annotations

import pytest

from expos.adapters.dry.constructs import components_for, construct_names
from expos.adapters.dry.mutation_operators import (
    OPERATOR_NAMES,
    MutationError,
    apply_by_id,
    apply_operator,
    module_fingerprint,
    translate,
)
from expos.kernel.claims import Ledger
from domains.generative_construct.acquisition import (
    composition_distance,
    select,
)
from domains.generative_construct.e2e import M25_CLAIM_ID, run_round
from domains.generative_construct.generation import (
    fixed_pool_seed_lineage,
    generate_children,
    seed_design,
)
from domains.generative_construct.objects import ConstructDesign, DesignLineage
from domains.generative_construct.provider import GenerativeConstructProvider
from domains.generative_construct.wet_truth import (
    FLAT_FACE,
    NEGATIVE_FACE,
    POSITIVE_FACE,
    truth_coord,
    wet_phenotype,
)

CDS_OPERATORS = ("codon_optimize", "cds_synonymous")


# ---------------------------------------------------------------- operators


def test_five_operators_exist():
    assert set(OPERATOR_NAMES) == {
        "promoter_swap", "rbs_swap", "codon_optimize", "utr5_mutation", "cds_synonymous",
    }


@pytest.mark.parametrize("op", OPERATOR_NAMES)
def test_operator_deterministic_and_sequence_invariant(op):
    parent = "j23103"
    c1, p1 = apply_by_id(op, parent)
    c2, p2 = apply_by_id(op, parent)
    # byte-identical child + provenance id on re-run (pure function)
    assert c1 == c2 and p1.child_id == p2.child_id
    # concatenation contract holds
    assert c1["sequence"] == c1["promoter"] + c1["rbs"] + c1["cds"]
    # child id is content-addressed and NEVER the parent id (design != observation)
    assert p1.child_id != parent
    assert p1.parent_id == parent


@pytest.mark.parametrize("op", CDS_OPERATORS)
def test_cds_operators_are_translation_invariant(op):
    """Synonymous edits keep the exact amino-acid peptide (dual-use-safe)."""
    # j23103 carries a rare-codon CDS (so codon_optimize(optimal) really re-encodes
    # it); j23100 carries an optimal CDS (so cds_synonymous can rotate a codon).
    parent = "j23103" if op == "codon_optimize" else "j23100"
    pc = components_for(parent)
    child, _ = apply_by_id(op, parent)
    assert translate(child["cds"]) == translate(pc["cds"])
    # but the nucleotide sequence actually changed
    assert child["cds"] != pc["cds"]


def test_operator_errors_fail_loud():
    pc = components_for("j23100")
    with pytest.raises(MutationError):
        apply_operator("no_such_operator", "j23100", pc)
    with pytest.raises(MutationError):
        apply_operator("promoter_swap", "j23100", pc, {"new_promoter_id": "nope"})
    # non-synonymous CDS target is rejected loudly
    with pytest.raises(MutationError):
        apply_operator("cds_synonymous", "j23100", pc, {"codon_index": 1, "target_codon": "TGG"})
    # a no-op UTR mutation (base == current) is rejected
    rbs = pc["rbs"]
    with pytest.raises(MutationError):
        apply_operator("utr5_mutation", "j23100", pc, {"position": 0, "base": rbs[0]})


def test_module_fingerprint_is_stable_hash():
    fp = module_fingerprint()
    assert fp.startswith("sha256:")
    assert module_fingerprint() == fp


# ---------------------------------------------------------------- lineage


def test_lineage_append_only_and_prov():
    lin = DesignLineage()
    parent = seed_design("j23103")
    lin.add(parent)
    children = generate_children(parent)
    for c in children:
        lin.add(c)
    # every generated child recorded a PROV activity (used=parent, generated=child)
    assert len(lin.activities) == len(children)
    for act in lin.activities:
        assert act["prov_type"] == "activity"
        assert act["used_entity"] == "j23103"
        assert act["generated_entity"] in lin.nodes
        assert "operator_fingerprint" in act
    # idempotent re-add of identical content is fine
    lin.add(children[0])
    # rewriting an id with DIFFERENT content is an append-only violation
    tampered = ConstructDesign(
        design_id=children[0].design_id, components=children[0].components,
        origin="tamper", proxy=0.0,
    )
    with pytest.raises(ValueError):
        lin.add(tampered)


def test_ancestry_chain():
    lin = DesignLineage()
    parent = seed_design("j23103")
    lin.add(parent)
    children = generate_children(parent)
    for c in children:
        lin.add(c)
    chain = lin.ancestry(children[0].design_id)
    assert chain[-1] == "j23103"
    assert children[0].design_id in chain


def test_fixed_pool_11_canary():
    """Regression anchor: the preset pool is exactly the 11 constructs with a
    stable, strictly descending dry-proxy design ladder."""
    lin = fixed_pool_seed_lineage()
    assert len(lin.nodes) == 11
    assert set(lin.nodes) == set(construct_names())
    proxies = [lin.nodes[cid].proxy for cid in construct_names()]
    # strong end beats weak end with a real span (honest-biased design ladder)
    assert proxies[0] > proxies[-1]
    assert proxies[0] == pytest.approx(0.6816, abs=1e-3)
    assert proxies[-1] == pytest.approx(0.1978, abs=1e-3)


# ---------------------------------------------------------------- acquisition


def test_diversity_gate_picks_more_diverse_than_greedy():
    parent = seed_design("j23103")
    pool = generate_children(parent)
    greedy = select(pool, 4, strategy="greedy")
    diverse = select(pool, 4, strategy="diversity")

    def avg_pairwise(sel):
        comps = {d.design_id: d.components for d in pool}
        ids = [s.design_id for s in sel]
        ds = [
            composition_distance(comps[a], comps[b])
            for i, a in enumerate(ids) for b in ids[i + 1 :]
        ]
        return sum(ds) / len(ds)

    assert avg_pairwise(diverse) > avg_pairwise(greedy)


def test_acquisition_deterministic_and_observation_independent():
    parent = seed_design("j23103")
    pool = generate_children(parent)
    a = select(pool, 3, strategy="value_diversity")
    b = select(pool, 3, strategy="value_diversity")
    assert [s.design_id for s in a] == [s.design_id for s in b]
    # value_diversity with lam=0 collapses to greedy ranking
    g = select(pool, 3, strategy="value_diversity", lam=0.0)
    greedy = select(pool, 3, strategy="greedy")
    assert [s.design_id for s in g] == [s.design_id for s in greedy]


# ---------------------------------------------------------------- wet + faces


def test_wet_phenotype_deterministic_replicates_are_independent():
    comp = components_for("j23100")
    r0 = wet_phenotype("j23100", comp, seed=1, replicate=0)
    r0b = wet_phenotype("j23100", comp, seed=1, replicate=0)
    r1 = wet_phenotype("j23100", comp, seed=1, replicate=1)
    assert r0.value == r0b.value            # deterministic
    assert r0.value != r1.value             # replicate is a NEW draw, not a copy
    assert r0.true_coord == r1.true_coord   # same truth, different noise


def test_positive_face_supported():
    prov = GenerativeConstructProvider()
    _, r = run_round(prov.aligned_pool(), round_id=1, ledger=Ledger(),
                     face=POSITIVE_FACE, strategy="greedy", k=4)
    assert r.status == "supported"
    assert r.dry_top == r.wet_top
    assert r.next_strategy == "greedy"


def test_overturn_face_rejected():
    """The headline discriminative case: dry ranking overturned by wet phenotype."""
    prov = GenerativeConstructProvider()
    _, r = run_round(prov.discriminative_pool(), round_id=1, ledger=Ledger(),
                     face=POSITIVE_FACE, strategy="greedy", k=3)
    assert r.status == "rejected"
    assert r.dry_top == "overturn_high_dry"   # dry proxy's top pick
    assert r.wet_top != r.dry_top             # a dry-disfavored design wins wet
    assert r.effect < 0
    # changed knowledge re-steers acquisition away from the proxy
    assert r.next_strategy == "value_diversity"


def test_flat_null_face_insufficient():
    prov = GenerativeConstructProvider()
    _, r = run_round(prov.discriminative_pool(), round_id=1, ledger=Ledger(),
                     face=FLAT_FACE, strategy="greedy", k=3)
    assert r.status == "insufficient"
    assert r.effective_status == "OPEN"       # absence of signal never supports


def test_dry_top_wins_dry_but_loses_wet_explicitly():
    """Directly exhibit a design that dry ranks above another yet loses in wet."""
    prov = GenerativeConstructProvider()
    pool = {d.design_id: d for d in prov.discriminative_pool()}
    high, low = pool["overturn_high_dry"], pool["overturn_low_dry"]
    # dry proxy prefers 'high'
    assert high.proxy > low.proxy
    # but wet truth coordinate prefers 'low' (weak promoter cripples 'high')
    assert truth_coord(low.components) > truth_coord(high.components)
    wh = wet_phenotype(high.design_id, high.components, face=POSITIVE_FACE).true_response
    wl = wet_phenotype(low.design_id, low.components, face=POSITIVE_FACE).true_response
    assert wl > wh


# ---------------------------------------------------------------- e2e loop


def test_e2e_claim_lands_on_real_ledger():
    prov = GenerativeConstructProvider()
    ledger, r = run_round(prov.discriminative_pool(), round_id=1, ledger=Ledger(),
                          face=POSITIVE_FACE, strategy="greedy", k=3)
    statuses = ledger.effective_statuses()
    assert statuses.get(M25_CLAIM_ID) == r.status  # claim reached the shared ledger


def test_e2e_two_round_loop_resteers_after_overturn():
    prov = GenerativeConstructProvider()
    disc = prov.discriminative_pool()
    ledger1, r1 = run_round(disc, round_id=1, ledger=Ledger(),
                            face=POSITIVE_FACE, strategy="greedy", k=3)
    _, r2 = run_round(disc, round_id=2, ledger=ledger1, face=POSITIVE_FACE,
                      strategy=r1.next_strategy, k=3,
                      consumed_knowledge_fp=r1.knowledge_fingerprint)
    # round 1 rejected the proxy; round 2 therefore ran a different strategy
    assert r1.strategy == "greedy"
    assert r2.strategy == "value_diversity"


def test_e2e_deterministic_knowledge_fingerprint():
    prov = GenerativeConstructProvider()
    _, a = run_round(prov.aligned_pool(), round_id=1, ledger=Ledger(),
                     face=POSITIVE_FACE, strategy="greedy", k=4)
    _, b = run_round(prov.aligned_pool(), round_id=1, ledger=Ledger(),
                     face=POSITIVE_FACE, strategy="greedy", k=4)
    assert a.knowledge_fingerprint == b.knowledge_fingerprint


def test_negative_surface_face_is_representable():
    """The surface-level negative face (flipped monotone) is a distinct face from
    the design-level overturn -- both are first-class negative results."""
    prov = GenerativeConstructProvider()
    _, r = run_round(prov.aligned_pool(), round_id=1, ledger=Ledger(),
                     face=NEGATIVE_FACE, strategy="greedy", k=4)
    # on the flipped surface the high-coord dry-top expresses LOW -> not supported
    assert r.status in ("rejected", "insufficient")
