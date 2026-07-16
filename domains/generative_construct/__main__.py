"""Domain-local smoke + full v0.1 story for M25 generative_construct.

Deterministic, no network, no mcl. Demonstrates the whole organ:
  1. fixed-pool 11-construct canary (regression anchor);
  2. all five auditable operators on a weak parent (dry proxy moves);
  3. parent -> children pool + diversity-aware acquisition;
  4. the domain-local e2e: seed -> generate -> acquire -> simulated wet -> claim
     -> knowledge -> changed next-round decision, on THREE acceptance faces
     (positive SUPPORTED, overturn REJECTED, flat INSUFFICIENT).
NOT a wet/truth channel for the dry legs; the wet phenotype is a SIMULATION.
"""

from __future__ import annotations

from expos.adapters.dry.constructs import components_for
from expos.adapters.dry.mutation_operators import OPERATOR_NAMES, apply_by_id, score_components
from expos.kernel.claims import Ledger
from domains.generative_construct.e2e import run_round
from domains.generative_construct.generation import (
    fixed_pool_seed_lineage,
    generate_children,
    seed_design,
)
from domains.generative_construct.provider import GenerativeConstructProvider
from domains.generative_construct.wet_truth import FLAT_FACE, POSITIVE_FACE


def smoke() -> int:
    prov = GenerativeConstructProvider()

    # 1. fixed-pool 11 canary --------------------------------------------------
    canary = fixed_pool_seed_lineage()
    assert len(canary.nodes) == 11, f"expected 11 presets, got {len(canary.nodes)}"
    print(f"[M25] fixed-pool canary: {len(canary.nodes)} preset roots "
          f"(proxy {min(d.proxy for d in canary.nodes.values()):.3f}"
          f"..{max(d.proxy for d in canary.nodes.values()):.3f})")

    # 2. five operators on the weak parent ------------------------------------
    root_id = "j23103"
    pc = components_for(root_id)
    print(f"[M25] five operators on {root_id} (parent proxy={score_components(pc):.4f}):")
    for op in OPERATOR_NAMES:
        child, prov_e = apply_by_id(op, root_id)
        print(f"       {op:16s} -> {prov_e.child_id:34s} proxy={score_components(child):.4f}")

    # 3. generation pool + diversity acquisition ------------------------------
    parent = seed_design(root_id)
    pool = generate_children(parent)
    picks = prov.acquire(pool, k=4, strategy="value_diversity")
    print(f"[M25] pool={len(pool)} children; diversity-aware top-4:")
    for p in picks:
        print(f"       #{p.rank} {p.design_id:34s} value={p.value:.3f} div={p.diversity:.3f} acq={p.acq_score:.3f}")

    # 4. domain-local e2e over three faces ------------------------------------
    aligned = prov.aligned_pool()
    disc = prov.discriminative_pool()
    print("[M25] domain-local e2e (obs -> claim -> knowledge -> next decision):")

    # 4a. POSITIVE face on the aligned in-window preset pool -> SUPPORTED
    ledger, r = run_round(aligned, round_id=1, ledger=Ledger(), face=POSITIVE_FACE,
                          strategy="greedy", k=4)
    print(f"   [aligned/positive] dry_top={r.dry_top} wet_top={r.wet_top} effect={r.effect:+.3f}"
          f" -> claim={r.status} hyp={r.effective_status} next={r.next_strategy}")
    assert r.status == "supported", r.status

    # 4b. POSITIVE face on the discriminative pool -> REJECTED (overturn)
    ledger2, r2 = run_round(disc, round_id=1, ledger=Ledger(), face=POSITIVE_FACE,
                            strategy="greedy", k=3)
    print(f"   [overturn]        dry_top={r2.dry_top} wet_top={r2.wet_top} effect={r2.effect:+.3f}"
          f" -> claim={r2.status} hyp={r2.effective_status} next={r2.next_strategy}")
    assert r2.status == "rejected", r2.status
    assert r2.dry_top != r2.wet_top, "overturn requires dry_top != wet_top"
    assert r2.next_strategy == "value_diversity", r2.next_strategy

    # 4c. FLAT null face -> INSUFFICIENT (no signal; must not fabricate a claim)
    _, r3 = run_round(disc, round_id=1, ledger=Ledger(), face=FLAT_FACE,
                      strategy="greedy", k=3)
    print(f"   [flat/null]       effect={r3.effect:+.3f} -> claim={r3.status}"
          f" hyp={r3.effective_status} next={r3.next_strategy}")
    assert r3.status == "insufficient", r3.status

    # 4d. two-round loop: overturn REJECT re-steers acquisition next round
    ledger_a, ra = run_round(disc, round_id=1, ledger=Ledger(), face=POSITIVE_FACE,
                             strategy="greedy", k=3)
    _, rb = run_round(disc, round_id=2, ledger=ledger_a, face=POSITIVE_FACE,
                      strategy=ra.next_strategy, k=3,
                      consumed_knowledge_fp=ra.knowledge_fingerprint)
    print(f"   [loop] round1 strategy={ra.strategy} -> knowledge re-steered round2 "
          f"strategy={rb.strategy} (proxy distrusted after overturn)")
    assert rb.strategy == "value_diversity", rb.strategy

    print("[M25 smoke] PASS (5 operators + pool + acquisition + 3-face e2e + re-steered loop)")
    return 0


if __name__ == "__main__":
    raise SystemExit(smoke())
