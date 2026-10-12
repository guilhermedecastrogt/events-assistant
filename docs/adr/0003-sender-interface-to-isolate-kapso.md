# 0003: Sender interface to isolate Kapso

- Status: Accepted
- Date: 2026-10-12

## Context

WhatsApp access goes through Kapso, whose MCP/API surface is young and may change. Jobs and the
agent must send messages, but tests and local development should not hit a real phone number.

## Decision

Define a small `Sender` interface in `senders/` (send a template, send a text, return the WhatsApp
message ID). Implementations: `ConsoleSender` for local development, `FakeSender` for tests (records
calls), and `KapsoSender` for production. Jobs and the API receive a `Sender` by injection; nothing
outside `senders/kapso.py` imports Kapso-specific code.

## Consequences

- Reminder and discovery jobs are testable without network access.
- Changing or replacing Kapso touches one module.
- The interface must stay minimal; Kapso-only features need an explicit decision before leaking in.
