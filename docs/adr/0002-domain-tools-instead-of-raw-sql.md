# 0002: Domain tools instead of raw SQL

- Status: Accepted
- Date: 2026-10-12

## Context

The agent needs to read and change event state ("going, ticket later", "what's coming up?").
The quickest option is to hand the model a SQL tool. That lets the model bypass the state
machine, write invalid statuses, forget to create or cancel reminders, or run destructive
statements after a misread reply or a prompt injection hidden in event descriptions.

## Decision

Both tool surfaces expose only domain tools: the agent's internal tools (`get_event`,
`list_my_events`, `set_status`, `snooze`, `record_preference`, `list_preferences`, ...) and the
public ingest MCP (`submit_events`, `get_search_preferences`, `list_known_event_urls`; see
[0006](0006-public-mcp-exposes-ingest-only.md)). Each tool validates input with Pydantic and calls
repository functions that run the state machine and write status and reminders in one transaction.
There is no generic query tool.

## Consequences

- Every state change goes through the same tested transition function.
- The blast radius of a bad tool call is one valid transition on one event.
- Tool names and arguments are easy to assert in evals (`reply -> expected tool call`).
- New questions the agent cannot answer need a new tool or parameter; this is deliberate friction.
