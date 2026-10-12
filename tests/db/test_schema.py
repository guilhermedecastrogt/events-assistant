"""Constraint tests for migrations/001_init.sql, run against a real Postgres."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration

DUBLIN = ZoneInfo("Europe/Dublin")
STARTS_AT = datetime(2026, 11, 5, 18, 30, tzinfo=UTC)


def insert_event(db: psycopg.Connection, **overrides) -> int:
    values = {
        "source": "meetup",
        "source_url": "https://www.meetup.com/python-ireland/events/1/",
        "title": "Python Ireland November meetup",
        "starts_at": STARTS_AT,
        **overrides,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({k})s" for k in values)
    row = db.execute(
        f"INSERT INTO events ({columns}) VALUES ({placeholders}) RETURNING id",  # noqa: S608
        values,
    ).fetchone()
    assert row is not None
    return row[0]


def insert_reminder(db: psycopg.Connection, event_id: int, **overrides) -> int:
    values = {"event_id": event_id, "kind": "pre_event_3d", "due_at": STARTS_AT - timedelta(3)}
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({k})s" for k in values)
    row = db.execute(
        f"INSERT INTO reminders ({columns}) VALUES ({placeholders}) RETURNING id",  # noqa: S608
        values,
    ).fetchone()
    assert row is not None
    return row[0]


# --- events ------------------------------------------------------------------


def test_event_defaults(db):
    event_id = insert_event(db)
    raw, created_at, is_free = db.execute(
        "SELECT raw, created_at, is_free FROM events WHERE id = %s", (event_id,)
    ).fetchone()
    assert raw == {}
    assert created_at.tzinfo is not None
    assert is_free is None


def test_duplicate_source_url_is_rejected(db):
    insert_event(db)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        insert_event(db, title="Same URL, different title")


def test_duplicate_source_url_can_be_ignored_with_on_conflict(db):
    insert_event(db)
    cur = db.execute(
        """
        INSERT INTO events (source, source_url, title, starts_at)
        VALUES ('luma', %s, 'dup', %s)
        ON CONFLICT (source_url) DO NOTHING
        """,
        ("https://www.meetup.com/python-ireland/events/1/", STARTS_AT),
    )
    assert cur.rowcount == 0


def test_event_cannot_end_before_it_starts(db):
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_event(db, ends_at=STARTS_AT - timedelta(minutes=1))


def test_event_may_end_when_it_starts(db):
    insert_event(db, ends_at=STARTS_AT)


@pytest.mark.parametrize("column", ["source", "source_url", "title", "starts_at"])
def test_event_required_columns(db, column):
    with pytest.raises(errors.NotNullViolation), db.transaction():
        insert_event(db, **{column: None})


@pytest.mark.parametrize(
    ("local", "expected_utc"),
    [
        # Dublin switches to IST (UTC+1) at 01:00 UTC on 2026-03-29.
        (datetime(2026, 3, 28, 19, 0, tzinfo=DUBLIN), datetime(2026, 3, 28, 19, 0, tzinfo=UTC)),
        (datetime(2026, 3, 29, 19, 0, tzinfo=DUBLIN), datetime(2026, 3, 29, 18, 0, tzinfo=UTC)),
    ],
)
def test_timestamptz_preserves_the_instant(db, local, expected_utc):
    event_id = insert_event(db, starts_at=local)
    (stored,) = db.execute("SELECT starts_at FROM events WHERE id = %s", (event_id,)).fetchone()
    assert stored.astimezone(UTC) == expected_utc


# --- my_events ---------------------------------------------------------------


def test_my_events_defaults(db):
    event_id = insert_event(db)
    db.execute("INSERT INTO my_events (event_id) VALUES (%s)", (event_id,))
    status, digested_at, changed = db.execute(
        "SELECT status, digested_at, status_changed_at FROM my_events WHERE event_id = %s",
        (event_id,),
    ).fetchone()
    assert status == "suggested"
    assert digested_at is None
    assert changed is not None


@pytest.mark.parametrize(
    "status",
    ["suggested", "going_no_ticket", "going_with_ticket", "declined", "cancelled", "expired"],
)
def test_my_events_accepts_every_valid_status(db, status):
    event_id = insert_event(db)
    db.execute("INSERT INTO my_events (event_id, status) VALUES (%s, %s)", (event_id, status))


@pytest.mark.parametrize("status", ["going", "GOING_NO_TICKET", "", "unknown"])
def test_my_events_rejects_invalid_status(db, status):
    event_id = insert_event(db)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute("INSERT INTO my_events (event_id, status) VALUES (%s, %s)", (event_id, status))


def test_my_events_is_one_row_per_event(db):
    event_id = insert_event(db)
    db.execute("INSERT INTO my_events (event_id) VALUES (%s)", (event_id,))
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute("INSERT INTO my_events (event_id) VALUES (%s)", (event_id,))


def test_my_events_requires_existing_event(db):
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        db.execute("INSERT INTO my_events (event_id) VALUES (999999)")


# --- reminders ---------------------------------------------------------------


@pytest.mark.parametrize("kind", ["pre_event_3d", "pre_event_1d", "ticket_nag", "snooze_resurface"])
def test_reminders_accept_every_valid_kind(db, kind):
    insert_reminder(db, insert_event(db), kind=kind)


@pytest.mark.parametrize("kind", ["pre_event", "nag", "PRE_EVENT_3D"])
def test_reminders_reject_invalid_kind(db, kind):
    event_id = insert_event(db)
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_reminder(db, event_id, kind=kind)


def test_duplicate_reminder_is_rejected(db):
    event_id = insert_event(db)
    insert_reminder(db, event_id)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        insert_reminder(db, event_id)


def test_same_kind_at_different_times_is_allowed(db):
    event_id = insert_event(db)
    insert_reminder(db, event_id, kind="ticket_nag", due_at=STARTS_AT - timedelta(days=9))
    insert_reminder(db, event_id, kind="ticket_nag", due_at=STARTS_AT - timedelta(days=6))


def test_reminder_cannot_be_both_sent_and_canceled(db):
    event_id = insert_event(db)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_reminder(db, event_id, sent_at=now, canceled_at=now)


def test_reminder_requires_existing_event(db):
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        insert_reminder(db, 999999)


# --- messages ----------------------------------------------------------------


def insert_message(db: psycopg.Connection, **overrides) -> None:
    values = {"direction": "inbound", "wa_message_id": "wamid.1", "event_id": None, **overrides}
    db.execute(
        "INSERT INTO messages (direction, wa_message_id, event_id)"
        " VALUES (%(direction)s, %(wa_message_id)s, %(event_id)s)",
        values,
    )


def test_duplicate_wa_message_id_is_rejected(db):
    insert_message(db)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        insert_message(db, direction="outbound")


@pytest.mark.parametrize("direction", ["in", "out", "INBOUND", ""])
def test_messages_reject_invalid_direction(db, direction):
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_message(db, direction=direction)


def test_messages_require_wa_message_id(db):
    with pytest.raises(errors.NotNullViolation), db.transaction():
        insert_message(db, wa_message_id=None)


# --- preferences -------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", ["dislike_topic", "like_topic", "dislike_organizer", "max_price", "max_distance_km"]
)
def test_preferences_accept_every_valid_kind(db, kind):
    db.execute("INSERT INTO preferences (kind, value) VALUES (%s, 'x')", (kind,))


@pytest.mark.parametrize("kind", ["like_organizer", "topic", ""])
def test_preferences_reject_invalid_kind(db, kind):
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute("INSERT INTO preferences (kind, value) VALUES (%s, 'x')", (kind,))


def test_preference_weight_defaults_to_one(db):
    (weight,) = db.execute(
        "INSERT INTO preferences (kind, value) VALUES ('like_topic', 'python') RETURNING weight"
    ).fetchone()
    assert weight == 1.0


# --- cascades ----------------------------------------------------------------


def test_deleting_event_cascades_and_detaches_messages(db):
    event_id = insert_event(db)
    db.execute("INSERT INTO my_events (event_id) VALUES (%s)", (event_id,))
    insert_reminder(db, event_id)
    insert_message(db, event_id=event_id)

    db.execute("DELETE FROM events WHERE id = %s", (event_id,))

    assert db.execute("SELECT count(*) FROM my_events").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM reminders").fetchone() == (0,)
    assert db.execute("SELECT event_id FROM messages").fetchall() == [(None,)]


# --- ingest_runs ---------------------------------------------------------------


def insert_run(db: psycopg.Connection, **overrides) -> int:
    values = {
        "submitted": 0,
        "accepted": 0,
        "duplicates": 0,
        "filtered": 0,
        "rejected": 0,
        "rejections": Jsonb([]),
        **overrides,
    }
    row = db.execute(
        """
        INSERT INTO ingest_runs (submitted, accepted, duplicates, filtered, rejected, rejections)
        VALUES (%(submitted)s, %(accepted)s, %(duplicates)s, %(filtered)s, %(rejected)s,
                %(rejections)s)
        RETURNING id
        """,
        values,
    ).fetchone()
    assert row is not None
    return row[0]


def test_empty_ingest_run_is_valid(db):
    (received_at, rejections) = db.execute(
        "INSERT INTO ingest_runs DEFAULT VALUES RETURNING received_at, rejections"
    ).fetchone()
    assert received_at is not None
    assert rejections == []


def test_ingest_run_round_trips_rejections(db):
    rejections = [{"source_url": "https://example.com/e/1", "reason": "starts_at is naive"}]
    run_id = insert_run(
        db,
        submitted=4,
        accepted=1,
        duplicates=1,
        filtered=1,
        rejected=1,
        rejections=Jsonb(rejections),
    )
    (stored,) = db.execute("SELECT rejections FROM ingest_runs WHERE id = %s", (run_id,)).fetchone()
    assert stored == rejections


@pytest.mark.parametrize(
    "counts",
    [
        {"submitted": 3, "accepted": 1},  # under-counted
        {"submitted": 1, "accepted": 1, "rejected": 1},  # over-counted
    ],
)
def test_ingest_run_counts_must_add_up(db, counts):
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_run(db, **counts)


@pytest.mark.parametrize("column", ["submitted", "accepted", "duplicates", "filtered", "rejected"])
def test_ingest_run_counts_cannot_be_negative(db, column):
    counts = {column: -1}
    if column != "submitted":
        counts["submitted"] = -1  # keep the sum CHECK satisfied so only >= 0 can fail
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_run(db, **counts)


@pytest.mark.parametrize("value", [{}, "oops", 1])
def test_ingest_run_rejections_must_be_an_array(db, value):
    with pytest.raises(errors.CheckViolation), db.transaction():
        insert_run(db, rejections=Jsonb(value))
