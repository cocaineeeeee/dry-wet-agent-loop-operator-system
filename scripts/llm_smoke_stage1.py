"""M18 Stage 1 — live LLM AgentBackend single-shot ping (NOT in the loop).

Design of record: docs/M18_LLM_LIVE_SMOKE.md §2 Stage 1. This script fires a REAL provider
completion exactly to prove the seam works end-to-end, then stops. It is the last checkpoint
before the backend is allowed near the closed loop, so it is heavy on guardrails and audit.

What it asserts (the four Stage-1 judgments):
  * J1 positive     — one live completion yields a schema-legal ProposalSchema whose
                      ``knowledge_fingerprint`` matches the compiled KnowledgeView the model was
                      handed, and whose ``basis`` cites only in-ledger claim_ids.
  * J2 counterexample — a stale/forged fingerprint is forced onto the FIRST response, and the
                      validate-and-reask machinery is proven to fire (>=1 model-side reask) and
                      then recover to a legal proposal (recent_failures stays empty). The reask
                      count is recorded.
  * J3 audit        — every request/response (full text + sha256 + model + timestamp + usage)
                      is persisted to ``--out`` as JSON.
  * J4 cost guard   — max_tokens <= 2048 and total live calls <= 4 (reasks included); a breach
                      exits loudly rather than burning budget.

Cost/safety posture:
  * Offline-testable: the whole flow runs through ``run_stage1(..., completion_factory=...)``;
    tests inject a stub factory and exercise ``main`` logic with ZERO network / ZERO API key.
    ``main()`` is the only path that binds the live litellm factory.
  * litellm is optional: if it is not importable, ``main`` prints an install hint and exits 3
    (never a bare ImportError traceback).

Usage (the main session runs this — it makes REAL API calls):

    python scripts/llm_smoke_stage1.py \
        --provider openai/gpt-4o-mini \
        --out runs/llm_smoke_stage1/

Exit codes: 0 = all judgments passed · 1 = a judgment failed (which one is printed) ·
2 = guardrail/config misuse · 3 = litellm not installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Repo-root import (script may be launched from anywhere).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from expos.agent.llm_backend import (  # noqa: E402
    CompletionFn,
    CompletionResult,
    LLMBackend,
)
from expos.kernel.knowledge import compile_knowledge  # noqa: E402
from expos.kernel.objects import HypothesisObject  # noqa: E402
from expos.kernel.store import ReadOnlyRunView  # noqa: E402

#: Cost guardrails (M18 §0.5 / §2). Hard caps — a request to exceed them exits loudly.
_MAX_TOKENS_CEILING = 2048
_MAX_CALLS_CEILING = 4
_EXPORTED_AT = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------- knowledge fixture


def _fixed_knowledge():
    """A minimal, fixed KnowledgeView (one claim, one hypothesis) so the compiled fingerprint is
    a real, deterministic value the model must echo. This is the whitelisted prompt input face —
    no truth surface anywhere near it (the truth-isolation guard enforces that structurally)."""
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


# ---------------------------------------------------------------- completion wrappers


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_audited(inner: CompletionFn, out_dir: Path, budget: dict[str, Any]) -> CompletionFn:
    """Wrap a completion callable so every round-trip is (a) counted against the call budget and
    (b) persisted to ``out_dir`` as a self-describing JSON audit record (full request + response
    text, both sha256'd, plus model / timestamp / usage). Wrapping the OUTERMOST callable means
    the audit captures exactly what the validator saw (including a forced stale fingerprint)."""

    def _fn(messages, **kwargs) -> CompletionResult:
        budget["calls"] += 1
        n = budget["calls"]
        if n > budget["max_calls"]:
            raise SystemExit(
                f"[GUARDRAIL] live call budget exceeded: attempted call #{n} > "
                f"max_calls={budget['max_calls']} — aborting before spending more."
            )
        req_text = json.dumps(list(messages), ensure_ascii=False, sort_keys=True)
        result = inner(messages, **kwargs)
        record = {
            "call_index": n,
            "phase": budget.get("phase"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": budget.get("provider"),
            "model": kwargs.get("model"),
            "max_tokens": budget.get("max_tokens"),
            "request_messages": list(messages),
            "request_sha256": _sha256(req_text),
            "response_text": result.text,
            "response_sha256": _sha256(result.text or ""),
            "usage": dict(result.usage or {}),
        }
        (out_dir / f"call_{n:02d}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return result

    return _fn


def _make_stale_first_fp(inner: CompletionFn, expected_fp: str) -> tuple[CompletionFn, dict]:
    """Wrap a completion so the FIRST response's knowledge_fingerprint is corrupted to a stale
    value, forcing the validator to reject and trigger a model-side reask. Subsequent responses
    pass through untouched (the reask carries the correct fingerprint, so the model recovers).

    This is how a live counterexample is produced deterministically without depending on the model
    to spontaneously err: it faithfully exercises the reask code path over a REAL round-trip. The
    returned state dict reports how many corruptions/calls happened (retry count = calls - 1)."""
    state = {"calls": 0}

    def _fn(messages, **kwargs) -> CompletionResult:
        state["calls"] += 1
        result = inner(messages, **kwargs)
        if state["calls"] == 1:
            # Rewrite the fingerprint in the returned JSON to a stale value.
            try:
                obj = json.loads(_strip_fence(result.text))
                for p in obj.get("proposals", []):
                    p["knowledge_fingerprint"] = f"STALE::{expected_fp[:8]}::deadbeef"
                return CompletionResult(text=json.dumps(obj), usage=result.usage)
            except (ValueError, TypeError):
                # If the model returned unparseable text, that already forces a reask — leave it.
                return result
        return result

    return _fn, state


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -len("```")]
        s = s.removeprefix("json").strip()
    return s


# ---------------------------------------------------------------- core Stage-1 flow


def run_stage1(
    *,
    provider: str,
    out_dir: Path,
    completion_factory: Callable[[], CompletionFn],
    max_tokens: int = _MAX_TOKENS_CEILING,
    max_calls: int = _MAX_CALLS_CEILING,
) -> int:
    """Execute the four Stage-1 judgments. Returns a process exit code (0 = all passed).

    ``completion_factory`` returns a completion callable — the live factory in ``main`` builds one
    via ``LLMBackend.from_provider``; tests inject a stub. All network is behind that seam."""
    out_dir.mkdir(parents=True, exist_ok=True)
    kv = _fixed_knowledge()
    claim_ids = sorted({e.claim_id for h in kv.hypotheses for e in h.evidence})

    budget: dict[str, Any] = {
        "calls": 0, "max_calls": max_calls, "provider": provider,
        "max_tokens": max_tokens, "phase": "positive",
    }
    inner = completion_factory()
    audited = _make_audited(inner, out_dir, budget)

    results: dict[str, Any] = {
        "provider": provider,
        "max_tokens": max_tokens,
        "max_calls": max_calls,
        "knowledge_fingerprint": kv.knowledge_fingerprint,
        "claim_ids": claim_ids,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "judgments": {},
    }
    failures: list[str] = []

    # -- J1 positive: one live completion -> schema-legal, fingerprint-matched proposal ---------
    b_pos = LLMBackend(audited, kv, model_id=provider, provider=provider)
    pos = b_pos.suggest(_view(), round_id=1)
    j1_ok = (
        len(pos) >= 1
        and pos[0].content["knowledge_fingerprint"] == kv.knowledge_fingerprint
        and all(c in claim_ids for c in pos[0].content.get("basis", []))
        and b_pos.recent_failures() == []
    )
    results["judgments"]["J1_positive_legal_proposal"] = {
        "passed": j1_ok,
        "n_proposals": len(pos),
        "fingerprint_matched": bool(
            pos and pos[0].content["knowledge_fingerprint"] == kv.knowledge_fingerprint
        ),
        "basis": pos[0].content.get("basis") if pos else None,
        "failures": [f.reason for f in b_pos.recent_failures()],
    }
    if not j1_ok:
        failures.append("J1_positive_legal_proposal")

    # -- J2 counterexample: forced stale fingerprint -> reask fires -> recovers -----------------
    budget["phase"] = "counterexample"
    stale_fn, stale_state = _make_stale_first_fp(inner, kv.knowledge_fingerprint)
    audited_stale = _make_audited(stale_fn, out_dir, budget)
    b_neg = LLMBackend(audited_stale, kv, model_id=provider, provider=provider)
    neg = b_neg.suggest(_view(), round_id=2)
    reasks = max(0, stale_state["calls"] - 1)
    j2_ok = (
        stale_state["calls"] >= 2          # the reask round-trip actually happened
        and reasks >= 1
        and len(neg) >= 1                  # and the model recovered to a legal proposal
        and neg[0].content["knowledge_fingerprint"] == kv.knowledge_fingerprint
        and b_neg.recent_failures() == []  # recovered, not exhausted
    )
    results["judgments"]["J2_stale_fingerprint_triggers_reask"] = {
        "passed": j2_ok,
        "reask_count": reasks,
        "counterexample_round_trips": stale_state["calls"],
        "recovered_legal": len(neg) >= 1,
        "failures": [f.reason for f in b_neg.recent_failures()],
    }
    if not j2_ok:
        failures.append("J2_stale_fingerprint_triggers_reask")

    # -- J3 audit: one JSON file per live round-trip ---------------------------------------------
    audit_files = sorted(out_dir.glob("call_*.json"))
    j3_ok = len(audit_files) == budget["calls"] and budget["calls"] >= 1
    results["judgments"]["J3_audit_persisted"] = {
        "passed": j3_ok,
        "audit_files": [p.name for p in audit_files],
        "total_calls": budget["calls"],
    }
    if not j3_ok:
        failures.append("J3_audit_persisted")

    # -- J4 cost guard: caps honored -------------------------------------------------------------
    j4_ok = (
        max_tokens <= _MAX_TOKENS_CEILING
        and max_calls <= _MAX_CALLS_CEILING
        and budget["calls"] <= max_calls
    )
    results["judgments"]["J4_cost_guardrails"] = {
        "passed": j4_ok,
        "max_tokens": max_tokens,
        "max_tokens_ceiling": _MAX_TOKENS_CEILING,
        "total_calls": budget["calls"],
        "max_calls": max_calls,
    }
    if not j4_ok:
        failures.append("J4_cost_guardrails")

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    results["all_passed"] = not failures
    results["failed_judgments"] = failures
    (out_dir / "stage1_summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if failures:
        print(f"[STAGE1] FAILED judgments: {failures}", file=sys.stderr)
        print(f"[STAGE1] see {out_dir / 'stage1_summary.json'}", file=sys.stderr)
        return 1
    print(
        f"[STAGE1] all judgments passed "
        f"(calls={budget['calls']}, reasks={reasks}, out={out_dir})"
    )
    return 0


# ---------------------------------------------------------------- CLI (binds live provider)


def _live_factory(provider: str, max_tokens: int) -> Callable[[], CompletionFn]:
    def _factory() -> CompletionFn:
        return LLMBackend.from_provider(provider, max_tokens=max_tokens)
    return _factory


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="openai/gpt-4o-mini",
                    help="litellm provider string (default: openai/gpt-4o-mini)")
    ap.add_argument("--out", default="runs/llm_smoke_stage1/", type=Path,
                    help="audit output directory")
    ap.add_argument("--max-tokens", type=int, default=_MAX_TOKENS_CEILING,
                    help=f"per-call max_tokens (hard ceiling {_MAX_TOKENS_CEILING})")
    ap.add_argument("--max-calls", type=int, default=_MAX_CALLS_CEILING,
                    help=f"total live-call budget incl. reasks (hard ceiling {_MAX_CALLS_CEILING})")
    args = ap.parse_args(argv)

    # Guardrails are checked BEFORE any provider is touched — misuse must not spend budget.
    if args.max_tokens <= 0 or args.max_tokens > _MAX_TOKENS_CEILING:
        print(f"[GUARDRAIL] --max-tokens must be in 1..{_MAX_TOKENS_CEILING}, got "
              f"{args.max_tokens}", file=sys.stderr)
        return 2
    if args.max_calls <= 0 or args.max_calls > _MAX_CALLS_CEILING:
        print(f"[GUARDRAIL] --max-calls must be in 1..{_MAX_CALLS_CEILING}, got "
              f"{args.max_calls}", file=sys.stderr)
        return 2

    import importlib.util
    if importlib.util.find_spec("litellm") is None:
        print(
            "[STAGE1] the 'litellm' package is not installed — Stage 1 needs a live provider.\n"
            "  Install it (pin the version in env.txt afterwards):\n"
            "      pip install litellm\n"
            "  Then re-run with your provider, e.g.:\n"
            f"      python scripts/llm_smoke_stage1.py --provider {args.provider} --out {args.out}",
            file=sys.stderr,
        )
        return 3

    return run_stage1(
        provider=args.provider,
        out_dir=args.out,
        completion_factory=_live_factory(args.provider, args.max_tokens),
        max_tokens=args.max_tokens,
        max_calls=args.max_calls,
    )


if __name__ == "__main__":
    raise SystemExit(main())
