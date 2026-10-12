# events-assistant

A personal WhatsApp assistant that finds tech events, sends them to me, understands my replies
("going, ticket later", "not for me"), and reminds me at the right time.

Owner: solo student project, built as a professional-grade portfolio repo.
Timezone for all user-facing times: Europe/Dublin. Store timestamps as UTC (`timestamptz`).

## Core principle

**The LLM is the interface. The database is the state machine.**

- Claude interprets natural language and picks a tool call.
- Code and Postgres own all facts: event status, dates, reminders.
- Reminders fire from deterministic code. The LLM is never in the reminder path.
- Prefer structured input (WhatsApp quick-reply buttons) over free text; use the LLM only for what structure can't cover.

## Stack

- Python 3.12, `uv` for dependency management
- FastAPI (webhook API), Pydantic for schemas
- Postgres 16 (local via Docker Compose), plain SQL migrations in `migrations/`
- MCP Python SDK for the events MCP server
- Anthropic SDK (tool use) for the agent
- Kapso for WhatsApp (webhooks in, MCP/API out)
- pytest, ruff, pre-commit, GitHub Actions

## Layout

```
src/events_assistant/
  domain/        # state machine, reminder rules (pure Python, no I/O)
  db/            # repositories, SQL
  mcp_server/    # events MCP server: domain tools
  agent/         # system prompt, tool loop, preference handling
  jobs/          # reminder_job, discovery_job
  api/           # FastAPI webhook for Kapso
  senders/       # WhatsApp sender interface + console fake + Kapso impl
migrations/
tests/
evals/           # reply -> expected tool call cases
docs/            # PRD.md, ARCHITECTURE.md, adr/
```

## Commands

- `docker compose up -d db` start Postgres
- `uv sync` install deps
- `uv run pytest` run tests
- `uv run ruff check . && uv run ruff format --check .` lint and format check
- `uv run python -m events_assistant.jobs.reminder_job` run the reminder job once
- `uv run python -m evals.run` run agent evals

## Working rules

1. Work spec -> plan -> tests -> implement -> review -> commit. Use plan mode before each feature.
2. Write tests first for state machine and reminder logic.
3. One branch per issue (`feat/...`, `fix/...`). Small commits, conventional messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
4. Never give the model raw SQL. The agent and MCP server expose domain tools only.
5. Never put the LLM in the reminder path. Reminder job must be testable with a fixed clock.
6. Inject the clock and the sender; no `datetime.now()` or network calls inside domain code.
7. Proactive WhatsApp messages (reminders, digests) use approved templates; free text only inside the 24h window.
8. Secrets live in `.env` (gitignored). Keep `.env.example` current.
9. Any significant decision gets a short ADR in `docs/adr/`.
10. Update docs in the same PR as the behavior change.

## Status model (summary, full detail in docs/ARCHITECTURE.md)

`suggested -> declined`
`suggested -> going_no_ticket -> going_with_ticket`
`going_* -> cancelled` (changed my mind), and any open status -> `expired` when the event passes.

## Definition of done

Tests pass, lint clean, docs updated, evals still pass, PR reviewed (by me and a review subagent).