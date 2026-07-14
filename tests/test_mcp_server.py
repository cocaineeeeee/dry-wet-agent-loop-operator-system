"""Offline tests for the read-only expos MCP surface (expos_mcp/server.py).

Positives run a REAL one-round mcl loop (the same substrate as
tests/test_verify_run_chain.py) into a tmp runs root, then drive the tool
implementations directly AND through the official in-memory MCP client/server
harness (``mcp.shared.memory.create_connected_server_and_client_session``, no
real process, no stdio — INDEX_M19 §4). Negatives prove each guard is
load-bearing:

  * truth isolation — a fabricated return carrying a forbidden marker key makes
    the recursive guard raise (the "delete-the-guard-goes-red" contract);
  * path safety — ``../`` / leading ``/`` / null-byte / symlink-escape run_names
    are refused before any read (INDEX_M19 §2 attack set), both directly and
    end-to-end through the MCP protocol (isError);
  * kind whitelist — an unregistered event kind is refused;
  * limit — get_events honours its cap.

SDK note (mcp==1.16.0): the lowlevel server's call_tool handler catches ALL
tool-body exceptions and returns them as ``isError=True`` results — even under
``raise_exceptions=True`` (which only lifts request-handler-level errors). So
protocol-level negatives assert ``isError`` + message text, not a raise.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from expos.kernel.store import RunStore
from expos.mcl import run_mcl_loop
from expos.planner.certification import AggregatedCertification
from expos.qc.certification_stats import AggregationConfig, ClaimHead

from expos_mcp import server as S
from expos_mcp.server import MarkerIsolationError, _assert_marker_free

_REPO = Path(__file__).resolve().parents[1]
_DOMAIN = _REPO / "domains" / "solvent_screen.yaml"

_ALL_TOOLS = {"list_runs", "get_run_status", "get_claim_ledger",
              "get_knowledge_fingerprints", "get_events", "verify_gate12",
              "diff_runs"}


def _aggregated_cert() -> AggregatedCertification:
    head = ClaimHead(
        claim_id="c_polar_responds_higher",
        statement="polar solvents give a higher plate-reader response",
        favorable_direction="higher",
        focal_group=("cand_ethanol",),
        reference_group=("cand_acetonitrile",),
    )
    return AggregatedCertification(
        [head], config=AggregationConfig(run_fingerprint="mcp_test")
    )


@pytest.fixture(scope="module")
def runs_root(tmp_path_factory) -> Path:
    """A tmp runs root holding one REAL one-round mcl run named ``r1``."""
    root = tmp_path_factory.mktemp("mcp_runs")
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=root / "r1",
                 certification=_aggregated_cert())
    return root


@pytest.fixture()
def rooted(runs_root, monkeypatch) -> Path:
    """Point the server's runs root at the fixture root for the duration of a test."""
    monkeypatch.setenv("EXPOS_MCP_RUNS_ROOT", str(runs_root))
    return runs_root


# ============================================================ positive: tool logic

def test_list_runs_finds_the_real_run(rooted):
    runs = S.list_runs()
    names = {r["run_name"] for r in runs}
    assert "r1" in names
    r1 = next(r for r in runs if r["run_name"] == "r1")
    assert r1["exit_status"] == "success"
    assert r1["completed_rounds"] == 1
    assert r1["n_rounds"] == 1


def test_get_run_status_summary(rooted):
    st = S.get_run_status("r1")
    assert st["run_name"] == "r1"
    assert st["completed_rounds"] == 1
    assert st["exit_status"] == "success"
    assert st["n_rounds"] == 1
    assert len(st["knowledge_fingerprints"]) == 1
    assert all(fp for fp in st["knowledge_fingerprints"])
    # effective status is derived from the append-only ledger.
    assert st["effective_status"]  # non-empty: at least one adjudicated claim
    assert all(isinstance(v, str) for v in st["effective_status"].values())


def test_get_claim_ledger_shape_and_no_raw_obs(rooted):
    led = S.get_claim_ledger("r1")
    assert led["run_name"] == "r1"
    assert led["claims"], "ledger should carry claim records"
    rec = led["claims"][0]
    # decision content is present ...
    for key in ("claim_id", "version", "status", "statement", "evidence_strength",
                "supersedes", "is_annotation", "decision_fn_id", "n_evidence",
                "consumed_knowledge_fingerprint", "statistic"):
        assert key in rec
    # ... but the raw per-well observation stream is NOT (only the count).
    assert isinstance(rec["n_evidence"], int)
    assert "observations" not in rec
    assert "e_value_stats" in led


def test_get_knowledge_fingerprints(rooted):
    fps = S.get_knowledge_fingerprints("r1")
    assert len(fps) == 1
    assert fps[0]["round_index"] == 0
    assert fps[0]["fingerprint"]
    assert fps[0]["n_hypotheses"] == 2


def test_verify_gate12_green(rooted):
    report = S.verify_gate12("r1")
    assert report.ok is True
    assert report.layer is None
    assert report.summary["n_rounds"] == 1


def test_diff_runs_self_is_consistent(rooted):
    diff = S.diff_runs("r1", "r1")
    assert diff.diverged is False
    assert diff.n_nodes_a == diff.n_nodes_b


def test_get_events_returns_payloads(rooted):
    page = S.get_events("r1", "claim_decision")
    assert page.run_name == "r1"
    assert page.kind == "claim_decision"
    assert page.events, "expected at least one claim_decision"
    for e in page.events:
        assert e.kind == "claim_decision"
        assert isinstance(e.payload, dict)


# ============================================================ in-memory MCP session
# Official harness (INDEX_M19 §4): true protocol handshake/list/call, no process.

def _session_test(coro_fn):
    """Run an async in-memory-session test body against the FastMCP lowlevel server."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def _run() -> None:
        async with create_connected_server_and_client_session(
            S.mcp._mcp_server, raise_exceptions=True
        ) as session:
            await coro_fn(session)

    asyncio.run(_run())


def test_inmemory_tools_readonly_annotations_and_schemas(rooted):
    """list_tools over the real protocol: all seven tools present, every one
    annotated readOnlyHint=True (git-server read-command pattern), and the three
    schema-pinned tools advertise an outputSchema."""

    async def body(session):
        tools = await session.list_tools()
        by_name = {t.name: t for t in tools.tools}
        assert _ALL_TOOLS <= set(by_name)
        for name, tool in by_name.items():
            assert tool.annotations is not None, f"{name} missing annotations"
            assert tool.annotations.readOnlyHint is True, f"{name} not readOnlyHint"
            assert tool.annotations.openWorldHint is False
        for pinned in ("get_events", "verify_gate12", "diff_runs"):
            schema = by_name[pinned].outputSchema
            assert schema is not None, f"{pinned} missing outputSchema"
            assert schema.get("additionalProperties") is False  # extra="forbid"

    _session_test(body)


def test_inmemory_call_and_structured_content(rooted):
    """call_tool over the real protocol: structuredContent matches the pinned
    schema shape and the verdict is green on the real run."""

    async def body(session):
        res = await session.call_tool("list_runs", {})
        assert res.isError is False

        res2 = await session.call_tool("verify_gate12", {"run_name": "r1"})
        assert res2.isError is False
        sc = res2.structuredContent
        assert sc["ok"] is True
        assert set(sc) == {"ok", "layer", "code", "message", "detail", "summary"}

        res3 = await session.call_tool(
            "get_events", {"run_name": "r1", "kind": "claim_decision", "limit": 1})
        assert res3.isError is False
        assert res3.structuredContent["kind"] == "claim_decision"
        assert len(res3.structuredContent["events"]) == 1

    _session_test(body)


def test_inmemory_resources_listed_and_readable(rooted):
    """The four pure read faces are addressable as resources (INDEX_M19 §1):
    one static catalog + three run-templated JSON projections."""

    async def body(session):
        resources = await session.list_resources()
        uris = {str(r.uri) for r in resources.resources}
        assert "resource://expos/runs" in uris

        templates = await session.list_resource_templates()
        t_uris = {t.uriTemplate for t in templates.resourceTemplates}
        assert {"resource://expos/runs/{run_name}/status",
                "resource://expos/runs/{run_name}/ledger",
                "resource://expos/runs/{run_name}/fingerprints"} <= t_uris

        catalog = await session.read_resource("resource://expos/runs")
        data = json.loads(catalog.contents[0].text)
        assert any(r["run_name"] == "r1" for r in data)

        status = await session.read_resource("resource://expos/runs/r1/status")
        st = json.loads(status.contents[0].text)
        assert st["exit_status"] == "success"

    _session_test(body)


def test_inmemory_negatives_surface_as_errors(rooted):
    """End-to-end protocol negatives: traversal and whitelist violations come back
    as isError results (mcp 1.16 lowlevel call_tool catches tool-body exceptions
    even under raise_exceptions=True)."""

    async def body(session):
        res = await session.call_tool("get_run_status", {"run_name": "../../etc"})
        assert res.isError is True
        assert "traversal" in res.content[0].text

        res2 = await session.call_tool(
            "get_events", {"run_name": "r1", "kind": "truth_dump"})
        assert res2.isError is True
        assert "not an exposable event kind" in res2.content[0].text

    _session_test(body)


# ============================================================ negative: truth isolation

def test_marker_guard_raises_on_forbidden_key():
    """Delete-the-guard-goes-red: a fabricated return carrying a truth marker key
    (nested) makes the recursive guard raise — proof the guard is load-bearing."""
    tainted = {
        "run_name": "r1",
        "claims": [{"claim_id": "c", "provenance": {"truth_profile": "polar_high"}}],
    }
    with pytest.raises(MarkerIsolationError):
        _assert_marker_free(tainted, where="fabricated")


def test_marker_guard_catches_top_level_and_case(rooted):
    """The blacklist match is case-insensitive and fires at any depth, including a
    bare ``truth`` / ``harvest_truth`` key."""
    with pytest.raises(MarkerIsolationError):
        _assert_marker_free({"Truth": 1}, where="x")
    with pytest.raises(MarkerIsolationError):
        _assert_marker_free([{"ok": [{"harvest_truth": []}]}], where="x")


def test_marker_guard_passes_clean_tool_output(rooted):
    """A genuine tool return is marker-free (the guard does not false-positive),
    including the pydantic output models (walked via model_dump)."""
    _assert_marker_free(S.get_claim_ledger("r1"), where="get_claim_ledger")
    _assert_marker_free(S.list_runs(), where="list_runs")
    _assert_marker_free(S.verify_gate12("r1"), where="verify_gate12")


# ============================================================ negative: path safety
# INDEX_M19 §2 attack set: traversal, leading-slash injection, null byte, symlink.

@pytest.mark.parametrize("bad", [
    "../etc",            # plain traversal
    "../../tmp",         # deeper traversal
    "r1/../../r1",       # embedded .. segment
    "/etc",              # leading slash — Path(root)/"/etc" would DROP the root
    "\\evil",            # leading backslash
    "r1\x00",            # null byte
    "",                  # empty
])
def test_path_attacks_refused(rooted, bad):
    with pytest.raises(ValueError):
        S.get_run_status(bad)


def test_symlink_escape_refused(rooted, tmp_path):
    """A symlink planted under the runs root pointing outside it must be refused:
    resolve() follows the link, so the realpath fails is_relative_to."""
    outside = tmp_path / "outside_run"
    outside.mkdir()
    (outside / "events.jsonl").write_text("", encoding="utf-8")
    link = rooted / "sneaky"
    link.symlink_to(outside)
    try:
        with pytest.raises(ValueError):
            S.get_run_status("sneaky")
    finally:
        link.unlink()


def test_missing_run_refused(rooted):
    with pytest.raises(ValueError):
        S.get_run_status("no_such_run")


# ============================================================ negative: kind whitelist

def test_get_events_rejects_unregistered_kind(rooted):
    # not in RunStore.EVENT_PAYLOAD_REQUIRED
    assert "checkpoint" not in RunStore.EVENT_PAYLOAD_REQUIRED
    with pytest.raises(ValueError):
        S.get_events("r1", "checkpoint")
    with pytest.raises(ValueError):
        S.get_events("r1", "truth_dump")


def test_get_events_limit_enforced(rooted):
    full = S.get_events("r1", "routing", limit=1000)
    assert full.n_total >= 2, "one-round run emits several routing events"
    capped = S.get_events("r1", "routing", limit=1)
    assert len(capped.events) == 1
    assert capped.n_total == full.n_total  # cap trims the page, not the count
    with pytest.raises(ValueError):
        S.get_events("r1", "routing", limit=0)
