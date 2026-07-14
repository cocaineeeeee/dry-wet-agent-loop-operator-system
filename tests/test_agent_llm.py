"""LLM AgentBackend acceptance (M18, INDEX_M18_LLM_READY).

Covers: forced ProposalSchema (fingerprint required / forged-or-missing rejected /
evidence-gated basis); validate-and-reask loop (reask on violation -> success; exhaustion ->
legal-quiet + structured failure; provider outage -> legal-quiet); permission boundary
(Protocol conformance, no write API, no store handle, products feed ProposalQueue); usage
accounting into content; canary regression (TemplateBackend still bit-exact); and a G1
distributional discriminator (P(proposal|K) != P(proposal|K_flip), same-K samples all legal).

All completion callables are mocks — zero network, zero API key.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from expos.agent import llm_backend as llm_mod
from expos.agent.backends import AgentBackend, TemplateBackend
from expos.agent.llm_backend import (
    CompletionResult,
    LLMBackend,
    LLMBackendError,
    ProposalBatch,
    ProposalSchema,
)
from expos.agent.views import ProposalQueue
from expos.kernel.knowledge import compile_knowledge
from expos.kernel.objects import (
    Actor,
    DecisionKind,
    HypothesisObject,
    PROPOSAL_KINDS,
)
from expos.kernel.store import ReadOnlyRunView

_EXPORTED_AT = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------- fixtures / helpers


def _knowledge(polar_status: str = "supported"):
    """Compile a two-claim knowledge view; flip polar_status to invert the hypothesis."""
    hyps = [
        HypothesisObject(
            hypothesis_id="hyp_polar",
            statement="polar solvents respond higher",
            evidence_refs=["c_polar"],
        )
    ]
    return compile_knowledge([{"claim_id": "c_polar", "status": polar_status}], hyps)


def _view(observations=()):
    return ReadOnlyRunView(
        run_root="/tmp/run", exported_at=_EXPORTED_AT, observations=tuple(observations)
    )


def _prompt_payload(messages) -> dict:
    """Locate the rendered knowledge prompt among the messages (robust to reask messages
    appended after it — the prompt is the JSON message carrying knowledge_fingerprint)."""
    for m in messages:
        try:
            obj = json.loads(m["content"])
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and "knowledge_fingerprint" in obj:
            return obj
    raise AssertionError("no rendered knowledge prompt found in messages")


def _fp_from_messages(messages) -> str:
    """Read the fingerprint the backend rendered into the user prompt (what a real model would
    echo back — the mock stands in for that echo)."""
    return _prompt_payload(messages)["knowledge_fingerprint"]


def _legal_completion(usage=None):
    """A mock that always returns one legal proposal echoing the prompted fingerprint."""

    def _complete(messages, **kwargs):
        fp = _fp_from_messages(messages)
        body = {"proposals": [{
            "candidates": ["cand_a", "cand_b"],
            "basis": ["c_polar"],
            "knowledge_fingerprint": fp,
            "rationale": "polar hypothesis supported",
        }]}
        return CompletionResult(text=json.dumps(body), usage=usage or {})

    return _complete


# ================================================================ ProposalSchema gate


def test_schema_valid_instance_passes_without_context():
    p = ProposalSchema(
        candidates=["x"], basis=["c1"], knowledge_fingerprint="deadbeef", rationale="ok"
    )
    assert p.candidates == ["x"] and p.knowledge_fingerprint == "deadbeef"


def test_schema_missing_fingerprint_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProposalSchema(candidates=["x"], basis=[])  # no knowledge_fingerprint field


def test_schema_forged_fingerprint_rejected_by_context():
    from pydantic import ValidationError

    ctx = {"knowledge_fingerprint": "REAL", "claim_ids": {"c1"}}
    with pytest.raises(ValidationError):
        ProposalSchema.model_validate(
            {"candidates": ["x"], "basis": ["c1"], "knowledge_fingerprint": "FORGED"},
            context=ctx,
        )


def test_schema_basis_outside_ledger_rejected_by_context():
    from pydantic import ValidationError

    ctx = {"knowledge_fingerprint": "REAL", "claim_ids": {"c1"}}
    with pytest.raises(ValidationError):
        ProposalSchema.model_validate(
            {"candidates": ["x"], "basis": ["c_ghost"], "knowledge_fingerprint": "REAL"},
            context=ctx,
        )


def test_schema_legal_under_context_passes():
    ctx = {"knowledge_fingerprint": "REAL", "claim_ids": {"c1", "c2"}}
    batch = ProposalBatch.model_validate(
        {"proposals": [{
            "candidates": ["x"], "basis": ["c1"], "knowledge_fingerprint": "REAL"
        }]},
        context=ctx,
    )
    assert len(batch.proposals) == 1


# ================================================================ validate-and-reask


def test_suggest_happy_path_mints_gated_proposals():
    kv = _knowledge()
    b = LLMBackend(_legal_completion(), kv, model_id="mock/m", provider="mock")
    out = b.suggest(_view(), round_id=1)
    assert len(out) == 1
    rec = out[0]
    assert rec.actor == Actor.AGENT and rec.kind == DecisionKind.ACTION_PROPOSAL
    assert rec.content["knowledge_fingerprint"] == kv.knowledge_fingerprint
    assert rec.content["basis"] == ["c_polar"]
    assert rec.accepted is None  # unadjudicated
    assert b.recent_failures() == []


def test_reask_then_success_is_second_round_trip():
    kv = _knowledge()
    calls = {"n": 0}

    def _complete(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # First attempt: forged fingerprint -> validation error -> model-side reask.
            body = {"proposals": [{
                "candidates": ["x"], "basis": ["c_polar"],
                "knowledge_fingerprint": "FORGED", "rationale": "",
            }]}
        else:
            body = {"proposals": [{
                "candidates": ["x"], "basis": ["c_polar"],
                "knowledge_fingerprint": _fp_from_messages(messages), "rationale": "",
            }]}
        return CompletionResult(text=json.dumps(body))

    b = LLMBackend(_complete, kv)
    out = b.suggest(_view(), round_id=0)
    assert calls["n"] == 2, "should reask exactly once, then succeed"
    assert len(out) == 1
    assert out[0].content["knowledge_fingerprint"] == kv.knowledge_fingerprint
    assert b.recent_failures() == []


def test_reask_exhaustion_is_legal_quiet_plus_failure_record():
    kv = _knowledge()
    calls = {"n": 0}

    def _always_forged(messages, **kwargs):
        calls["n"] += 1
        body = {"proposals": [{
            "candidates": ["x"], "basis": ["c_polar"],
            "knowledge_fingerprint": "NEVER_RIGHT", "rationale": "",
        }]}
        return CompletionResult(text=json.dumps(body))

    b = LLMBackend(_always_forged, kv, max_reasks=2)
    out = b.suggest(_view(), round_id=3)
    assert out == [], "exhausted reask budget -> legal-quiet empty proposal list"
    assert calls["n"] == 3, "1 initial + 2 reasks (N=2)"
    fails = b.recent_failures()
    assert len(fails) == 1
    assert fails[0].reason == "reask_exhausted"
    assert fails[0].attempts == 3
    assert fails[0].knowledge_fingerprint == kv.knowledge_fingerprint


def test_provider_outage_is_legal_quiet_plus_failure_record():
    kv = _knowledge()

    def _boom(messages, **kwargs):
        raise RuntimeError("all providers down")

    b = LLMBackend(_boom, kv)
    out = b.suggest(_view(), round_id=2)
    assert out == []
    fails = b.recent_failures()
    assert len(fails) == 1 and fails[0].reason == "provider_error"


def test_malformed_json_triggers_reask():
    kv = _knowledge()
    calls = {"n": 0}

    def _complete(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return CompletionResult(text="not json at all {{{")
        body = {"proposals": [{
            "candidates": ["x"], "basis": [], "knowledge_fingerprint":
            _fp_from_messages(messages),
        }]}
        return CompletionResult(text=json.dumps(body))

    b = LLMBackend(_complete, kv)
    out = b.suggest(_view(), round_id=0)
    assert calls["n"] == 2 and len(out) == 1


def test_gated_method_without_knowledge_loud_fails():
    b = LLMBackend(_legal_completion(), knowledge_provider=None)
    with pytest.raises(LLMBackendError):
        b.suggest(_view(), round_id=0)


def test_knowledge_provider_callable_is_resolved_per_call():
    calls = {"n": 0}

    def _kp(view):
        calls["n"] += 1
        return _knowledge()

    b = LLMBackend(_legal_completion(), knowledge_provider=_kp)
    b.suggest(_view(), round_id=0)
    assert calls["n"] == 1  # resolved once for the call


# ================================================================ usage accounting (Q4)


def test_usage_block_rides_in_content():
    kv = _knowledge()
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
             "response_ms": 42.0, "response_cost": 0.0001}
    b = LLMBackend(_legal_completion(usage), kv, model_id="mock/m", provider="mockp", seed=9)
    rec = b.suggest(_view(), round_id=1)[0]
    u = rec.content["usage"]
    assert u["total_tokens"] == 15 and u["response_ms"] == 42.0
    assert u["model_id"] == "mock/m" and u["provider"] == "mockp" and u["seed"] == 9


def test_usage_key_present_even_without_provider_usage():
    kv = _knowledge()
    b = LLMBackend(_legal_completion(), kv, model_id="mock/m")
    rec = b.suggest(_view(), round_id=1)[0]
    assert "usage" in rec.content and rec.content["usage"]["model_id"] == "mock/m"


# ================================================================ permission boundary (Axiom 7)


def test_llm_backend_satisfies_agent_protocol():
    b = LLMBackend(_legal_completion(), _knowledge())
    assert isinstance(b, AgentBackend)


WRITE_WORDS = ("save", "append", "write", "delete", "update", "remove")
FORBIDDEN_IMPORTS = (
    "RunStore", "lifecycle", "adapters", "planner", "models",
    "submit_proposal", "validate_proposal", "adjudicate", "reclassify",
    "route_observation",
)


def test_llm_module_has_no_write_public_api():
    offenders = [
        n for n in dir(llm_mod)
        if not n.startswith("_") and any(w in n.lower() for w in WRITE_WORDS)
    ]
    assert offenders == [], f"llm_backend exposes write-type public API: {offenders}"


def test_llm_module_imports_no_forbidden_targets():
    with open(llm_mod.__file__, encoding="utf-8") as f:
        import_lines = [ln for ln in f if ln.lstrip().startswith(("import ", "from "))]
    src = "".join(import_lines)
    hits = [tok for tok in FORBIDDEN_IMPORTS if tok in src]
    assert hits == [], f"llm_backend import touches forbidden symbols: {hits}"


def test_backend_holds_no_store_write_handle():
    """Structural: the backend carries only an injected callable + knowledge + config; nothing
    on it is a store/write surface (it cannot write even if the model misbehaves)."""
    b = LLMBackend(_legal_completion(), _knowledge())
    for val in vars(b).values():
        assert not hasattr(val, "append_event")
        assert not hasattr(val, "append_decision")
        assert not hasattr(val, "save_observation")


def test_products_feed_proposal_queue_legally():
    kv = _knowledge()
    b = LLMBackend(_legal_completion(), kv)
    q = ProposalQueue()
    for rec in b.suggest(_view(), round_id=1):
        q.put(rec)  # actor=agent + kind∈PROPOSAL_KINDS -> accepted
    assert len(q) == 1
    for rec in b.propose_priors(_view(), round_id=1):
        assert rec.kind in PROPOSAL_KINDS


# ================================================================ from_provider (lazy)


def test_from_provider_validates_route_at_construction():
    if importlib.util.find_spec("litellm") is None:
        # No litellm installed: CONSTRUCTION must loud-fail with a clear message, proving
        # import is lazy at module level (the import above already succeeded without litellm).
        with pytest.raises(LLMBackendError):
            LLMBackend.from_provider("openai/gpt-4o-mini")
        return
    # Valid route -> a callable, assembled without any network traffic.
    fn = LLMBackend.from_provider("openai/gpt-4o-mini")
    assert callable(fn)
    # Invalid route (the Stage-1 live incident: 'litellm/' is a library name, not a route)
    # -> refused at assembly, never mid-loop.
    with pytest.raises(LLMBackendError, match="invalid litellm route"):
        LLMBackend.from_provider("litellm/gpt-4o")


# ================================================================ canary regression


def test_template_backend_still_bit_exact_and_shares_protocol():
    """The deterministic canary must be untouched by the LLM backend's arrival: TemplateBackend
    stays bit-exact and both backends satisfy the one AgentBackend Protocol (INDEX layer 4)."""
    from tests.test_agent import make_obs, make_view

    tb = TemplateBackend()
    view = make_view([make_obs("o1"), make_obs("o2")])
    assert tb.suggest(view, round_id=1) == tb.suggest(view, round_id=1)
    assert isinstance(tb, AgentBackend)
    assert isinstance(LLMBackend(_legal_completion(), _knowledge()), AgentBackend)


# ================================================================ G1 distributional (Q2)


def _knowledge_conditioned_completion():
    """Mock whose proposal distribution is a function of the compiled knowledge it is handed.

    It branches on the polar hypothesis's effective_status (SUPPORTED -> rank 'polar_high'
    first; REJECTED -> 'nonpolar_high' first), echoes the prompted fingerprint, and jitters the
    tail ordering by a call counter so each sample is genuinely drawn (yet the modal head — the
    knowledge-determined signal — stays fixed). This is the LLM-side stand-in the K-E
    discriminator measures."""
    counter = {"n": 0}

    def _complete(messages, **kwargs):
        payload = _prompt_payload(messages)
        fp = payload["knowledge_fingerprint"]
        status = payload["hypotheses"][0]["effective_status"]
        counter["n"] += 1
        jitter = counter["n"] % 3
        if status == "SUPPORTED":
            head, tail = "polar_high", ["mid", "nonpolar_high"]
        else:  # REJECTED / OPEN
            head, tail = "nonpolar_high", ["mid", "polar_high"]
        candidates = [head] + tail[jitter % 2:] + tail[: jitter % 2]
        body = {"proposals": [{
            "candidates": candidates, "basis": ["c_polar"],
            "knowledge_fingerprint": fp, "rationale": f"status={status}",
        }]}
        return CompletionResult(text=json.dumps(body))

    return _complete


def test_g1_proposal_distribution_is_a_function_of_knowledge():
    """P(proposal|K) is separable from P(proposal|K_flip) and same-K samples stay legal.

    Layer 0 (knowledge face) stays bit-exact: the two fingerprints differ. Layer 2
    (discriminative): the modal head candidate under frozen K is disjoint from the flipped K —
    knowledge consumption is real, not performative — while every sample is schema-legal."""
    K = _knowledge("supported")
    K_flip = _knowledge("rejected")
    assert K.knowledge_fingerprint != K_flip.knowledge_fingerprint  # layer 0, bit-exact

    n = 12
    b_K = LLMBackend(_knowledge_conditioned_completion(), K)
    b_flip = LLMBackend(_knowledge_conditioned_completion(), K_flip)

    heads_K, heads_flip = [], []
    for _ in range(n):
        recs = b_K.suggest(_view(), round_id=0)
        assert len(recs) == 1, "every same-K sample must be schema-legal"
        assert recs[0].content["knowledge_fingerprint"] == K.knowledge_fingerprint
        heads_K.append(recs[0].content["candidates"][0])

        recs_f = b_flip.suggest(_view(), round_id=0)
        assert len(recs_f) == 1
        assert recs_f[0].content["knowledge_fingerprint"] == K_flip.knowledge_fingerprint
        heads_flip.append(recs_f[0].content["candidates"][0])

    # Distributional separability: the modal head under K and under K_flip are disjoint sets.
    assert set(heads_K).isdisjoint(set(heads_flip)), (
        f"proposal distribution did not shift with knowledge: {set(heads_K)} vs "
        f"{set(heads_flip)}"
    )
    # Same-K stability: the frozen-K modal head is a single value (noise band, not divergence).
    assert len(set(heads_K)) == 1 and heads_K[0] == "polar_high"
    assert len(set(heads_flip)) == 1 and heads_flip[0] == "nonpolar_high"
