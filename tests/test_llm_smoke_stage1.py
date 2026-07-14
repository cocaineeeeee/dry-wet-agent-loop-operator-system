"""Offline smoke of the M18 Stage-1 live-ping script (scripts/llm_smoke_stage1.py).

Drives ``run_stage1``/``main`` end-to-end with an INJECTED stub completion — zero network, zero
API key, zero litellm. Proves the script's judgment logic, audit persistence and cost guardrails
without ever touching a real provider. The live run is the main session's job.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "llm_smoke_stage1.py"
_spec = importlib.util.spec_from_file_location("llm_smoke_stage1", _SCRIPT)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

from expos.agent.llm_backend import CompletionResult  # noqa: E402


def _stub_completion():
    """A well-behaved fake model: echoes the fingerprint the backend rendered into the prompt and
    cites the in-ledger claim. Stands in for a real completion with no network."""

    def _complete(messages, **kwargs):
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
            "candidates": ["cand_a", "cand_b"], "basis": ["c_polar"],
            "knowledge_fingerprint": fp, "rationale": "polar supported",
        }]}
        return CompletionResult(
            text=json.dumps(body),
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    return _complete


def test_run_stage1_all_judgments_pass_offline(tmp_path):
    code = smoke.run_stage1(
        provider="stub/echo",
        out_dir=tmp_path,
        completion_factory=_stub_completion,
    )
    assert code == 0

    summary = json.loads((tmp_path / "stage1_summary.json").read_text())
    assert summary["all_passed"] is True
    assert summary["failed_judgments"] == []

    j = summary["judgments"]
    assert j["J1_positive_legal_proposal"]["passed"] is True
    assert j["J1_positive_legal_proposal"]["fingerprint_matched"] is True

    # The forced stale fingerprint must have driven exactly one reask, then recovered.
    assert j["J2_stale_fingerprint_triggers_reask"]["passed"] is True
    assert j["J2_stale_fingerprint_triggers_reask"]["reask_count"] == 1
    assert j["J2_stale_fingerprint_triggers_reask"]["recovered_legal"] is True

    # 1 positive call + 2 counterexample round-trips (initial stale + 1 reask) = 3, each audited.
    assert j["J3_audit_persisted"]["passed"] is True
    assert j["J3_audit_persisted"]["total_calls"] == 3
    audit_files = sorted(tmp_path.glob("call_*.json"))
    assert len(audit_files) == 3

    # Audit records are self-describing: full text + sha256 + usage.
    rec = json.loads(audit_files[0].read_text())
    for key in ("request_sha256", "response_sha256", "response_text", "usage", "timestamp"):
        assert key in rec

    assert j["J4_cost_guardrails"]["passed"] is True
    assert summary["knowledge_fingerprint"] == smoke._fixed_knowledge().knowledge_fingerprint


def test_call_budget_breach_exits_loudly(tmp_path):
    """If a run would exceed the call cap, the audited wrapper aborts (SystemExit) rather than
    spending more — proven by squeezing max_calls below the 3 calls the flow needs."""
    with pytest.raises(SystemExit):
        smoke.run_stage1(
            provider="stub/echo",
            out_dir=tmp_path,
            completion_factory=_stub_completion,
            max_calls=1,
        )


def test_main_rejects_max_tokens_over_ceiling(tmp_path):
    code = smoke.main(["--max-tokens", "9999", "--out", str(tmp_path)])
    assert code == 2  # guardrail exit, before any provider is touched


def test_main_rejects_max_calls_over_ceiling(tmp_path):
    code = smoke.main(["--max-calls", "99", "--out", str(tmp_path)])
    assert code == 2


@pytest.mark.skipif(
    importlib.util.find_spec("litellm") is not None,
    reason="litellm is installed; the absence-path exit code is not exercised here",
)
def test_main_exits_3_when_litellm_absent(tmp_path):
    """With litellm missing, main() prints an install hint and exits 3 (no bare ImportError)."""
    code = smoke.main(["--out", str(tmp_path)])
    assert code == 3
