"""M16 W9 — `--loop mcl` two-round end-to-end smoke suite.

These are the wiring proofs for the dual-leg minimum complete loop
(expos.mcl.run_mcl_loop): a real two-round run really spawns PySCF dry jobs and
drives the in-process plate-reader wet leg, so the whole suite is minutes-level
(each PySCF HF/STO-3G job is sub-second — W3 proven). They complement the
discriminative acceptance bodies in tests/test_w8_acceptance (owned by A): here
we only assert the loop closes and replays deterministically.

  * test_mcl_two_rounds_end_to_end — the run exits success, the event chain
    carries {run_start, knowledge_updated×2, promotion_decision×2, run_stop}, and
    BOTH legs are QC-routed (a routing event per observation, dry + wet channels).
  * test_mcl_same_seed_is_deterministic — two same-seed runs produce the same
    event sequence on the load-bearing fields (the G5 replay substrate).
  * test_mcl_contrary_claim_resteers_promotion — a flipped claim predictably
    changes the knowledge fingerprint, the proposal order and the promoted set
    (the G1 knowledge-consumption discriminator, applied at the loop level).
"""

from __future__ import annotations

from pathlib import Path

from expos.kernel.objects import TrustLevel
from expos.kernel.store import RunStore
from expos.mcl import run_mcl_loop

_DOMAIN = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"


def _decision_projection(run_dir: Path) -> list[tuple]:
    """The DECISION surface of the run: the knowledge compile, the agent
    proposal and the promoted set. This layer is a PURE function of (seed,
    knowledge) — the G1/G5 replay substrate — so it is byte-identical across
    same-seed runs.

    Deliberately EXCLUDED: the dry/wet EXECUTION results (obs values, routing
    counts) and a candidate's ``deny_reason``. Real out-of-process PySCF
    execution can be killed under resource contention and land as a legitimate
    ``dry_failed`` (G2 failure taxonomy — that is the point of running the dry
    leg as real jobs, not an in-process function). Such a flake changes a
    denied REASON but not the promoted set (only the two top-acquisition,
    reliably-converging in-window candidates are promoted; a flake of a
    gate-ranked/out-of-window candidate cannot change the promoted set), so the
    decision surface stays deterministic while honest about real execution."""
    store = RunStore(run_dir, create=False)
    proj: list[tuple] = []
    for ev in store.read_events():
        kind, p = ev["kind"], ev["payload"]
        if kind == "knowledge_updated":
            proj.append((kind, p["fingerprint"], p["n_hypotheses"], p["n_claims"]))
        elif kind == "promotion_decision":
            proj.append((kind, p["round_id"], p["knowledge_fingerprint"],
                         tuple(x["cand_id"] for x in p["promoted"])))
        elif kind == "decision":
            c = p["content"]
            proj.append((kind, p["round_id"], tuple(c.get("candidates", ())),
                         tuple(c.get("basis", ()))))
        elif kind in ("run_start", "run_stop"):
            proj.append((kind, p.get("exit_status")))
    return proj


def test_mcl_two_rounds_end_to_end(tmp_path):
    summary = run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "run")

    assert summary["rounds_completed"] == 2
    store = RunStore(tmp_path / "run", create=False)
    events = store.read_events()
    kinds = [e["kind"] for e in events]

    # terminal-state contract: exactly one run_start, one successful run_stop
    assert kinds.count("run_start") == 1
    stops = store.read_events("run_stop")
    assert len(stops) == 1 and stops[0]["payload"]["exit_status"] == "success"

    # one knowledge compile + one promotion decision per round
    assert kinds.count("knowledge_updated") == 2
    assert kinds.count("promotion_decision") == 2

    # both legs land in one store and are QC-routed (one routing event per obs).
    all_obs = store.list_observations()
    assert {o.raw_ref.kind for o in all_obs} == {"dry", "wet"}  # both channels ran
    assert all(o.trust is not TrustLevel.PENDING for o in all_obs)  # adjudicated
    assert len(store.read_events("routing")) == len(all_obs)
    # one qc_report per adjudicated leg: dry both rounds (2) + wet every round
    # that promoted at least one candidate.
    n_wet_rounds = sum(
        1 for ev in store.read_events("promotion_decision") if ev["payload"]["promoted"]
    )
    assert n_wet_rounds >= 1  # the wet leg closed at least once
    assert len(store.read_events("qc_report")) == 2 + n_wet_rounds

    # every promotion_decision records its knowledge witness, and no denial is a
    # silent edge (every cull carries a reason).
    for ev in store.read_events("promotion_decision"):
        p = ev["payload"]
        assert p["knowledge_fingerprint"]
        assert all(d["deny_reason"] for d in p["denied"])

    # payload-validation gate sees no violations across the whole stream
    assert store.validate_event_payloads(events) == []


def test_mcl_same_seed_is_deterministic(tmp_path):
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "a")
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "b")

    proj_a = _decision_projection(tmp_path / "a")
    proj_b = _decision_projection(tmp_path / "b")
    assert proj_a == proj_b  # G5 replay: same-seed decision surface is bit-identical

    # frozen knowledge => round-2 decision is bit-identical to round-1
    promos = [p for p in proj_a if p[0] == "promotion_decision"]
    assert len(promos) == 2
    assert promos[0][2:] == promos[1][2:]  # fingerprint + promoted set equal
    props = [p for p in proj_a if p[0] == "decision"]
    assert len(props) == 2
    assert props[0][2:] == props[1][2:]  # proposal candidates + basis equal


def test_mcl_contrary_claim_resteers_promotion(tmp_path):
    """Loop-level G1 discriminator: injecting a contrary claim (polar->rejected,
    nonpolar->supported) must re-fingerprint the knowledge, reverse the proposal
    ordering and change the promoted set — proving the agent CONSUMES knowledge,
    not merely performs feedback (C2 lesson)."""
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "base")
    contrary = [
        {"claim_id": "c_polar_responds_higher", "status": "rejected"},
        {"claim_id": "c_nonpolar_responds_higher", "status": "supported"},
    ]
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "flip", claims=contrary)

    base = RunStore(tmp_path / "base", create=False)
    flip = RunStore(tmp_path / "flip", create=False)

    fp_base = base.read_events("knowledge_updated")[0]["payload"]["fingerprint"]
    fp_flip = flip.read_events("knowledge_updated")[0]["payload"]["fingerprint"]
    assert fp_base != fp_flip  # knowledge state provably differs

    prop_base = base.list_decisions()[0].content["candidates"]
    prop_flip = flip.list_decisions()[0].content["candidates"]
    assert prop_base != prop_flip  # proposal re-steered

    promo_base = [x["cand_id"]
                  for x in base.read_events("promotion_decision")[0]["payload"]["promoted"]]
    promo_flip = [x["cand_id"]
                  for x in flip.read_events("promotion_decision")[0]["payload"]["promoted"]]
    assert promo_base != promo_flip  # promoted set changed predictably
