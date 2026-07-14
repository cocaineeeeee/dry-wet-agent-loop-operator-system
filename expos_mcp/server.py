"""expos_mcp.server — a READ-ONLY MCP server exposing the expos Research OS
audit / knowledge / decision-chain plane to external LLM agents (Claude, etc.).

Run it (stdio transport)::

    python -m expos_mcp.server

Or register it with a Claude Code / MCP client::

    claude mcp add expos -- python -m expos_mcp.server

API discipline: every SDK symbol used here is anchored on the LOCAL pinned
``mcp==1.16.0`` (site-packages), never on the python-sdk GitHub HEAD (which has
since been refactored — see r4_os_references/INDEX_M19_MCPSRV.md, version
discipline note).

Design red lines (enforced here, not merely documented):

1. READ-ONLY v1. No tool writes, appends, deletes or mutates anything. Every
   tool is a pure read of a run's ``events.jsonl`` (the append-only truth) and
   ``checkpoint.json`` (the lagging reconciliation cursor). The agent-permission
   axiom of expos is proposal-only; this MCP surface is strictly narrower — a
   future write surface is a separate project. Every tool additionally carries
   ``ToolAnnotations(readOnlyHint=True, openWorldHint=False)`` (the git-server
   read-command pattern, INDEX_M19 §1/§5).

2. TRUTH ISOLATION. The hidden simulation truth surface lives ONLY inside the
   wet reader's server-side sidecar (``expos/adapters/wet/sim_reader.py``:
   TRUTH_PROFILES / TruthSurface / the ``truth_records`` / ``harvest_truth``
   channels) and the store's opaque ``truth/`` sidecar. NONE of it may reach an
   external client. Every payload is passed through ``_assert_marker_free``, a
   recursive guard that raises the instant a forbidden marker key appears (the
   key blacklist mirrors ``expos/agent/llm_backend.py``). For tools with pinned
   output models the guard runs BEFORE the model is populated (INDEX_M19 §3:
   schema-ification makes fields easier to scrape programmatically, so the
   strip/assert must precede model construction). The "delete-the-guard-goes-
   red" contract: dropping the guard call lets a tainted payload through — the
   negative tests in tests/test_mcp_server.py fail.

3. NO SOURCE EDITS to ``expos/``. This package reuses the store reader, the
   claim ledger model and ``scripts/verify_run_chain.py`` (imported, never
   re-implemented); it only reads them.

MCP-shape note (INDEX_M19 §1 "four sink to resource, three stay tools"): the
four pure key-addressed read faces (runs list / run status / claim ledger /
knowledge fingerprints) are ALSO registered as resources
(``resource://expos/runs``, ``resource://expos/runs/{run_name}/status|ledger|
fingerprints``) — GET-semantic, cacheable projections. They stay available as
tools too (dual registration) because tool-centric MCP clients drive tools far
more readily; both faces share the same guarded implementations. The three
value-validated / computing faces (get_events with its kind whitelist,
verify_gate12, diff_runs) are tools only.

Path safety (INDEX_M19 §2, git validate_repo_path form): a single choke point
``_resolve_run`` (a) literal early-rejects null bytes / a leading ``/`` (which
would silently DROP the root in ``Path(root) / name``) / any ``..`` segment,
then (b) resolves BOTH sides (``resolve()`` == realpath, so symlinks cannot
escape) and (c) requires ``is_relative_to`` the resolved runs root. Every tool
and resource obtains run directories exclusively through it. run_name values
are never URL-decoded (taken literally). Runs root defaults to
``/Data1/ericyang/dry_wet_agent_os/runs``; override with ``EXPOS_MCP_RUNS_ROOT``.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

# Read-only reuse of the store reader + claim-ledger model (never a writer handle).
from expos.kernel.claims import ClaimRecord, Ledger
from expos.kernel.store import RunStore, StoreError

# ============================================================ runs root

_DEFAULT_RUNS_ROOT = Path("/Data1/ericyang/dry_wet_agent_os/runs")

# The one annotation object every tool carries: this surface never modifies its
# environment, is idempotent, and touches only the local runs tree (closed world).
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _runs_root() -> Path:
    """The runs root, resolved fresh each call so ``EXPOS_MCP_RUNS_ROOT`` can be
    overridden (tests point it at a tmp dir; deployments at the real runs tree).
    Fixed by env/constant only — deliberately NOT re-rootable via the MCP roots
    protocol (INDEX_M19 §2 not-copy: a client must not be able to widen the read
    face at runtime)."""
    return Path(os.environ.get("EXPOS_MCP_RUNS_ROOT", str(_DEFAULT_RUNS_ROOT))).resolve()


def _resolve_run(run_name: str) -> Path:
    """Resolve ``run_name`` to a run directory strictly UNDER the runs root.

    The single path choke point (INDEX_M19 §2, git ``validate_repo_path`` form).
    Three layers, all raising ``ValueError`` on refusal:

      1. literal early-reject — empty name, embedded null byte, a leading ``/``
         or ``\\`` (``Path(root) / "/etc"`` silently DISCARDS root — the leading-
         slash injection), or any ``..`` path segment;
      2. resolve BOTH sides — ``resolve()`` is realpath, so a symlink planted
         under the root cannot escape it (string-only checks are symlink-blind);
      3. containment — the resolved candidate must be ``is_relative_to`` the
         resolved root, be a directory, and carry an ``events.jsonl``.

    ``run_name`` is treated literally (never URL-decoded)."""
    name = str(run_name)
    if not name.strip():
        raise ValueError("run_name must be a non-empty run directory name")
    if "\x00" in name:
        raise ValueError("run_name must not contain null bytes")
    if name.startswith(("/", "\\")):
        raise ValueError(
            f"run_name {run_name!r} must be a relative run directory name "
            "(a leading slash would escape the runs root)"
        )
    if ".." in PurePosixPath(name).parts:
        raise ValueError(
            f"run_name {run_name!r} contains a '..' segment — refused (path traversal)"
        )
    root = _runs_root()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(
            f"run_name {run_name!r} escapes the runs root — refused (path traversal)"
        )
    if candidate == root:
        raise ValueError("run_name must name a run under the runs root, not the root")
    if not candidate.is_dir():
        raise ValueError(f"no such run directory: {run_name!r}")
    if not (candidate / "events.jsonl").exists():
        raise ValueError(f"{run_name!r} is not a run (no events.jsonl)")
    return candidate


# ============================================================ truth-isolation guard
#
# Kept deliberately in sync with expos/agent/llm_backend.py::_FORBIDDEN_MARKER_KEYS.
# These are the sim-reader hidden-response-surface marker keys; if ANY of them ever
# appears in a value we are about to hand an external client, that value has been
# tainted by the truth sidecar and must not leave. The blacklist entries are DATA
# (exact key strings we refuse), not identifiers — expos_mcp is outside the four-
# package EXP001 scope, but its own naming still avoids the forbidden word so the
# surface cannot be repurposed to leak later. "xt" = the truth-isolation red line.
_FORBIDDEN_MARKER_KEYS: frozenset[str] = frozenset({
    "truth_profile",
    "truth_profiles",
    "truth_surface",
    "truthsurface",
    "hidden_truth",
    "truth",
    "true_response",
    "truth_records",
    "truth_dump",
    "harvest_truth",
    "save_truth",
})


class MarkerIsolationError(RuntimeError):
    """Raised when a value about to leave via MCP carries a forbidden marker key
    (the sim-reader hidden truth surface). Fail loud, never leak."""


def _assert_marker_free(obj: Any, *, where: str, _seen: set[int] | None = None) -> None:
    """Recursively assert that ``obj`` carries no forbidden marker key (see
    ``_FORBIDDEN_MARKER_KEYS``). Mirrors the llm_backend guard: walks dicts,
    lists/tuples/sets and pydantic-model dumps, comparing each dict key case-
    insensitively against the blacklist. Load-bearing: every payload runs through
    this before it is serialized (or, for schema-pinned tools, BEFORE the output
    model is populated — INDEX_M19 §3). The "xt" red line."""
    seen = _seen if _seen is not None else set()
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)

    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        _assert_marker_free(obj.model_dump(mode="json"), where=where, _seen=seen)
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(key, str) and key.strip().lower() in _FORBIDDEN_MARKER_KEYS:
                raise MarkerIsolationError(
                    f"truth-isolation violation while building {where}: return value "
                    f"carries the forbidden marker key {key!r}. The MCP surface admits "
                    "only the public audit / knowledge / decision-chain content; the "
                    "hidden simulation response surface (sim_reader TRUTH_PROFILES / "
                    "TruthSurface / the truth sidecar) must never reach a client."
                )
            _assert_marker_free(val, where=where, _seen=seen)
        return
    if isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            _assert_marker_free(item, where=where, _seen=seen)
        return
    # scalars (str/int/float/bool/None) carry no keys — nothing to check.


def _guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool implementation so its return value is asserted marker-free
    before it leaves the process. Outer defense-in-depth layer; schema-pinned
    tools ALSO guard the raw payload dict before populating their output model."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        _assert_marker_free(result, where=fn.__name__)
        return result

    return wrapper


# ============================================================ output models
#
# Pinned audit contracts (INDEX_M19 §3) for the three computing tools: pydantic
# return annotations make FastMCP 1.16 auto-generate an outputSchema (sent on
# list_tools) and a structured result channel (structuredContent) — and the
# lowlevel server jsonschema-validates every result against that schema.
# ``extra="forbid"`` keeps the contract TIGHT: field changes are explicit schema
# evolution, never silent drift (same spirit as the agent-side ProposalSchema).


class Gate12Report(BaseModel):
    """verify_gate12 result: the gate-12 three-layer audit verdict."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    layer: int | None = None
    code: str | None = None
    message: str = ""
    detail: dict[str, Any] = {}
    summary: dict[str, Any] = {}


class RunDiff(BaseModel):
    """diff_runs result: first divergence between two decision chains."""

    model_config = ConfigDict(extra="forbid")

    diverged: bool
    first_divergence: dict[str, Any] | None = None
    n_nodes_a: int = 0
    n_nodes_b: int = 0


class EventRecord(BaseModel):
    """One event as exposed by get_events (seq order)."""

    model_config = ConfigDict(extra="forbid")

    seq: int | None = None
    ts: str | None = None
    kind: str
    payload: dict[str, Any]


class EventPage(BaseModel):
    """get_events result: up to ``limit`` events of one whitelisted kind."""

    model_config = ConfigDict(extra="forbid")

    run_name: str
    kind: str
    n_total: int
    events: list[EventRecord]


# ============================================================ verify_run_chain reuse
#
# scripts/ is not a package; load verify_run_chain.py as a module ONCE and reuse
# its verify_run / diff_decision_chain (gate-12 discipline: import, never rewrite).

def _load_verify_run_chain() -> Any:
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_run_chain.py"
    spec = importlib.util.spec_from_file_location("verify_run_chain", script)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {script}")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the module's @dataclass field-type introspection can
    # resolve itself via sys.modules (else Py3.13 @dataclass raises).
    sys.modules.setdefault("verify_run_chain", mod)
    spec.loader.exec_module(mod)
    return mod


_vrc = _load_verify_run_chain()


# ============================================================ read helpers

def _read_store(run_dir: Path) -> RunStore:
    """Open a READ-ONLY store handle (create=False, no lock, no writer)."""
    return RunStore(run_dir, create=False)


def _knowledge_fingerprints(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The per-round knowledge fingerprint chain (one knowledge_updated per round,
    in seq order => round index is its position)."""
    out: list[dict[str, Any]] = []
    for i, e in enumerate(e for e in events if e.get("kind") == "knowledge_updated"):
        p = e.get("payload") or {}
        out.append({
            "round_index": i,
            "fingerprint": p.get("fingerprint"),
            "n_hypotheses": p.get("n_hypotheses"),
            "n_claims": p.get("n_claims"),
        })
    return out


def _ledger_from_checkpoint(ckpt: dict[str, Any] | None) -> Ledger:
    """Rebuild the claim ledger from the checkpoint snapshot (same replay mcl uses
    on resume: append-only records validated back into ClaimRecords, no event
    re-emission). Missing/old checkpoint => empty ledger."""
    snapshot = (ckpt or {}).get("claim_ledger") or []
    return Ledger(claims=tuple(ClaimRecord.model_validate(rec) for rec in snapshot))


def _final_exit_status(events: list[dict[str, Any]]) -> str | None:
    stops = [e for e in events if e.get("kind") == "run_stop"]
    if not stops:
        return None
    return (stops[-1].get("payload") or {}).get("exit_status")


# ============================================================ MCP server

mcp = FastMCP(
    "expos",
    instructions=(
        "Read-only audit / knowledge / decision-chain surface over the expos "
        "Research OS. Use it to inspect a run's status, claim ledger, knowledge "
        "fingerprints and decision events, and to re-audit or diff decision chains. "
        "It NEVER writes and never exposes the hidden simulation truth surface. "
        "Start with list_runs() to discover runs, then query a run by name. The "
        "pure read faces are also addressable as resources under resource://expos/."
    ),
)


@mcp.tool(annotations=_READ_ONLY)
@_guarded
def list_runs() -> list[dict[str, Any]]:
    """List every run under the runs root, with a one-line status summary each.

    WHEN TO USE: call this FIRST to discover which runs exist and their names,
    before any per-run tool. Each entry gives the run_name to pass to the other
    tools, plus whether the run terminated cleanly (exit_status) and how far it
    got (completed_rounds). A run is any directory carrying an ``events.jsonl``.

    Returns a list of ``{run_name, domain, loop, completed_rounds, exit_status,
    n_events, n_rounds}``; ``exit_status`` is null for a run that crashed without
    a run_stop (absence == crash). Also addressable as the resource
    ``resource://expos/runs``. Reads only; nothing is modified."""
    root = _runs_root()
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "events.jsonl").exists():
            continue
        try:
            store = _read_store(child)
            events = store.read_events()
            ckpt = store.read_checkpoint() or {}
        except (StoreError, OSError, ValueError):
            # A broken/torn run still appears in the listing, flagged unreadable,
            # rather than making the whole listing fail.
            out.append({"run_name": child.name, "unreadable": True})
            continue
        out.append({
            "run_name": child.name,
            "domain": ckpt.get("domain"),
            "loop": ckpt.get("loop") or ckpt.get("mode"),
            "completed_rounds": ckpt.get("completed_rounds"),
            "exit_status": _final_exit_status(events),
            "n_events": len(events),
            "n_rounds": sum(1 for e in events if e.get("kind") == "knowledge_updated"),
        })
    return out


@mcp.tool(annotations=_READ_ONLY)
@_guarded
def get_run_status(run_name: str) -> dict[str, Any]:
    """Summarize one run's audit state: how far it got and where knowledge stands.

    WHEN TO USE: the go-to overview of a single run — after list_runs, ask this to
    see completed_rounds, the terminal exit_status, the per-round knowledge
    fingerprint chain, and each claim's current effective (head) status. Use it to
    answer "did this run finish, how many rounds, and what does it now believe?".

    Returns ``{run_name, completed_rounds, exit_status, n_events, n_rounds,
    knowledge_fingerprints, effective_status}`` where ``effective_status`` maps
    each claim_id to its replayed head status (derived from the append-only ledger,
    claims with only annotations are absent). Also addressable as the resource
    ``resource://expos/runs/{run_name}/status``. Read-only."""
    run_dir = _resolve_run(run_name)
    store = _read_store(run_dir)
    events = store.read_events()
    ckpt = store.read_checkpoint()
    ledger = _ledger_from_checkpoint(ckpt)
    effective = {cid: st.value for cid, st in ledger.effective_statuses().items()}
    return {
        "run_name": run_name,
        "completed_rounds": (ckpt or {}).get("completed_rounds"),
        "exit_status": _final_exit_status(events),
        "n_events": len(events),
        "n_rounds": sum(1 for e in events if e.get("kind") == "knowledge_updated"),
        "knowledge_fingerprints": [k["fingerprint"] for k in _knowledge_fingerprints(events)],
        "effective_status": effective,
    }


@mcp.tool(annotations=_READ_ONLY)
@_guarded
def get_claim_ledger(run_name: str) -> dict[str, Any]:
    """Return the run's claim ledger — the auditable decision content, no raw data.

    WHEN TO USE: to read WHAT the run concluded and HOW STRONGLY — the natural-
    language claim statements, each version's adjudicated status, the supersede
    chain (version / supersedes), and a compact e-value / effect summary per claim.
    This is exactly the outward-tellable decision record; it deliberately EXCLUDES
    the raw per-well observations (only their count is reported).

    Returns ``{run_name, effective_status, claims[], e_value_stats}``:
      * ``claims[]`` — one entry per ledger record: ``{claim_id, version, status,
        statement, evidence_strength, supersedes, is_annotation, deny_reason,
        decision_fn_id, n_evidence, consumed_knowledge_fingerprint, statistic}``
        where ``statistic`` is a compact public summary (effect_estimate, p_value,
        achieved_power, evidence_factor) — never the raw observation stream.
      * ``e_value_stats`` — per-claim accumulator from certification_state
        (rounds_observed, e_product, per_round_e, info_sum, weighted_effect_sum).
      * ``effective_status`` — the derived head status per claim_id.
    Also addressable as the resource ``resource://expos/runs/{run_name}/ledger``.
    Read-only."""
    run_dir = _resolve_run(run_name)
    store = _read_store(run_dir)
    ckpt = store.read_checkpoint() or {}
    ledger = _ledger_from_checkpoint(ckpt)

    claims: list[dict[str, Any]] = []
    for rec in ledger.claims:
        prov = rec.provenance
        stat = prov.statistic
        claims.append({
            "claim_id": rec.claim_id,
            "version": rec.version,
            "status": rec.status.value,
            "statement": rec.statement,
            "evidence_strength": rec.evidence_strength.value,
            "supersedes": rec.supersedes,
            "is_annotation": rec.is_annotation,
            "deny_reason": rec.deny_reason,
            "decision_fn_id": rec.decision_fn_id,
            # count only — the raw per-well observation stream is NOT exposed.
            "n_evidence": len(prov.usage.observations),
            "consumed_knowledge_fingerprint": prov.usage.consumed_knowledge_fingerprint,
            "statistic": {
                "test_method": stat.test_method,
                "statistic_name": stat.statistic_name,
                "statistic_value": stat.statistic_value,
                "effect_estimate": stat.effect_estimate,
                "p_value": stat.p_value,
                "achieved_power": stat.achieved_power,
                "evidence_factor": stat.evidence_factor,
            },
        })

    cert_state = ckpt.get("certification_state") or {}
    e_value_stats: dict[str, Any] = {}
    if isinstance(cert_state, dict):
        for cid, acc in cert_state.items():
            if not isinstance(acc, dict):
                continue
            e_value_stats[cid] = {
                "rounds_observed": acc.get("rounds_observed"),
                "e_product": acc.get("e_product"),
                "per_round_e": acc.get("per_round_e"),
                "info_sum": acc.get("info_sum"),
                "weighted_effect_sum": acc.get("weighted_effect_sum"),
            }

    return {
        "run_name": run_name,
        "effective_status": {cid: st.value for cid, st in ledger.effective_statuses().items()},
        "claims": claims,
        "e_value_stats": e_value_stats,
    }


@mcp.tool(annotations=_READ_ONLY)
@_guarded
def get_knowledge_fingerprints(run_name: str) -> list[dict[str, Any]]:
    """Return the run's per-round knowledge fingerprint chain.

    WHEN TO USE: to see how the compiled knowledge evolved round by round — one
    ``knowledge_updated`` fingerprint per round, in order. Use it to check whether
    two runs share a knowledge trajectory, or to locate the round at which the
    knowledge content changed (the fingerprint is a content hash: identical
    content => identical fingerprint).

    Returns a list of ``{round_index, fingerprint, n_hypotheses, n_claims}`` in
    round order. Also addressable as the resource
    ``resource://expos/runs/{run_name}/fingerprints``. Read-only."""
    run_dir = _resolve_run(run_name)
    store = _read_store(run_dir)
    return _knowledge_fingerprints(store.read_events())


@mcp.tool(annotations=_READ_ONLY)
def get_events(run_name: str, kind: str, limit: int = 50) -> EventPage:
    """Return up to ``limit`` events of one whitelisted kind from a run.

    WHEN TO USE: to inspect the actual event payloads behind a decision — e.g.
    ``promotion_decision`` (what got promoted), ``claim_decision`` (adjudication
    verdicts), ``knowledge_updated`` (knowledge compiles), ``run_stop`` (terminal
    status). Pick a kind from the whitelist below; other kinds (including any raw
    sidecar content) are refused.

    ``kind`` MUST be one of the payload-registered decision/lifecycle kinds
    (``RunStore.EVENT_PAYLOAD_REQUIRED``): routing, action_consumed,
    redo_reconciliation, run_stop, risk_map_applied, aggregation_alpha,
    reclassification, learning_weight_assigned, knowledge_updated,
    promotion_decision, claim_decision, agent_shadow_proposal,
    agent_generation_failed. Any other kind raises ``ValueError``.

    Returns an ``EventPage`` (pinned schema): ``{run_name, kind, n_total,
    events: [{seq, ts, kind, payload}]}`` with the first ``limit`` matching
    events in seq order; ``n_total`` is the total match count before the cap.
    Read-only."""
    run_dir = _resolve_run(run_name)
    whitelist = set(RunStore.EVENT_PAYLOAD_REQUIRED.keys())
    if kind not in whitelist:
        raise ValueError(
            f"kind {kind!r} is not an exposable event kind. Allowed: "
            f"{sorted(whitelist)}"
        )
    if limit <= 0:
        raise ValueError("limit must be a positive integer")
    store = _read_store(run_dir)
    matching = store.read_events(kind)
    payload = {
        "run_name": run_name,
        "kind": kind,
        "n_total": len(matching),
        "events": [
            {"seq": e.get("seq"), "ts": e.get("ts"), "kind": e.get("kind"),
             "payload": e.get("payload")}
            for e in matching[:limit]
        ],
    }
    # xt red line: assert marker-free BEFORE populating the output model
    # (INDEX_M19 §3 — never schema-ify a tainted payload).
    _assert_marker_free(payload, where="get_events")
    return EventPage.model_validate(payload)


@mcp.tool(annotations=_READ_ONLY)
def verify_gate12(run_name: str) -> Gate12Report:
    """Re-audit a run's whole decision chain from the event stream (gate-12).

    WHEN TO USE: to independently VERIFY that a run's decision chain is intact —
    lifecycle pairing (layer 1), payload completeness (layer 2), and per-round
    knowledge-fingerprint threading + checkpoint reconciliation (layer 3). Use it
    to answer "is this run's audit trail complete and self-consistent?" without
    trusting the checkpoint. Reuses scripts/verify_run_chain.py verbatim.

    Returns a ``Gate12Report`` (pinned schema): ``{ok, layer, code, message,
    detail, summary}``. When ``ok`` is true the chain is complete and ``summary``
    carries counts + the fingerprint chain; otherwise ``layer``/``code``/
    ``message`` pinpoint the FIRST breakpoint. Read-only."""
    run_dir = _resolve_run(run_name)
    payload = _vrc.verify_run(run_dir).as_dict()
    # xt red line: assert marker-free BEFORE populating the output model.
    _assert_marker_free(payload, where="verify_gate12")
    return Gate12Report.model_validate(payload)


@mcp.tool(annotations=_READ_ONLY)
def diff_runs(run_a: str, run_b: str) -> RunDiff:
    """Diff two runs' decision chains and report the FIRST point they diverge.

    WHEN TO USE: to compare two runs (e.g. a baseline vs a variant, or two seeds)
    and find where their decisions first differ — a structural mismatch, a flipped
    content fingerprint (e.g. a claim adjudicated the other way), or a length
    difference. Use it to answer "do these two runs make the same decisions, and if
    not, at which round/node do they split?". Reuses the gate-12 diff verbatim.

    Returns a ``RunDiff`` (pinned schema): ``{diverged, first_divergence,
    n_nodes_a, n_nodes_b}``; when ``diverged`` is false the chains are
    node-for-node consistent. Read-only."""
    run_dir_a = _resolve_run(run_a)
    run_dir_b = _resolve_run(run_b)
    payload = _vrc.diff_decision_chain(run_dir_a, run_dir_b).as_dict()
    # xt red line: assert marker-free BEFORE populating the output model.
    _assert_marker_free(payload, where="diff_runs")
    return RunDiff.model_validate(payload)


# ============================================================ resources
#
# INDEX_M19 §1: the four pure key-addressed read faces, exposed with GET
# semantics as JSON resources. They call the SAME guarded tool implementations
# (single source of truth for both the payload shape and the xt guard); the
# {run_name} template parameter matches [^/]+ (no slash) at the SDK layer AND
# still goes through _resolve_run inside the implementation. Resources carry no
# outputSchema by design — mime_type declares the JSON contract (§3 not-copy).


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@mcp.resource("resource://expos/runs", mime_type="application/json")
def runs_catalog() -> str:
    """Catalog of runs under the runs root (JSON array; same shape as the
    list_runs tool). GET-semantic, cacheable, read-only."""
    return _json_dumps(list_runs())


@mcp.resource("resource://expos/runs/{run_name}/status", mime_type="application/json")
def run_status_resource(run_name: str) -> str:
    """One run's audit status summary (JSON object; same shape as the
    get_run_status tool). Read-only."""
    return _json_dumps(get_run_status(run_name))


@mcp.resource("resource://expos/runs/{run_name}/ledger", mime_type="application/json")
def run_ledger_resource(run_name: str) -> str:
    """One run's claim ledger — decision content only, no raw observations (JSON
    object; same shape as the get_claim_ledger tool). Read-only."""
    return _json_dumps(get_claim_ledger(run_name))


@mcp.resource("resource://expos/runs/{run_name}/fingerprints", mime_type="application/json")
def run_fingerprints_resource(run_name: str) -> str:
    """One run's per-round knowledge fingerprint chain (JSON array; same shape as
    the get_knowledge_fingerprints tool). Read-only."""
    return _json_dumps(get_knowledge_fingerprints(run_name))


def main() -> None:
    """Entry point: run the server over stdio transport."""
    mcp.run("stdio")


if __name__ == "__main__":
    main()
