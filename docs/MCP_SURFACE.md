# expos MCP surface (read-only audit / knowledge plane)

`expos_mcp/` exposes the expos Research OS **read-only** audit, knowledge and
decision-chain plane as a [Model Context Protocol](https://modelcontextprotocol.io)
server, so an external LLM agent (Claude, etc.) can query a run's status, claim
ledger and decision chain without any write access and without ever seeing the
hidden simulation truth surface.

- Server: `expos_mcp/server.py` (built on `mcp.server.fastmcp.FastMCP`, pinned
  `mcp==1.16.0` — API anchored on the local site-packages, not the SDK GitHub HEAD)
- Transport: stdio — `python -m expos_mcp.server`
- Runs root: `/Data1/ericyang/dry_wet_agent_os/runs`, override with
  `EXPOS_MCP_RUNS_ROOT` (fixed at startup; deliberately NOT re-rootable via the
  MCP roots protocol)
- Tests: `tests/test_mcp_server.py` (offline; official in-memory MCP harness
  `create_connected_server_and_client_session` + direct calls)
- Reference walkthrough: `r4_os_references/INDEX_M19_MCPSRV.md`

## Red lines (enforced in code, not just documented)

1. **Read-only v1.** No tool writes, appends, deletes or mutates. Every tool is a
   pure read of a run's append-only `events.jsonl` and its `checkpoint.json`.
   Every tool carries `ToolAnnotations(readOnlyHint=True, destructiveHint=False,
   idempotentHint=True, openWorldHint=False)`. A future write surface is a
   separate project; no write / network-egress / roots-rebind / elicitation
   pattern is present.
2. **Truth isolation.** The hidden simulation truth surface (sim_reader
   `TRUTH_PROFILES` / `TruthSurface` / `truth_records` / `harvest_truth`, and the
   store's opaque `truth/` sidecar) is **never** exposed. Every payload runs
   through `_assert_marker_free`, a recursive guard whose forbidden-key blacklist
   mirrors `expos/agent/llm_backend.py`; it raises the instant a marker key
   appears. For schema-pinned tools the guard runs **before** the output model is
   populated. Dropping the guard turns the isolation negative tests red.
3. **Path confinement.** A single choke point `_resolve_run` guards every path:
   literal early-reject of null bytes, leading `/` or `\` (leading-slash
   injection would silently drop the root in `Path(root) / name`) and any `..`
   segment; then **both sides resolved** (`resolve()` = realpath, so symlinks
   cannot escape) and `is_relative_to` the resolved runs root. `run_name` is
   never URL-decoded.
4. **No edits to `expos/`.** The store reader, the claim-ledger model and
   `scripts/verify_run_chain.py` are imported and reused, never re-implemented.

## Tools

All seven tools are annotated `readOnlyHint=True`. The three computing tools pin
their return contract as a pydantic model (`extra="forbid"`) — FastMCP 1.16
auto-publishes the `outputSchema` on `list_tools` and returns validated
`structuredContent` alongside text.

| Tool | Args | Returns | When to use |
|------|------|---------|-------------|
| `list_runs` | — | `[{run_name, domain, loop, completed_rounds, exit_status, n_events, n_rounds}]` | Discover runs first; get names + terminal status |
| `get_run_status` | `run_name` | `{completed_rounds, exit_status, n_events, n_rounds, knowledge_fingerprints, effective_status}` | One-run overview: how far it got + current beliefs |
| `get_claim_ledger` | `run_name` | `{effective_status, claims[], e_value_stats}` | The auditable decision record (statements, statuses, supersede chain, e-value/effect summary) — no raw observations |
| `get_knowledge_fingerprints` | `run_name` | `[{round_index, fingerprint, n_hypotheses, n_claims}]` | Per-round knowledge fingerprint chain |
| `get_events` | `run_name, kind, limit=50` | `EventPage` (pinned schema): `{run_name, kind, n_total, events[]}` | Raw payloads of one whitelisted event kind |
| `verify_gate12` | `run_name` | `Gate12Report` (pinned schema): `{ok, layer, code, message, detail, summary}` | Independently re-audit the whole decision chain (3 layers) |
| `diff_runs` | `run_a, run_b` | `RunDiff` (pinned schema): `{diverged, first_divergence, n_nodes_a, n_nodes_b}` | Find the first point two runs' decisions diverge |

`get_events` `kind` must be one of the payload-registered kinds
(`RunStore.EVENT_PAYLOAD_REQUIRED`): `routing`, `action_consumed`,
`redo_reconciliation`, `run_stop`, `risk_map_applied`, `aggregation_alpha`,
`reclassification`, `learning_weight_assigned`, `knowledge_updated`,
`promotion_decision`, `claim_decision`, `agent_shadow_proposal`,
`agent_generation_failed`. Any other kind (including raw-sidecar kinds) is refused.

## Resources

The four pure key-addressed read faces are additionally exposed as GET-semantic
JSON resources (`mime_type="application/json"`), backed by the same guarded
implementations as the tools (INDEX_M19 §1: "pure key-addressed projection →
resource; value-validated / computing call → tool"):

| Resource URI | Content |
|---|---|
| `resource://expos/runs` | Runs catalog (same shape as `list_runs`) |
| `resource://expos/runs/{run_name}/status` | Run status summary |
| `resource://expos/runs/{run_name}/ledger` | Claim ledger (decision content only) |
| `resource://expos/runs/{run_name}/fingerprints` | Knowledge fingerprint chain |

`{run_name}` matches a single path segment (`[^/]+`) at the SDK layer and still
passes through `_resolve_run` inside the implementation.

## Client setup

Register with Claude Code (one line):

```bash
claude mcp add expos -- python -m expos_mcp.server
```

Point it at a different runs tree:

```bash
EXPOS_MCP_RUNS_ROOT=/path/to/runs claude mcp add expos -- python -m expos_mcp.server
```

Generic MCP client config (stdio):

```json
{
  "mcpServers": {
    "expos": {
      "command": "python",
      "args": ["-m", "expos_mcp.server"],
      "env": { "EXPOS_MCP_RUNS_ROOT": "/Data1/ericyang/dry_wet_agent_os/runs" }
    }
  }
}
```

Run `PYTHONPATH` must include the repo root (or install the repo) so
`import expos` / `import expos_mcp` resolve.
