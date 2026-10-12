# Architecture

## Principle

**The LLM is the interface. The database is the state machine.**
Claude turns messy language into a tool call. Postgres and plain code own the truth.

## Components

```
You (WhatsApp)
     |  ^
     v  |
   Kapso  ---------------------------+
     |  webhook (your replies)        | send (templates / messages)
     v                                |
 Webhook API (FastAPI)                |
     |                                |
     v                                |
 Claude agent  <--- Discovery job     |
     |             (daily)            |
     v                                |
 Events MCP server (domain tools)     |
     |                                |
     v                                |
  Postgres  <--- Reminder job (hourly, no LLM) ---+
```

| Component | Responsibility | Uses LLM |
|-----------|---------------|----------|
| Webhook API | Verify Kapso signature, allow-list my number, store inbound message, hand to agent | No |
| Claude agent | Interpret reply, call domain tools, compose the response | Yes |
| Events MCP server | Expose domain tools; the only way to read/write event state | No |
| Discovery job | Search sources, dedupe, filter by preferences, send digest | Yes (search/summarize) |
| Reminder job | Select due reminders, re-check state, send template, mark sent | No |
| Sender | Interface with `ConsoleSender` (dev) and `KapsoSender` (prod) | No |
| Postgres | Events, my decisions, reminders, messages, preferences | n/a |

## Data model

```
events(id, source, source_url UNIQUE, title, description, venue, city,
       starts_at, ends_at, price_text, is_free, raw jsonb, created_at)

my_events(event_id PK/FK, status, status_changed_at, snooze_until,
          ticket_url, notes)

reminders(id, event_id FK, kind, due_at, sent_at, canceled_at,
          UNIQUE(event_id, kind, due_at))
   kind: pre_event_3d | pre_event_1d | ticket_nag | snooze_resurface

messages(id, direction, wa_message_id UNIQUE, event_id FK NULL,
         quoted_wa_message_id, body, template_name, created_at)

preferences(id, kind, value, weight, reason, created_at)
   kind: dislike_topic | like_topic | dislike_organizer | max_price | max_distance_km
```

Key constraints:
- `events.source_url` unique -> discovery is idempotent.
- `reminders UNIQUE(event_id, kind, due_at)` + `sent_at` -> no duplicate sends.
- `messages.wa_message_id` unique -> webhook retries are idempotent.

## State machine (my_events.status)

```
suggested --decline--------> declined
suggested --going_no_ticket-> going_no_ticket --got_ticket--> going_with_ticket
suggested --going_ticket----> going_with_ticket
going_* --cancel-----------> cancelled
any open status --event passes--> expired
suggested/going_no_ticket --snooze--> same status, snooze_until set
```

Transitions live in `domain/` as pure functions: `(current_status, action, now) -> (new_status, reminders_to_create, reminders_to_cancel)`.

| Transition | Reminders created | Reminders canceled |
|-----------|-------------------|--------------------|
| -> going_no_ticket | pre_event_3d, ticket_nag (every 3 days) | none |
| -> going_with_ticket | pre_event_3d (and optional 1d) | ticket_nag |
| -> declined / cancelled | none | all pending |
| snooze | snooze_resurface at snooze_until | none |

## Events MCP server tools

| Tool | Purpose |
|------|---------|
| `save_event(...)` | Insert event, ignore duplicates |
| `list_new_events(since)` | Events not yet sent to me |
| `get_event(event_id)` | Details for answering questions |
| `list_my_events(status?, from?, to?)` | Agenda and "what's coming up" |
| `set_status(event_id, action, note?)` | Run a state machine transition |
| `snooze(event_id, days)` | Resurface later |
| `record_preference(kind, value, reason?)` | Save dislike/like |
| `list_preferences()` | Used by discovery and by me |

No raw SQL tool. The agent cannot touch tables directly.

## Key flows

### Inbound reply
1. Kapso posts webhook -> API verifies signature and sender, stores message (idempotent on `wa_message_id`).
2. Resolve target event: quoted message ID -> else button payload event ID -> else latest event message -> else ask me.
3. Button payloads skip the LLM and call `set_status` directly.
4. Free text goes to the agent with the resolved event as context; agent calls tools and replies.

### Reminder job (hourly)
1. Select reminders where `due_at <= now`, `sent_at IS NULL`, `canceled_at IS NULL`.
2. Re-check `my_events.status` and event date; cancel stale reminders instead of sending.
3. Send via `Sender` with the matching template; store in `messages`; set `sent_at`.
4. Schedule the next `ticket_nag` if still `going_no_ticket`.

### Discovery job (daily)
1. Read preferences.
2. Search sources, normalize, `save_event` each (duplicates ignored).
3. Send each new event as one message with quick-reply buttons; store `wa_message_id` linked to `event_id`.

## Testing strategy

- Unit tests: state machine and reminder rules with a fixed clock.
- Integration tests: repositories against Postgres in Docker.
- Reminder job tests with `FakeSender`.
- Agent evals in `evals/`: cases of (reply text, context) -> expected tool call. Run in CI, track pass rate.
- Webhook tests: signature check, idempotency, ambiguous reply handling.

## Deployment (target)

- Webhook API + jobs on a small container host (Cloud Run / Fly.io / Railway).
- Managed Postgres (Neon or Supabase) in production, Docker locally.
- Scheduling via the host's cron or GitHub Actions cron, defined in code in the repo.

## Related ADRs

See [adr/README.md](adr/README.md).

- [0001](adr/0001-reminders-do-not-use-the-llm.md): Reminders do not use the LLM
- [0002](adr/0002-domain-tools-instead-of-raw-sql.md): Domain tools instead of raw SQL
- [0003](adr/0003-sender-interface-to-isolate-kapso.md): Sender interface to isolate Kapso
- [0004](adr/0004-structured-buttons-before-free-text.md): Structured buttons before free-text interpretation
