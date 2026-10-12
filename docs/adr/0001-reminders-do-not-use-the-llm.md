# 0001: Reminders do not use the LLM

- Status: Accepted
- Date: 2026-10-12

## Context

The main promise of the assistant is "don't let me miss an event I said I'd attend". Reminders
(3 days before, ticket nags) must fire at the right time, exactly once, and only for events that
are still relevant. An LLM is non-deterministic, can be slow or unavailable, costs money per call,
and is hard to test against a fixed clock.

## Decision

The reminder path contains no LLM calls. Reminders are rows in the `reminders` table created by
the pure state machine in `domain/` when a status changes. An hourly reminder job selects due rows
(`due_at <= now`, not sent, not canceled), re-checks the event status and date, sends a fixed
WhatsApp template through the `Sender` interface, and sets `sent_at`. The clock and sender are
injected so the job runs deterministically in tests.

## Consequences

- Reminders keep working when the Anthropic API is down or the budget is exhausted.
- Reminder logic is fully unit-testable with a fixed clock and a fake sender.
- `UNIQUE(event_id, kind, due_at)` plus `sent_at` make sends idempotent.
- Reminder wording is fixed by templates rather than personalised; acceptable for v1 and required
  anyway by WhatsApp's rules for proactive messages.
