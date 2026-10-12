-- 001_init: initial schema. See docs/ARCHITECTURE.md "Data model".
-- All timestamps are timestamptz (stored as UTC instants). Enums are text + CHECK.

CREATE TABLE events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text        NOT NULL,
    source_url  text        NOT NULL,
    title       text        NOT NULL,
    description text,
    organizer   text,
    venue       text,
    city        text,
    starts_at   timestamptz NOT NULL,
    ends_at     timestamptz,
    price_text  text,
    is_free     boolean,                      -- NULL = unknown
    raw         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT events_source_url_key UNIQUE (source_url),
    CONSTRAINT events_ends_after_start CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

CREATE INDEX events_starts_at_idx ON events (starts_at);

CREATE TABLE my_events (
    event_id          bigint      PRIMARY KEY REFERENCES events (id) ON DELETE CASCADE,
    status            text        NOT NULL DEFAULT 'suggested',
    status_changed_at timestamptz NOT NULL DEFAULT now(),
    snooze_until      timestamptz,
    digested_at       timestamptz,             -- NULL = not yet sent in a digest
    ticket_url        text,
    notes             text,
    CONSTRAINT my_events_status_check CHECK (status IN (
        'suggested', 'going_no_ticket', 'going_with_ticket', 'declined', 'cancelled', 'expired'
    ))
);

CREATE INDEX my_events_status_idx ON my_events (status);
CREATE INDEX my_events_digest_pending_idx ON my_events (event_id)
    WHERE status = 'suggested' AND digested_at IS NULL;

CREATE TABLE reminders (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id    bigint      NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    kind        text        NOT NULL,
    due_at      timestamptz NOT NULL,
    sent_at     timestamptz,
    canceled_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reminders_event_kind_due_key UNIQUE (event_id, kind, due_at),
    CONSTRAINT reminders_kind_check CHECK (kind IN (
        'pre_event_3d', 'pre_event_1d', 'ticket_nag', 'snooze_resurface'
    )),
    CONSTRAINT reminders_sent_or_canceled CHECK (sent_at IS NULL OR canceled_at IS NULL)
);

-- Reminder job: due and still pending.
CREATE INDEX reminders_pending_due_idx ON reminders (due_at)
    WHERE sent_at IS NULL AND canceled_at IS NULL;
-- Cancel pending reminders for one event.
CREATE INDEX reminders_pending_event_idx ON reminders (event_id)
    WHERE sent_at IS NULL AND canceled_at IS NULL;

CREATE TABLE messages (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    direction            text        NOT NULL,
    wa_message_id        text        NOT NULL,
    event_id             bigint      REFERENCES events (id) ON DELETE SET NULL,
    quoted_wa_message_id text,
    body                 text,
    template_name        text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT messages_wa_message_id_key UNIQUE (wa_message_id),
    CONSTRAINT messages_direction_check CHECK (direction IN ('inbound', 'outbound'))
);

-- "Latest message about an event" fallback when resolving a reply.
CREATE INDEX messages_event_created_idx ON messages (event_id, created_at DESC);

CREATE TABLE preferences (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind       text        NOT NULL,
    value      text        NOT NULL,
    weight     real        NOT NULL DEFAULT 1.0,
    reason     text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT preferences_kind_check CHECK (kind IN (
        'dislike_topic', 'like_topic', 'dislike_organizer', 'max_price', 'max_distance_km'
    ))
);

-- One row per submit_events call from the scheduled task (append-only).
CREATE TABLE ingest_runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT now(),
    submitted   integer     NOT NULL DEFAULT 0 CHECK (submitted >= 0),
    accepted    integer     NOT NULL DEFAULT 0 CHECK (accepted >= 0),
    duplicates  integer     NOT NULL DEFAULT 0 CHECK (duplicates >= 0),
    filtered    integer     NOT NULL DEFAULT 0 CHECK (filtered >= 0),
    rejected    integer     NOT NULL DEFAULT 0 CHECK (rejected >= 0),
    rejections  jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- [{source_url, reason}, ...]
    CONSTRAINT ingest_runs_counts_add_up
        CHECK (accepted + duplicates + filtered + rejected = submitted),
    CONSTRAINT ingest_runs_rejections_is_array CHECK (jsonb_typeof(rejections) = 'array')
);

-- Heartbeat: latest run.
CREATE INDEX ingest_runs_received_at_idx ON ingest_runs (received_at DESC);
