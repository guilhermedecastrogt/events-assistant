# 0006: Public MCP exposes ingest only

- Status: Accepted
- Date: 2026-10-12

## Context

The Claude scheduled task ([0005](0005-discovery-via-claude-scheduled-task.md)) has to reach our app
over the internet as a remote MCP server. The chat agent also has MCP-style domain tools (change
status, snooze, record preferences, send more events). Exposing one MCP server with all tools would
let anyone holding the connector credential, or a prompt-injected task, change my decisions,
read my agenda or trigger WhatsApp messages.

## Decision

There are two separate tool surfaces:

- **Public ingest MCP** at `/mcp` with exactly three tools: `submit_events`,
  `get_search_preferences`, `list_known_event_urls`. It is authenticated, rate limited (per
  credential), size limited (≤ 50 events, ≤ 256 KB per call) and strictly validated (Pydantic,
  extra fields forbidden, tz-aware dates in a bounded window, length caps, normalised URLs).
  It can only add `suggested` events and read search-relevant data.
- **Internal tools** for the chat agent run in-process (stdio in local dev) and are never mounted
  on a public route.

The ingest MCP and the Kapso webhook are routes of the **same FastAPI app**, so there's a single
deployment. Each route has its own authentication.

The exact auth method that Claude custom connectors support (OAuth vs. other options) is **to be
verified before M3**. The result will be recorded in a follow-up ADR.

## Consequences

- The worst case for a leaked credential or a misbehaving task is junk `suggested` events. Those
  are bounded by rate limits, visible in `ingest_runs`, and filtered again by preferences.
- Ingest tools and agent tools reuse the same domain and repository code but are registered
  separately; tests assert that the public server lists exactly three tools.
- One app keeps hosting simple. A crash or overload in one route can affect the other, which is
  acceptable at personal volume.
- Adding a public tool needs an ADR update. Agent-only tools never become public by accident.
