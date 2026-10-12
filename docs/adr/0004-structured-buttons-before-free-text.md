# 0004: Structured buttons before free-text interpretation

- Status: Accepted
- Date: 2026-10-12

## Context

Most replies are one of a handful of decisions: going, going but ticket later, not going, remind me
later. Sending every reply through the LLM adds cost and latency and creates room for misreading
the decision or attaching it to the wrong event.

## Decision

Event digests and reminders use WhatsApp quick-reply buttons whose payload carries the action and
the event ID. The webhook maps button payloads directly to `set_status` / `snooze` without calling
the LLM. Only free text goes to the agent, with the target event resolved from the quoted message
ID when possible.

## Consequences

- The common path is deterministic, cheap and testable.
- Button payloads must be versioned and validated, since they are user-controlled input.
- The agent still handles questions, preferences and nuance, so evals remain necessary.
- Templates with buttons need Kapso/WhatsApp approval before M5.
