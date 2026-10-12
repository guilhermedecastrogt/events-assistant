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

Single user (me). No multi-tenancy, no auth beyond verifying the Kapso webhook signature and an allow-listed phone number.

## 4. Features

### F1. Event discovery (daily)
- Search configured sources for tech events in/near Dublin for the next 30 days.
- Deduplicate against the database (unique key on source URL, fallback title + start date).
- Filter using stored preferences.
- Send new events to me as a WhatsApp digest, one message per event so replies map to one event.

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
- Discovery reads preferences before sending.
- I can ask "what do you know about my preferences?" and edit them.

### F6. Agenda queries
- "What's coming up?" lists my `going_*` events.
- "Do I have anything in the next 3 days?" answers from the database.

## 5. Non-goals (v1)

- Multi-user support, web UI, calendar sync (maybe v2), buying tickets for me, voice messages, group chats.

## 6. Non-functional requirements

- Reminders are deterministic and never depend on the LLM.
- No duplicate reminders (idempotent sends, `sent_at` + unique key).
- All times shown in Europe/Dublin, stored in UTC.
- Cost: under ~10 EUR/month for LLM + hosting at personal volume.
- The agent can only act through domain tools; no raw SQL.
- Everything reproducible locally with `docker compose up`.

## 7. Milestones

| # | Milestone | Outcome |
|---|-----------|---------|
| M0 | Foundations | Repo, docs, CI, Docker Postgres |
| M1 | Schema + state machine | Tested transitions and reminder rules |
| M2 | Reminder job | Due reminders sent via fake console sender |
| M3 | Events MCP server | Domain tools over MCP |
| M4 | Agent + evals | Terminal chat works; 30+ eval cases pass in CI |
| M5 | Webhook + Kapso | Real WhatsApp two-way, templates, buttons |
| M6 | Discovery job | Daily digest with preferences |
| M7 | Hardening | Observability, retries, deploy, README polish |

## 8. Risks

| Risk | Mitigation |
|------|-----------|
| WhatsApp 24h window blocks proactive messages | Approved Kapso templates for reminders and digests |
| Kapso MCP is alpha | Wrap Kapso behind a `Sender` interface; swap if it changes |
| LLM misreads a reply | Structured buttons first; evals; confirm before destructive changes |
| Event sources change or block scraping | Multiple sources, treat each as a plugin, log failures |
| Reply linked to wrong event | Quoted message ID mapping; ask when ambiguous |

## 9. Open questions

- Which sources first (Meetup, Luma, Eventbrite, Dublin tech calendars)?
- Which hosting for the webhook (Cloud Run, Fly.io, Railway)?
- Digest cadence: one daily batch, or push events as discovered?
