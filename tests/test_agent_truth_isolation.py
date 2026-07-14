"""Truth-isolation acceptance for the LLM prompt path (M18 §0.2).

The hidden truth surface (adapters/wet/sim_reader.py: TRUTH_PROFILES / TruthSurface / the
``truth_records`` / ``true_response`` server-side sidecar) must NEVER reach an agent prompt. The
backend enforces this with a static structural guard on the prompt-construction input face. These
tests are the "删守卫必红" negatives: drop ``_assert_prompt_isolated`` from ``_render_prompt`` (or
its helper) and (b)/(c) go red.

All checks are offline — no completion callable, no network, no API key.
"""

from __future__ import annotations

import json
import types

import pytest

from expos.agent.llm_backend import (
    LLMBackend,
    LLMBackendError,
    _FORBIDDEN_MARKER_KEYS,
    _assert_prompt_isolated,
)
from expos.kernel.knowledge import compile_knowledge
from expos.kernel.objects import HypothesisObject
from expos.kernel.store import ReadOnlyRunView

_EXPORTED_AT = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------- fixtures / helpers


def _knowledge():
    hyps = [
        HypothesisObject(
            hypothesis_id="hyp_polar",
            statement="polar solvents respond higher",
            evidence_refs=["c_polar"],
        )
    ]
    return compile_knowledge([{"claim_id": "c_polar", "status": "supported"}], hyps)


def _view():
    return ReadOnlyRunView(run_root="/tmp/run", exported_at=_EXPORTED_AT, observations=())


def _legal_completion():
    from expos.agent.llm_backend import CompletionResult

    def _complete(messages, **kwargs):
        # Echo the fingerprint the backend rendered into the prompt (stands in for the model).
        fp = None
        for m in messages:
            try:
                obj = json.loads(m["content"])
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and "knowledge_fingerprint" in obj:
                fp = obj["knowledge_fingerprint"]
                break
        body = {"proposals": [{
            "candidates": ["cand_a"], "basis": ["c_polar"],
            "knowledge_fingerprint": fp, "rationale": "ok",
        }]}
        return CompletionResult(text=json.dumps(body))

    return _complete


def _backend():
    return LLMBackend(_legal_completion(), _knowledge())


def _tainted_knowledge_stub(marker_key: str):
    """A KnowledgeView-shaped stand-in that WOULD render a legal prompt (it has a fingerprint and
    an empty hypotheses list) except that it also smuggles one truth-side marker attribute.

    The empty-but-valid shape is deliberate: with the guard deleted, ``_render_prompt`` renders it
    without error, so the ``pytest.raises`` below fails — which is exactly the "删守卫必红" signal.
    """
    return types.SimpleNamespace(
        knowledge_fingerprint="fp-stub",
        hypotheses=[],
        **{marker_key: "leaked-truth-payload"},
    )


# ================================================================ (a) normal path is clean


def test_real_knowledge_view_renders_without_tripping_guard():
    """A legitimately compiled KnowledgeView passes the guard: its keys are all whitelisted."""
    b = _backend()
    kv = _knowledge()
    prompt = b._render_prompt(kv, task="propose")  # must NOT raise
    payload = json.loads(prompt)
    assert payload["knowledge_fingerprint"] == kv.knowledge_fingerprint


def test_full_suggest_does_not_trip_guard():
    """End-to-end: a normal suggest() round over a real KnowledgeView is unaffected by the guard."""
    b = _backend()
    out = b.suggest(_view(), round_id=1)
    assert len(out) == 1
    assert out[0].content["knowledge_fingerprint"] == _knowledge().knowledge_fingerprint


def test_guard_passes_on_bare_knowledge_view_object():
    _assert_prompt_isolated(_knowledge(), where="unit")  # no raise


# ================================================================ (b) tainted input loud-fails


def test_tainted_input_through_render_prompt_raises():
    """Drive the REAL prompt-construction path with a truth-tainted input -> loud raise.

    The stub is otherwise renderable, so this failure is attributable to the guard alone."""
    b = _backend()
    tainted = _tainted_knowledge_stub("truth_profile")
    with pytest.raises(LLMBackendError, match="truth-isolation violation"):
        b._render_prompt(tainted, task="propose")


def test_guard_scans_nested_pydantic_dump():
    """The guard recurses through pydantic dumps: a marker nested under a model field is caught."""
    from pydantic import BaseModel

    class _Leaky(BaseModel):
        knowledge_fingerprint: str = "fp"
        truth_records: list = []  # nested truth-side marker key

    with pytest.raises(LLMBackendError):
        _assert_prompt_isolated(_Leaky(), where="unit")


def test_guard_scans_nested_mapping_and_sequence():
    payload = {"hypotheses": [{"statement": "fine", "true_response": 0.42}]}
    with pytest.raises(LLMBackendError):
        _assert_prompt_isolated(payload, where="unit")


# ================================================================ (c) every blacklist key fires


@pytest.mark.parametrize("marker_key", sorted(_FORBIDDEN_MARKER_KEYS))
def test_each_blacklist_key_trips_the_guard(marker_key):
    """One shot per blacklist entry: each truth-side marker key, injected into an otherwise-legal
    input, trips the guard through the real ``_render_prompt`` path."""
    b = _backend()
    tainted = _tainted_knowledge_stub(marker_key)
    with pytest.raises(LLMBackendError, match="truth-isolation violation"):
        b._render_prompt(tainted, task="propose")


@pytest.mark.parametrize("marker_key", sorted(_FORBIDDEN_MARKER_KEYS))
def test_blacklist_key_is_case_insensitive(marker_key):
    with pytest.raises(LLMBackendError):
        _assert_prompt_isolated({marker_key.upper(): 1}, where="unit")


# ================================================================ narrowness (no false positives)


@pytest.mark.parametrize("safe_key", [
    "knowledge_fingerprint", "effective_status", "stored_status", "status",
    "hypothesis_id", "statement", "evidence", "claim_id", "signal", "truthfulness",
])
def test_whitelisted_and_lookalike_keys_do_not_trip(safe_key):
    """Narrow-by-design: exact key match only — real KnowledgeView keys and near-miss words
    (e.g. 'truthfulness') must NOT be flagged."""
    _assert_prompt_isolated({safe_key: "value"}, where="unit")  # no raise
