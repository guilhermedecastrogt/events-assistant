# 0005: Discovery via a Claude scheduled task

- Status: Accepted
- Date: 2026-10-12

## Context

The original plan was an in-repo daily `discovery_job` that searches Meetup, Luma, Eventbrite and
organizer sites, normalises results and sends a digest. That means maintaining scrapers or API
clients per source (which break or block), paying for search and LLM calls from our own runtime,
and putting an LLM in a job that also sends WhatsApp messages.

Claude scheduled tasks (configured in the Claude app) can already search the web on a schedule
and call remote MCP servers. Weekly discovery is enough at personal volume.

## Decision

Discovery runs as a **weekly Claude scheduled task** (Sunday evening, Europe/Dublin), configured in
the Claude app. Its prompt is versioned in `prompts/discovery.md`. The task:

1. calls `get_search_preferences()` and `list_known_event_urls()` on our ingest MCP,
2. searches the web,
3. calls `submit_events()` in batches of ≤ 50, and at least once per run (an empty list is fine).

The app owns every decision after submission: validation, dedupe, preference filtering and saving
as `suggested`. A separate deterministic **digest job** sends the events to WhatsApp (header + one
message per event, capped). The task never sends messages.

A **heartbeat** (Monday 12:00 Europe/Dublin) alerts me through an approved template if no ingest
run arrived in the previous 7 days.

## Consequences

- No scraper code to maintain in this repo, and search cost moves to the Claude app.
- The searching LLM is fully separated from messaging: its only effect is rows the app accepted.
- The schedule lives outside the repo, so it can be disabled or fail silently. The heartbeat and
  the `ingest_runs` log cover that, and the prompt is versioned here so it can be reviewed in PRs.
- The ingest endpoint must be public, which adds auth, validation and rate limits
  (see [0006](0006-public-mcp-exposes-ingest-only.md)).
- Discovery quality depends on the prompt and on web search, so it isn't unit-testable. The app
  side (validation, dedupe, filtering) is fully testable.
- Event pages are untrusted input to an LLM, so prompt injection is a real risk. The task can
  only submit data, and the app treats all submitted text as data.
