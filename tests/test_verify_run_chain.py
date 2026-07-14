"""Gate-12 acceptance tests for scripts/verify_run_chain.py — third-party
recompute/re-audit of a run's decision chain from the event stream alone.

All positives run REAL mcl loops (run_mcl_loop, seed=7, AggregatedCertification —
the same substrate tests/test_k_f_glue exercises), so the chain being verified is
genuine, not synthesized. Negatives tamper COPIES of a real run (never the
original) to prove each guard is load-bearing: delete it and the verifier goes
red at the right layer/breakpoint. Diff positives/negatives use same-seed runs
(zero divergence) and a truth-face flip (divergence at the wet-derived
claim_decision node). One unit body pins the INDEX §3 sort-normalization
(same content, different write order => identical node fingerprint).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from expos.agent.llm_backend import CompletionResult
from expos.mcl import run_mcl_loop
from expos.planner.certification import AggregatedCertification
from expos.qc.certification_stats import AggregationConfig, ClaimHead

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "verify_run_chain.py"
_DOMAIN = _REPO / "domains" / "solvent_screen.yaml"


def _load_vrc():
    """Load the CLI script as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("verify_run_chain", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field-type introspection can resolve the
    # module via sys.modules[cls.__module__] (else @dataclass raises on Py3.13).
    sys.modules["verify_run_chain"] = mod
    spec.loader.exec_module(mod)
    return mod


vrc = _load_vrc()


def _aggregated_cert() -> AggregatedCertification:
    head = ClaimHead(
        claim_id="c_polar_responds_higher",
        statement="polar solvents give a higher plate-reader response",
        favorable_direction="higher",
        focal_group=("cand_ethanol",),
        reference_group=("cand_acetonitrile",),
    )
    return AggregatedCertification(
        [head], config=AggregationConfig(run_fingerprint="verify_gate12")
    )


@pytest.fixture(scope="module")
def real_runs(tmp_path_factory) -> dict[str, Path]:
    """Three REAL two-round mcl runs, shared across the module to keep runtime sane:
      * ``default`` — the default (polar-high) truth face (primary positive + tamper base);
      * ``twin``    — same seed/config/face (zero-divergence diff);
      * ``flip``    — the flipped (nonpolar-high) truth face (divergence diff).
    Only the hidden truth face differs default<->flip; everything else is identical."""
    default = tmp_path_factory.mktemp("default") / "run"
    twin = tmp_path_factory.mktemp("twin") / "run"
    flip = tmp_path_factory.mktemp("flip") / "run"
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=default, certification=_aggregated_cert())
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=twin, certification=_aggregated_cert())
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=flip,
                 certification=_aggregated_cert(), truth_profile="nonpolar_high")
    return {"default": default, "twin": twin, "flip": flip}


# ============================================================ positive: all layers green

def test_real_run_verifies_all_layers_green(real_runs):
    """A real run passes all three layers: lifecycle pairing, payload completeness,
    and per-round fingerprint threading + checkpoint reconciliation."""
    result = vrc.verify_run(real_runs["default"])
    assert result.ok, f"{result.code}: {result.message}"
    assert result.layer is None and result.code is None
    s = result.summary
    assert s["n_rounds"] == 2
    assert s["n_promotion_decisions"] == 2
    assert s["n_claim_decisions"] == 2
    assert s["final_exit_status"] == "success"
    # one knowledge fingerprint per round, all non-empty.
    assert len(s["knowledge_fingerprints"]) == 2
    assert all(fp for fp in s["knowledge_fingerprints"])


def test_cli_exit_codes(real_runs):
    """CLI contract: 0 = complete, 2 = usage (unreadable run)."""
    env = {"PYTHONPATH": str(_REPO), "PATH": "/usr/bin:/bin"}
    ok = subprocess.run([sys.executable, str(_SCRIPT), str(real_runs["default"])],
                        capture_output=True, text=True, env=env)
    assert ok.returncode == 0, ok.stderr
    assert "CHAIN COMPLETE" in ok.stdout
    usage = subprocess.run([sys.executable, str(_SCRIPT), "/no/such/run"],
                           capture_output=True, text=True, env=env)
    assert usage.returncode == 2


# ============================================================ negative: delete a guard -> red

def _copy_run(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def _rewrite_events(run_dir: Path, events: list[dict]) -> None:
    """Rewrite events.jsonl with CONTIGUOUS seqs (so the store reader's seq
    monotonicity is satisfied and layer 3 — not the seq guard — is the one under
    test). Tamper on a COPY only."""
    for i, e in enumerate(events):
        e["seq"] = i
    run_dir.joinpath("events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
        encoding="utf-8",
    )


def test_delete_knowledge_updated_breaks_layer3(real_runs, tmp_path):
    """Negative (a): drop round-1's knowledge_updated event on a COPY. The round
    still has a promotion_decision/claim_decision, so the fingerprint chain has no
    knowledge node to thread through -> layer 3 red, pinpointing the orphaned round."""
    run = _copy_run(real_runs["default"], tmp_path / "del_knowledge")
    events = [json.loads(x) for x in
              run.joinpath("events.jsonl").read_text().splitlines() if x.strip()]
    seen = 0
    kept = []
    for e in events:
        if e["kind"] == "knowledge_updated":
            seen += 1
            if seen == 2:  # drop round-1's knowledge compile
                continue
        kept.append(e)
    _rewrite_events(run, kept)

    result = vrc.verify_run(run)
    assert not result.ok
    assert result.layer == 3
    assert result.code == "knowledge_missing_for_round"
    assert result.detail.get("round_id") == 1  # the orphaned round is named


def test_tamper_checkpoint_fingerprint_breaks_reconciliation(real_runs, tmp_path):
    """Negative (b): flip ONE char of a fingerprint inside checkpoint.claim_ledger
    on a COPY. The stream's claim_decision still carries the true consumed
    fingerprint, so end-state reconciliation catches the mismatch -> layer 3 red."""
    run = _copy_run(real_runs["default"], tmp_path / "tamper_ckpt")
    ckpt = json.loads(run.joinpath("checkpoint.json").read_text())
    tampered = False
    for rec in ckpt["claim_ledger"]:
        fp = rec["provenance"]["usage"]["consumed_knowledge_fingerprint"]
        if fp != "seed":
            flip = "f" if fp[0] != "f" else "0"
            rec["provenance"]["usage"]["consumed_knowledge_fingerprint"] = flip + fp[1:]
            tampered = True
            break
    assert tampered, "expected a non-seed ledger fingerprint to tamper"
    run.joinpath("checkpoint.json").write_text(json.dumps(ckpt, indent=2), encoding="utf-8")

    result = vrc.verify_run(run)
    assert not result.ok
    assert result.layer == 3
    assert result.code == "ledger_fp_mismatch"


def test_seq_gap_from_raw_deletion_is_layer1(real_runs, tmp_path):
    """A RAW event deletion (no renumber) trips the store reader's seq-monotonicity
    guard; the verifier folds that into a layer-1 stream-integrity breakpoint rather
    than crashing."""
    run = _copy_run(real_runs["default"], tmp_path / "raw_delete")
    lines = [x for x in run.joinpath("events.jsonl").read_text().splitlines() if x.strip()]
    # drop a middle line, leaving a seq hole.
    del lines[5]
    run.joinpath("events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = vrc.verify_run(run)
    assert not result.ok
    assert result.layer == 1
    assert result.code == "stream_integrity"


# ============================================================ diff: consistent vs divergent

def test_diff_same_seed_zero_divergence(real_runs):
    """Two same-seed/same-face runs produce byte-identical node fingerprints ->
    the decision chains diff to ZERO divergence (determinism substrate, INDEX §2)."""
    result = vrc.diff_decision_chain(real_runs["default"], real_runs["twin"])
    assert not result.diverged, result.first_divergence
    assert result.n_nodes_a == result.n_nodes_b > 0


def test_diff_truth_face_flip_diverges_at_claim_decision(real_runs):
    """Flip ONLY the hidden truth face: knowledge/proposal/promotion nodes are
    truth-blind and stay identical, so the FIRST divergence is the round-0
    claim_decision node whose wet-derived effect flips sign (K2)."""
    result = vrc.diff_decision_chain(real_runs["default"], real_runs["flip"])
    assert result.diverged
    d = result.first_divergence
    assert d["reason"] == "content"
    assert d["round"] == 0
    assert d["node_type"] == "claim_decision"
    assert d["discriminator"] == "c_polar_responds_higher"
    assert d["a_fingerprint"] != d["b_fingerprint"]
    # the divergence is the wet-derived sign flip: default effect >= 0, flip < 0.
    assert "effect=" in d["a_summary"] and "effect=" in d["b_summary"]
    a_effect = float(d["a_summary"].split("effect=")[1])
    b_effect = float(d["b_summary"].split("effect=")[1])
    assert a_effect >= 0.0 > b_effect


def test_diff_cli_exit_codes(real_runs):
    """CLI diff contract: 0 = consistent, 1 = divergent."""
    env = {"PYTHONPATH": str(_REPO), "PATH": "/usr/bin:/bin"}
    same = subprocess.run(
        [sys.executable, str(_SCRIPT), "--diff",
         str(real_runs["default"]), str(real_runs["twin"])],
        capture_output=True, text=True, env=env)
    assert same.returncode == 0, same.stderr
    assert "CONSISTENT" in same.stdout
    diff = subprocess.run(
        [sys.executable, str(_SCRIPT), "--diff",
         str(real_runs["default"]), str(real_runs["flip"])],
        capture_output=True, text=True, env=env)
    assert diff.returncode == 1
    assert "DIVERGE" in diff.stdout


# ============================================================ INDEX §3 sort-normalization

def test_sort_normalization_same_content_different_order_equal_fingerprint():
    """INDEX §3 flipped-defense: set-like fields (promoted[]/denied[]/
    input_observation_ids) and dict key write order must NOT change the node
    fingerprint — only genuine content changes may. Guards against a false
    'flipped' verdict driven purely by write order."""
    basis_a = {"convergence": 1.0, "window": 1.0, "acquisition_rank": 0.0, "risk": 0.0}
    basis_b = {"risk": 0.0, "acquisition_rank": 1.0, "window": 1.0, "convergence": 1.0}
    cost = {"n_transfers": 2, "duration_s": 10.0}
    payload_1 = {
        "knowledge_fingerprint": "fp",
        "policy": "evidence_gated",
        "promoted": [{"cand_id": "cand_a", "basis": basis_a, "wet_cost": cost},
                     {"cand_id": "cand_b", "basis": basis_b, "wet_cost": cost}],
        "denied": [{"cand_id": "cand_c", "basis": basis_a, "deny_reason": "gate_rank",
                    "wet_cost": cost}],
    }
    # same content, promoted list REVERSED + dict keys shuffled.
    payload_2 = {
        "policy": "evidence_gated",
        "knowledge_fingerprint": "fp",
        "denied": [{"deny_reason": "gate_rank", "cand_id": "cand_c",
                    "wet_cost": cost, "basis": dict(reversed(list(basis_a.items())))}],
        "promoted": [{"wet_cost": cost, "cand_id": "cand_b", "basis": basis_b},
                     {"basis": basis_a, "cand_id": "cand_a", "wet_cost": cost}],
    }
    fp1 = vrc._fingerprint(vrc._promotion_node_content(payload_1))
    fp2 = vrc._fingerprint(vrc._promotion_node_content(payload_2))
    assert fp1 == fp2  # write order does not leak into the fingerprint

    # ...but a REAL content change (a different promoted cand_id) DOES change it.
    payload_3 = json.loads(json.dumps(payload_1))
    payload_3["promoted"][0]["cand_id"] = "cand_z"
    fp3 = vrc._fingerprint(vrc._promotion_node_content(payload_3))
    assert fp3 != fp1

    # claim node volatile-id exclusion: the random obs UUIDs and their salted
    # content fingerprints must NOT enter the node fp (else same-seed runs diff as
    # 'flipped'); only the reproducible decision content + obs COUNT do.
    claim_1 = {"claim_id": "c", "claim_version": 2, "decision_status": "insufficient",
               "decision_fn_id": "fn", "input_observation_ids": ["obs_aaa", "obs_bbb", "obs_ccc"],
               "observation_fingerprints": {"obs_aaa": "sha256:1"},
               "consumed_knowledge_fingerprint": "fp", "statistic": {"value": 1.0, "name": "m"}}
    # different random obs ids + fingerprints, SAME decision content + obs count.
    claim_2 = dict(claim_1, input_observation_ids=["obs_zzz", "obs_yyy", "obs_xxx"],
                   observation_fingerprints={"obs_zzz": "sha256:9"})
    assert vrc._fingerprint(vrc._claim_node_content(claim_1)) == \
        vrc._fingerprint(vrc._claim_node_content(claim_2))
    # but a genuine effect change (the K2 sign flip) DOES change the node fp.
    claim_3 = dict(claim_1, statistic={"value": -1.0, "name": "m"})
    assert vrc._fingerprint(vrc._claim_node_content(claim_3)) != \
        vrc._fingerprint(vrc._claim_node_content(claim_1))
    # and a different observation COUNT (structural) changes it too.
    claim_4 = dict(claim_1, input_observation_ids=["obs_aaa", "obs_bbb"])
    assert vrc._fingerprint(vrc._claim_node_content(claim_4)) != \
        vrc._fingerprint(vrc._claim_node_content(claim_1))


# ============================================================ legal-quiet chain semantics
# A legal-quiet round is a first-class chain node, NOT a broken chain (letter
# red_to_blue/101 §3): its ``wet_leg_skipped{reason}`` is the chain evidence. These
# exercise the empty-proposal class (a real llm run) plus the strict guards that keep a
# silent skip or a self-contradicting skip red.


def _empty_proposal_stub():
    """A legal, knowledge-conditioned proposal whose candidates are all OUT-OF-POOL
    (hallucinated) — every one is dropped by mcl's _candidate_from_solvent, so the
    recorded prior_proposal carries candidates==[] and the round closes legal-quiet
    with wet_leg_skipped{no_candidate_proposed} (the empty-candidates case is blocked
    by the schema's min_length=1, so an all-dropped proposal is the reachable shape).
    Mirrors the stub form in tests/test_agent_backend_switch.py."""
    def fn(messages, **_kwargs) -> CompletionResult:
        pk = json.loads(messages[1]["content"])
        proposal = {
            "candidates": ["unobtainium", "phlogiston"],  # not in SOLVENT_POLARITY
            "basis": pk.get("claim_ids", [])[:1],
            "knowledge_fingerprint": pk["knowledge_fingerprint"],
            "rationale": "all-out-of-pool",
        }
        return CompletionResult(text=json.dumps({"proposals": [proposal]}),
                                usage={"input_tokens": 3, "output_tokens": 2})
    return fn


@pytest.fixture(scope="module")
def quiet_run(tmp_path_factory) -> Path:
    """A REAL two-round llm-mode run whose every round is an empty-proposal legal-quiet
    round (knowledge_updated -> prior_proposal(candidates==[]) ->
    wet_leg_skipped{no_candidate_proposed}); no dry/wet/promotion/claim work happens."""
    out = tmp_path_factory.mktemp("quiet") / "run"
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=out,
                 agent_backend={"mode": "llm", "completion_fn": _empty_proposal_stub()})
    return out


def _events(run: Path) -> list[dict]:
    return [json.loads(x) for x in
            run.joinpath("events.jsonl").read_text().splitlines() if x.strip()]


# ---- positive (a): a real empty-proposal quiet run verifies COMPLETE --------

def test_empty_proposal_quiet_round_verifies_complete(quiet_run):
    """The empty-proposal legal-quiet round is a complete chain: proposal(candidates==[])
    -> wet_leg_skipped{no_candidate_proposed} -> knowledge_updated, with no
    promotion/claim required. All three layers pass."""
    result = vrc.verify_run(quiet_run)
    assert result.ok, f"{result.code}: {result.message}"
    s = result.summary
    assert s["n_rounds"] == 2
    assert s["n_promotion_decisions"] == 0
    assert s["n_claim_decisions"] == 0
    assert s["final_exit_status"] == "success"
    # the skip is genuinely present in the stream (the evidence being recognized).
    skips = [e for e in _events(quiet_run) if e["kind"] == "wet_leg_skipped"]
    assert len(skips) == 2
    assert all(s["payload"]["reason"] == "no_candidate_proposed" for s in skips)


# ---- negative (b): delete the wet_leg_skipped guard -> red ------------------

def test_delete_wet_leg_skipped_breaks_quiet_round(quiet_run, tmp_path):
    """The wet_leg_skipped is load-bearing evidence: drop it on a COPY and the round
    loses its quiet legitimacy — it is judged as a normal round and goes red (no
    promotion_decision to thread)."""
    run = _copy_run(quiet_run, tmp_path / "quiet_noskip")
    kept = [e for e in _events(run) if e["kind"] != "wet_leg_skipped"]
    _rewrite_events(run, kept)

    result = vrc.verify_run(run)
    assert not result.ok
    assert result.layer == 3


# ---- negative (c): a skipped round that still adjudicates is a contradiction -

def test_quiet_round_with_claim_decision_is_contradiction(quiet_run, real_runs, tmp_path):
    """Forge a claim_decision into an empty-proposal quiet round on a COPY: the wet leg
    was skipped, so an adjudicated claim is self-contradicting -> layer-3 red."""
    run = _copy_run(quiet_run, tmp_path / "quiet_claim")
    # borrow a real claim_decision shape and retag it to the quiet round 0.
    real_claim = next(e for e in _events(real_runs["default"])
                      if e["kind"] == "claim_decision")
    inj = json.loads(json.dumps(real_claim))
    inj["payload"]["round_id"] = 0
    out = []
    for e in _events(run):
        out.append(e)
        if e["kind"] == "wet_leg_skipped" and (e.get("payload") or {}).get("round_id") == 0:
            out.append(inj)
    _rewrite_events(run, out)

    result = vrc.verify_run(run)
    assert not result.ok
    assert result.layer == 3
    assert result.code == "quiet_round_has_claim"


# ---- class-(b) hardening: a zero-promotion round MUST loudly skip -----------

def test_zero_promotion_round_requires_skip_evidence(real_runs, tmp_path):
    """Synthesize a zero-promotion round on a COPY of a real active run: empty round-0's
    promoted[] and drop its claim_decision. Without a wet_leg_skipped it is a SILENT
    zero-promotion round -> red; adding wet_leg_skipped{no_candidate_promoted} makes it a
    legal-quiet round -> COMPLETE (the class-(b) acceptance path)."""
    run = _copy_run(real_runs["default"], tmp_path / "zero_promo")
    events = _events(run)
    silent = []
    for e in events:
        p = e.get("payload") or {}
        if e["kind"] == "claim_decision" and p.get("round_id") == 0:
            continue  # the wet leg was 'skipped' -> no adjudication in round 0
        if e["kind"] == "promotion_decision" and p.get("round_id") == 0:
            e = json.loads(json.dumps(e))
            e["payload"]["promoted"] = []  # gate admitted nobody
        silent.append(e)
    _rewrite_events(run, list(silent))

    # (1) silent zero-promotion round -> red (the strict guard).
    silent_result = vrc.verify_run(run)
    assert not silent_result.ok
    assert silent_result.layer == 3
    assert silent_result.code == "zero_promotion_without_skip"

    # (2) add the loud skip right after round-0's promotion -> legal-quiet -> COMPLETE.
    loud = []
    for e in silent:
        loud.append(e)
        if e["kind"] == "promotion_decision" and (e.get("payload") or {}).get("round_id") == 0:
            loud.append({"ts": e.get("ts"), "kind": "wet_leg_skipped",
                         "payload": {"round_id": 0, "reason": "no_candidate_promoted"}})
    _rewrite_events(run, loud)

    loud_result = vrc.verify_run(run)
    assert loud_result.ok, f"{loud_result.code}: {loud_result.message}"


# ---- diff: a static (quiet) round diverges from an active round -------------

def test_diff_quiet_vs_active_diverges(quiet_run, real_runs):
    """A quiet run and an active run diverge: the quiet round 0's empty proposal (then a
    wet_leg_skipped node) is not the active run's populated proposal / claim chain."""
    result = vrc.diff_decision_chain(quiet_run, real_runs["default"])
    assert result.diverged
    assert result.first_divergence["round"] == 0
