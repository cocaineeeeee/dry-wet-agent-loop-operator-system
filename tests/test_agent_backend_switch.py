"""M18 agent-backend three-mode switch — discriminative acceptance.

The switch (expos.agent.backend_select + expos.mcl) resolves ``agent_backend``
``{mode: template|shadow|llm, provider|completion_fn}`` ONCE at construction into a
strategy object (EXP002 injection discipline). These bodies inject STUB completion
callables (zero network, zero API key) and assert, discriminative-first:

  1. template / None is the BYTE-IDENTICAL regression twin (zero agent_* events);
  2. shadow's decision face == template's, filtered by the versioned whitelist
     DECISION_FACE_KINDS.v1 (including the shadow events in the comparison must FAIL);
  3. exactly one agent_shadow_proposal / round with all required keys, prompt_sha256
     stable under frozen knowledge, and an invalid-fingerprint proposal records
     schema_valid/fingerprint_match false while leaving the decision untouched;
  4. llm mode drives the decision from the stub (not the template) and, on reask
     exhaustion, emits agent_generation_failed + closes a legal-quiet round;
  5. an invalid mode and a 'litellm/...' provider both fail loud BEFORE any round;
  6. resuming a shadow run does not re-emit agent_shadow_proposal (count/round == 1).

Each body drives the real dual-leg MCL (real out-of-process PySCF dry jobs + the
in-process plate-reader wet leg), so only the AGENT leg is stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from expos.agent.llm_backend import CompletionResult
from expos.errors import ExposError
from expos.kernel.store import RunStore
from expos.mcl import run_mcl_loop

_DOMAIN = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"


# ----------------------------------------------------------------- decision-face whitelist

#: DECISION_FACE_KINDS.v1 (docs/M18_LLM_LIVE_SMOKE.md §2, letter 094 §2): the versioned
#: whitelist the "decision face bitwise equal" comparison is defined over. agent_shadow_proposal
#: is CONSTRUCTIVELY EXCLUDED (its usage/latency/response-id are non-deterministic — including
#: it would make bitwise equality impossible). Bumping this set is a version change (v2).
#: PROMOTED to an importable kernel constant (Phase 4 item #5) — one authority for producers
#: and consumers; imported here (no longer defined locally).
from expos.kernel.store import DECISION_FACE_KINDS_V1  # noqa: E402


def _decision_face(run_dir: Path, kinds: frozenset[str] = DECISION_FACE_KINDS_V1) -> list[tuple]:
    """Project the run's DECISION face over the whitelist, dropping every non-deterministic
    field (seq/ts/usage/latency). Pure function of (seed, knowledge) => byte-identical across
    same-seed runs. ``kinds`` is a parameter only so the kill test can widen it."""
    store = RunStore(run_dir, create=False)
    face: list[tuple] = []
    for ev in store.read_events():
        k, p = ev["kind"], ev["payload"]
        if k not in kinds:
            continue
        if k == "knowledge_updated":
            face.append((k, p["fingerprint"], p["n_hypotheses"], p["n_claims"]))
        elif k == "promotion_decision":
            face.append((k, p["round_id"], p["knowledge_fingerprint"],
                         tuple(x["cand_id"] for x in p["promoted"])))
        elif k == "decision":
            c = p["content"]
            # candidates + basis + fingerprint — NOT usage (non-ABI, may vary by provider).
            face.append((k, p["round_id"], tuple(c.get("candidates", ())),
                         tuple(c.get("basis", ())), c.get("knowledge_fingerprint")))
        elif k == "run_stop":
            face.append((k, p.get("exit_status")))
        elif k == "agent_shadow_proposal":  # only reachable when the kill test widens `kinds`
            face.append((k, p["round_id"], p["schema_valid"], p["fingerprint_match"]))
    return face


# ----------------------------------------------------------------- stub completion callables

def _prompt_knowledge(messages) -> dict:
    """Read the frozen-knowledge prompt the backend rendered (messages[1] is the user message;
    it stays stable across reasks)."""
    return json.loads(messages[1]["content"])


def _echo_stub(order: list[str]):
    """A well-behaved knowledge-conditioned stub: echoes the exact knowledge_fingerprint it was
    given and cites an in-ledger claim_id, proposing ``order``. Mints a legal proposal."""
    def fn(messages, **_kwargs) -> CompletionResult:
        pk = _prompt_knowledge(messages)
        claim_ids = pk.get("claim_ids", [])
        proposal = {
            "candidates": list(order),
            "basis": claim_ids[:1],
            "knowledge_fingerprint": pk["knowledge_fingerprint"],
            "rationale": "stub",
        }
        return CompletionResult(
            text=json.dumps({"proposals": [proposal]}),
            usage={"input_tokens": 11, "output_tokens": 7, "system_fingerprint": "fp_stub"},
        )
    return fn


def _bad_fingerprint_stub(order: list[str]):
    """Structurally valid but echoes a WRONG fingerprint — the fingerprint-echo gate fails, so
    the backend reasks and (the stub never corrects) exhausts to an empty legal-quiet proposal."""
    def fn(messages, **_kwargs) -> CompletionResult:
        pk = _prompt_knowledge(messages)
        proposal = {
            "candidates": list(order),
            "basis": pk.get("claim_ids", [])[:1],
            "knowledge_fingerprint": "DEADBEEF_not_the_real_fingerprint",
            "rationale": "stub-bad-fp",
        }
        return CompletionResult(
            text=json.dumps({"proposals": [proposal]}),
            usage={"input_tokens": 9, "output_tokens": 4},
        )
    return fn


# ----------------------------------------------------------------- 1. template regression twin

def test_template_is_byte_identical_regression_twin(tmp_path):
    """None (no kwarg) and mode=template produce an identical decision face and emit ZERO
    agent_* events across a two-round run."""
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "default")  # no kwarg
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "tmpl",
                 agent_backend={"mode": "template"})

    assert _decision_face(tmp_path / "default") == _decision_face(tmp_path / "tmpl")

    for name in ("default", "tmpl"):
        kinds = {e["kind"] for e in RunStore(tmp_path / name, create=False).read_events()}
        assert not any(k.startswith("agent_") for k in kinds), f"{name} emitted agent_* events"


# ----------------------------------------------------------------- 2. shadow decision-inert

def test_shadow_decision_face_equals_template(tmp_path):
    """Same-seed shadow run vs template run: decision face bitwise equal after filtering by
    DECISION_FACE_KINDS.v1. Including the shadow events in the comparison must FAIL (kill)."""
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "tmpl",
                 agent_backend={"mode": "template"})
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "shadow",
                 agent_backend={"mode": "shadow",
                                "completion_fn": _echo_stub(["hexane", "ethanol"])})

    # filtered by the versioned whitelist: shadow is decision-inert.
    assert _decision_face(tmp_path / "tmpl") == _decision_face(tmp_path / "shadow")

    # KILL: widen the whitelist to include the shadow events -> the faces MUST diverge
    # (the template run has zero shadow events, the shadow run has one per round), proving the
    # constructive exclusion is load-bearing.
    widened = DECISION_FACE_KINDS_V1 | {"agent_shadow_proposal"}
    assert _decision_face(tmp_path / "tmpl", widened) != _decision_face(tmp_path / "shadow", widened)


# ----------------------------------------------------------------- 3. shadow event payload

_SHADOW_REQUIRED = {"round_id", "schema_valid", "fingerprint_match", "basis_subset",
                    "order_diff", "usage", "prompt_sha256", "validator_versions"}


def test_shadow_event_payload_well_behaved(tmp_path):
    """Exactly one agent_shadow_proposal per round; all required keys present; prompt_sha256
    stable across rounds under frozen knowledge; validator_versions correct; gates all pass."""
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "run",
                 agent_backend={"mode": "shadow",
                                "completion_fn": _echo_stub(["ethanol", "acetonitrile"])})
    store = RunStore(tmp_path / "run", create=False)
    events = store.read_events("agent_shadow_proposal")

    assert len(events) == 2  # exactly one per round
    assert {e["payload"]["round_id"] for e in events} == {0, 1}
    for e in events:
        p = e["payload"]
        assert _SHADOW_REQUIRED <= set(p), f"missing keys: {_SHADOW_REQUIRED - set(p)}"
        assert p["validator_versions"] == ["fingerprint_echo@v1", "basis_subset@v1"]
        assert p["schema_valid"] is True
        assert p["fingerprint_match"] is True
        assert p["basis_subset"] is True

    # frozen knowledge (NullCertification) => prompt_sha256 bit-identical across rounds.
    assert events[0]["payload"]["prompt_sha256"] == events[1]["payload"]["prompt_sha256"]
    # the whole stream still passes the payload-required gate.
    assert store.validate_event_payloads(store.read_events()) == []


def test_shadow_invalid_fingerprint_records_false_without_touching_decision(tmp_path):
    """An invalid-fingerprint proposal records schema_valid/fingerprint_match false, yet the
    loop decision is unaffected (identical to a plain template run)."""
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "tmpl",
                 agent_backend={"mode": "template"})
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "shadow",
                 agent_backend={"mode": "shadow",
                                "completion_fn": _bad_fingerprint_stub(["hexane"])})

    store = RunStore(tmp_path / "shadow", create=False)
    (ev,) = store.read_events("agent_shadow_proposal")
    p = ev["payload"]
    assert p["schema_valid"] is False
    assert p["fingerprint_match"] is False

    # decision path untouched: same-seed decision face equals the template twin.
    assert _decision_face(tmp_path / "tmpl") == _decision_face(tmp_path / "shadow")


# ----------------------------------------------------------------- 4. llm mode drives decisions

def test_llm_mode_drives_decision_order_from_stub(tmp_path):
    """A well-behaved stub drives the decision: the recorded proposal order follows the stub,
    not the deterministic template ordering."""
    # template proposal order (base knowledge prefers polar): recorded for contrast.
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "tmpl",
                 agent_backend={"mode": "template"})
    template_order = list(
        RunStore(tmp_path / "tmpl", create=False).list_decisions()[0].content["candidates"]
    )

    stub_order = ["hexane", "acetone", "acetonitrile", "ethanol"]
    assert stub_order != template_order  # the stub deliberately disagrees with the template
    summary = run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "llm",
                           agent_backend={"mode": "llm",
                                          "completion_fn": _echo_stub(stub_order)})

    assert summary["rounds_completed"] == 1
    store = RunStore(tmp_path / "llm", create=False)
    prop = store.list_decisions()[0]
    assert list(prop.content["candidates"]) == stub_order  # LLM order drove the decision
    assert "usage" in prop.content  # 方案 A: agent proposal carries a usage block
    stops = store.read_events("run_stop")
    assert stops[-1]["payload"]["exit_status"] == "success"
    assert store.read_events("agent_generation_failed") == []  # no failure on the happy path


def test_llm_reask_exhaustion_emits_failure_and_closes_quiet_round(tmp_path):
    """An always-invalid stub exhausts the reask budget -> agent_generation_failed emitted, the
    empty-proposal legal-quiet round completes, run_stop=success."""
    summary = run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "run",
                           agent_backend={"mode": "llm",
                                          "completion_fn": _bad_fingerprint_stub(["ethanol"])})

    assert summary["rounds_completed"] == 1
    store = RunStore(tmp_path / "run", create=False)
    (fail,) = store.read_events("agent_generation_failed")
    fp = fail["payload"]
    assert {"round_id", "failure_kind", "attempts", "usage", "prompt_sha256"} <= set(fp)
    assert fp["failure_kind"] == "reask_exhausted"
    assert fp["attempts"] >= 1
    # legal-quiet: no candidate proposed => the wet leg is loudly skipped, the run still succeeds.
    skips = store.read_events("wet_leg_skipped")
    assert any(s["payload"]["reason"] == "no_candidate_proposed" for s in skips)
    stops = store.read_events("run_stop")
    assert stops[-1]["payload"]["exit_status"] == "success"
    assert store.validate_event_payloads(store.read_events()) == []


# ----------------------------------------------------------------- 5. construction-time failures

def test_invalid_mode_fails_loud_before_any_round(tmp_path):
    out = tmp_path / "run"
    with pytest.raises(ExposError):
        run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=out,
                     agent_backend={"mode": "shdow"})  # typo
    # loud at construction, before the store/reader/rounds: no run artifacts landed.
    assert not (out / "events.jsonl").exists()


def test_litellm_prefixed_provider_fails_loud_before_any_round(tmp_path):
    out = tmp_path / "run"
    with pytest.raises(ExposError):
        run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=out,
                     agent_backend={"mode": "llm", "provider": "litellm/gpt-4o-mini"})
    assert not (out / "events.jsonl").exists()


# ----------------------------------------------------------------- 6. resume discipline

def test_resume_does_not_reemit_shadow_events(tmp_path):
    """Resuming a shadow run does not re-run completed rounds -> no duplicate
    agent_shadow_proposal (count per round == 1)."""
    out = tmp_path / "run"
    backend = {"mode": "shadow", "completion_fn": _echo_stub(["ethanol", "hexane"])}

    # round 0 runs and its shadow event lands; then resume continues into round 1.
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=out, agent_backend=backend)
    run_mcl_loop(_DOMAIN, rounds=2, seed=7, out_dir=out, resume=True, agent_backend=backend)

    store = RunStore(out, create=False)
    events = store.read_events("agent_shadow_proposal")
    rounds = [e["payload"]["round_id"] for e in events]
    assert sorted(rounds) == [0, 1]  # exactly one per round, no round-0 duplicate on resume
