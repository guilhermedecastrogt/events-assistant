# PRD: events-assistant

## 1. Problem

Tech events (meetups, conferences, workshops) are scattered across Meetup, Eventbrite, Luma and
organizer sites. I miss good ones, forget to buy tickets, and forget events I said I'd attend.

## 2. Goal

A personal WhatsApp assistant that:
1. Finds relevant tech events for me and sends them.
2. Understands my replies in plain language and records my decision.
3. Reminds me before events I'm going to, and nags (politely) about tickets I haven't bought.
4. Learns what I don't like and stops suggesting it.

Success: for a month I use WhatsApp only, never a spreadsheet or calendar notes, and don't miss an event I wanted to attend.

## 3. User

Single user (me). No multi-tenancy. Auth is limited to: verifying the Kapso webhook signature, an
allow-listed phone number, and one credential for the Claude scheduled task calling the ingest MCP.

## 4. Features

### F1. Weekly discovery via a Claude scheduled task + ingest MCP
- A Claude scheduled task (configured in the Claude app, not in this repo's runtime) runs weekly,
  **Sunday evening** (default 18:00 Europe/Dublin). Its prompt lives in `prompts/discovery.md`.
- The task searches the web for tech events in/near Dublin for the next few weeks. It does not
  send messages and has no access to my WhatsApp.
- It talks to this app only through the public **ingest MCP**:
  1. `get_search_preferences()` returns what I like/dislike, price and distance limits.
  2. `list_known_event_urls()` returns upcoming event URLs already stored, so it can skip them.
  3. `submit_events(events)` sends candidates in batches (max 50). It is called **at least once per
     run, with an empty list if nothing new was found**, so every run is recorded.
- The app (not the task) validates each event, deduplicates on source URL, filters by stored
  preferences and saves accepted events with status `suggested`. Every call is logged as an ingest run.
- A **digest job** sends new `suggested` events to WhatsApp:
  - one short header message first ("12 new events this week"),
  - then **one message per event**, so a reply maps to exactly one event,
  - capped at a configurable maximum (`DIGEST_MAX_EVENTS`, default 10, soonest first). The rest
    stay pending and are sent on request ("show more" button or reply sends the next batch).
  - Header and event messages are proactive, so they use approved WhatsApp templates.

### F2. Reply handling
I can answer in natural language or with quick-reply buttons.
- "Going" -> status `going_with_ticket`
- "Going, ticket later" -> status `going_no_ticket`
- "Not going" / "skip" -> status `declined`, optional reason saved as a preference
- "Remind me later" -> snooze, resurface in N days
- Questions ("is it free?", "where is it?") are answered from stored event data.

Replies are linked to the right event via the quoted WhatsApp message ID, falling back to the most recent event message, and asking me if still ambiguous.

### F3. Pre-event reminder
- 3 days before every event with status `going_*`, send a reminder.
- Optional second reminder the day before.
- Reminders re-check state at send time: skipped if I declined, cancelled, or the event passed.

### F4. Ticket nagging
- For `going_no_ticket`, remind me every 3 days until I buy the ticket, decline, or the event passes.
- Reminder is a template with buttons: "Got it", "Not going anymore", "Remind me later".
- Ticket reminders stop automatically once the pre-event reminder window starts, or earlier if the event is known to be sold out.

### F5. Preferences / memory
- Record dislikes and likes from my replies (topics, organizers, price, distance).
- The scheduled task reads preferences (via `get_search_preferences`) before searching, and the
  app filters submitted events against them again before saving.
- I can ask "what do you know about my preferences?" and edit them.

### F6. Agenda queries
- "What's coming up?" lists my `going_*` events.
- "Do I have anything in the next 3 days?" answers from the database.

### F7. Discovery heartbeat
- Every **Monday 12:00 Europe/Dublin** a heartbeat check looks for at least one ingest run in the
  **7 days before the check**.
- The window does not depend on which day the task runs: moving the task to another day keeps
  working as long as it runs weekly before the check.
- If no run is found, send me a WhatsApp alert ("No events were ingested this week; check the
  scheduled task"). This is a proactive message, so it uses an approved template, like reminders.
- Check day/time are configurable.

## 5. Non-goals (v1)

- Multi-user support, web UI, calendar sync (maybe v2), buying tickets for me, voice messages, group chats.
- Scraping or search code in this repo: searching is the scheduled task's job.

## 6. Non-functional requirements

- Reminders, digests and the heartbeat are deterministic and never depend on the LLM.
- No duplicate reminders (idempotent sends, `sent_at` + unique key).
- All times shown in Europe/Dublin, stored in UTC.
- Cost: under ~10 EUR/month for LLM + hosting at personal volume.
- The agent can only act through domain tools; no raw SQL.
- The public endpoint exposes only the narrow ingest MCP (three tools). The chat agent's tools are never public.
- Web content is untrusted data: validated, length-capped, stored as data, never treated as instructions.
- Ingest is authenticated and rate limited.
- Everything reproducible locally with `docker compose up`.

## 7. Milestones

| # | Milestone | Outcome |
|---|-----------|---------|
| M0 | Foundations | Repo, docs, CI, Docker Postgres |
| M1 | Schema + state machine | Tested transitions and reminder rules |
| M2 | Reminder job | Due reminders sent via fake console sender |
| M3 | Ingest MCP + public deployment | Authenticated public `/mcp` with the 3 ingest tools; the Claude scheduled task submits events end to end |
| M4 | Agent + evals | Terminal chat works; 30+ eval cases pass in CI |
| M5 | Webhook + Kapso | Real WhatsApp two-way, templates, buttons |
| M6 | Digest job + heartbeat | Weekly header + capped per-event digest, "show more", Monday heartbeat alert |
| M7 | Hardening | Observability, retries, README polish |

## 8. Risks

| Risk | Mitigation |
|------|-----------|
| WhatsApp 24h window blocks proactive messages | Approved Kapso templates for reminders, digests and the heartbeat alert |
| Kapso MCP is alpha | Wrap Kapso behind a `Sender` interface; swap if it changes |
| LLM misreads a reply | Structured buttons first; evals; confirm before destructive changes |
| Reply linked to wrong event | Quoted message ID mapping; ask when ambiguous |
| Prompt injection from web content (event pages telling the task or the agent to do things) | The task can only submit data; the app validates and length-caps every field and stores it as data; the chat agent receives event text wrapped as quoted data, never as instructions; no send or write-state tool is public |
| Auth method for Claude custom connectors is unverified (OAuth vs. other options) | Verify what the Claude app supports for remote MCP connectors **before building M3**; record it in an ADR |
| Scheduled task silently stops running (disabled, quota, auth expired) | F7 heartbeat alert every Monday |
| Task submits junk or too much | Strict validation, per-call and per-day limits, every run and rejection logged in `ingest_runs` |

## 9. Open questions

- Which hosting for the public app (Cloud Run, Fly.io, Railway)?
- Scheduled task day/time: Sunday 18:00 is the default; confirm once real runs exist.
- Digest send time: right after ingest, or Monday morning?
