"""M18 Stage 2 — SHADOW-lane discriminating acceptance (in the loop, decision face untouched).

Design of record: docs/M18_LLM_LIVE_SMOKE.md §2 Stage 2; switch shape per letters 086 §2 /
090/091 (B-ratified): ``agent_backend={"mode": "shadow", "provider": "<litellm ROUTE>"}``,
shadow audit event kind ``agent_shadow_proposal`` with required keys
{round_id, schema_valid, fingerprint_match, basis_subset, order_diff, usage}.

Two REAL mcl runs on the flat truth face (safest: expected insufficient, knowledge unchanged),
identical domain/rounds/seed:

  * run T — template lane (no agent_backend): the regression anchor.
  * run S — ``agent_backend={"mode": "shadow", ...}``: LLM rides along, audit-only.

Judgments:
  * S1 decision-face bitwise equality — knowledge-fingerprint chain, PRIOR_PROPOSAL candidate
    orders, promotion payloads (promoted + denied incl. reasons) and run_stop status are all
    EQUAL between T and S. The shadow lane must never perturb the loop (the discriminating
    assertion — a shadow that leaks into the decision face turns this red).
  * S2 shadow audit completeness — run S carries >=1 ``agent_shadow_proposal`` event per round,
    each with every ratified required key present. run T carries ZERO such events.
    Distinguishes loudly between "audit incomplete" and "switch not landed yet" (exit 4).
  * S3 audit persisted — the comparison summary lands in --out as JSON.

Cost posture: shadow fires one live completion (+ reasks) per round -> a 2-round run is a few
cents at gpt-4o-mini rates. The loop itself is the REAL dry+wet stack (PySCF + sim reader),
minutes not seconds.

Usage (after B's switch lands; provider must be a real litellm ROUTE — ``openai/...``):

    python scripts/llm_smoke_stage2.py --provider openai/gpt-4o-mini \
        --out runs/llm_smoke_stage2/

Exit codes: 0 = all judgments passed · 1 = judgment failed · 2 = config misuse ·
4 = switch not functional yet (no shadow events while decision faces match — pre-landing state).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from expos.kernel.objects import DecisionKind  # noqa: E402
from expos.kernel.store import RunStore  # noqa: E402
from expos.mcl import run_mcl_loop  # noqa: E402

#: Ratified required keys of one agent_shadow_proposal payload (letters 086 §2 / 090;
#: prompt_sha256 added by 094 §4 — Stage 3 asserts same-condition hash equality, without
#: which shadow-phase data has no inferential force for the cut-over decision;
#: validator_versions added by B's landing letter 099 — gate version ids
#: ["fingerprint_echo@v1", "basis_subset@v1"]).
SHADOW_REQUIRED_KEYS = frozenset(
    {"round_id", "schema_valid", "fingerprint_match", "basis_subset", "order_diff",
     "usage", "prompt_sha256", "validator_versions"}
)


#: DECISION_FACE_KINDS.v1 (letter 094 §2, versioned — bump on any change): the
#: whitelist that DEFINES "decision-face bitwise equal". agent_shadow_proposal is
#: excluded BY CONSTRUCTION — its usage/latency/response ids are inherently
#: non-deterministic; including them would make bitwise equality unreachable.
DECISION_FACE_VERSION = "decision_face.v1"


def decision_face(run_dir: Path) -> dict[str, Any]:
    """The decision-plane surface of a finished run — everything that must be bitwise
    equal between the template and shadow runs (DECISION_FACE_KINDS.v1). Execution-side
    noise (timings, job ids) is deliberately not part of this surface (M16 determinism
    dichotomy)."""
    store = RunStore(run_dir, create=False)
    return {
        "knowledge_fps": [
            e["payload"]["fingerprint"] for e in store.read_events("knowledge_updated")
        ],
        "proposal_orders": [
            rec.content.get("candidates")
            for rec in store.list_decisions(kind=DecisionKind.PRIOR_PROPOSAL)
        ],
        "promotions": [e["payload"] for e in store.read_events("promotion_decision")],
        "run_stop": [e["payload"].get("status") for e in store.read_events("run_stop")],
    }


def shadow_events(run_dir: Path) -> list[dict[str, Any]]:
    store = RunStore(run_dir, create=False)
    return [e["payload"] for e in store.read_events("agent_shadow_proposal")]


def run_stage2(
    domain: Path,
    rounds: int,
    seed: int,
    out: Path,
    agent_backend: dict[str, Any],
    truth_profile: str | None,
) -> int:
    out.mkdir(parents=True, exist_ok=True)
    t_dir, s_dir = out / "template", out / "shadow"

    common: dict[str, Any] = {"rounds": rounds, "seed": seed}
    if truth_profile:
        common["truth_profile"] = truth_profile

    run_mcl_loop(domain, out_dir=t_dir, **common)
    run_mcl_loop(domain, out_dir=s_dir, agent_backend=agent_backend, **common)

    face_t, face_s = decision_face(t_dir), decision_face(s_dir)
    shadows_s, shadows_t = shadow_events(s_dir), shadow_events(t_dir)

    s1 = face_t == face_s
    missing = [
        sorted(SHADOW_REQUIRED_KEYS - set(p)) for p in shadows_s if SHADOW_REQUIRED_KEYS - set(p)
    ]
    s2 = len(shadows_s) >= rounds and not missing and not shadows_t

    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "domain": str(domain),
        "rounds": rounds,
        "seed": seed,
        "truth_profile": truth_profile,
        "agent_backend": {k: v for k, v in agent_backend.items() if k != "completion_fn"},
        "judgments": {
            "S1_decision_face_bitwise_equal": {"passed": s1, "template": face_t, "shadow": face_s},
            "S2_shadow_audit_complete": {
                "passed": s2,
                "n_shadow_events": len(shadows_s),
                "n_rounds": rounds,
                "missing_keys_per_event": missing,
                "template_run_shadow_events": len(shadows_t),
            },
        },
        "shadow_events": shadows_s,
    }
    (out / "stage2_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    if not shadows_s and s1:
        # Loud pre-landing diagnosis: faces match (dangling kwarg is inert) but the shadow
        # lane emitted nothing — the switch has not actually landed in the loop yet.
        print("[STAGE2] switch not functional: zero agent_shadow_proposal events "
              f"(faces equal) — has B's agent_backend lane landed? summary={out}/stage2_summary.json")
        return 4
    failed = [n for n, j in summary["judgments"].items() if not j["passed"]]
    if failed:
        print(f"[STAGE2] FAILED judgments: {failed}")
        print(f"[STAGE2] see {out}/stage2_summary.json")
        return 1
    print(f"[STAGE2] all judgments passed (shadow_events={len(shadows_s)}, out={out})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", type=Path,
                    default=_REPO_ROOT / "domains" / "solvent_screen.yaml")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--provider", default="openai/gpt-4o-mini",
                    help="litellm ROUTE string (e.g. openai/gpt-4o-mini)")
    ap.add_argument("--truth-profile", default="flat",
                    help="hidden truth face for the sim reader (flat = safest)")
    ap.add_argument("--out", type=Path, default=_REPO_ROOT / "runs" / "llm_smoke_stage2")
    ap.add_argument("--agent-backend-json", default=None,
                    help="escape hatch: full agent_backend dict as JSON, overriding "
                         "--provider (use if B's landed shape differs)")
    args = ap.parse_args(argv)

    if args.provider.startswith("litellm/"):
        print("[STAGE2] 'litellm/' is not a valid litellm route (letter 088 §3); "
              "use e.g. openai/gpt-4o-mini")
        return 2
    backend = (json.loads(args.agent_backend_json) if args.agent_backend_json
               else {"mode": "shadow", "provider": args.provider})
    return run_stage2(args.domain, args.rounds, args.seed, args.out, backend,
                      args.truth_profile or None)


if __name__ == "__main__":
    raise SystemExit(main())
