"""expos_mcp — a READ-ONLY Model Context Protocol (MCP) surface over the expos
Research OS audit / knowledge plane.

This is a separate top-level package (NOT inside ``expos/``) so it can be added,
versioned and reasoned about independently of the OS kernel. It never writes:
every tool is a pure read of a run's ``events.jsonl`` / ``checkpoint.json`` under
the runs root, and every return value is passed through a recursive marker-key
guard so the hidden simulation truth surface (sim_reader) can never leak to an
external LLM client. See ``expos_mcp/server.py`` and ``docs/MCP_SURFACE.md``.

The server object lives in ``expos_mcp.server`` (import it there, not here, so
``python -m expos_mcp.server`` does not re-import this package's side effects).
"""

