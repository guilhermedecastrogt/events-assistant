from datetime import UTC, datetime, timedelta

import pytest

from events_assistant.domain import ReminderKind, ReminderPolicy, ReminderSpec
from events_assistant.domain.reminders import (
    DEFAULT_POLICY,
    next_ticket_nag,
    pre_event_reminders,
    ticket_nag_window_start,
)
from tests.domain.conftest import NOW

TEN_DAYS_AWAY = datetime(2026, 11, 12, 19, 0, tzinfo=UTC)


def test_default_policy():
    assert (
        ReminderPolicy(
            pre_event_lead_days=3,
            pre_event_1d=False,
            ticket_nag_every_days=3,
            default_snooze_days=3,
        )
        == DEFAULT_POLICY
    )


def test_pre_event_reminder_three_days_before():
    assert pre_event_reminders(TEN_DAYS_AWAY, NOW) == [
        ReminderSpec(ReminderKind.PRE_EVENT_3D, datetime(2026, 11, 9, 19, 0, tzinfo=UTC))
    ]


def test_optional_day_before_reminder():
    policy = ReminderPolicy(pre_event_1d=True)
    assert pre_event_reminders(TEN_DAYS_AWAY, NOW, policy) == [
        ReminderSpec(ReminderKind.PRE_EVENT_3D, datetime(2026, 11, 9, 19, 0, tzinfo=UTC)),
        ReminderSpec(ReminderKind.PRE_EVENT_1D, datetime(2026, 11, 11, 19, 0, tzinfo=UTC)),
    ]


def test_no_past_dated_pre_event_reminders():
    two_days_away = NOW + timedelta(days=2)
    assert pre_event_reminders(two_days_away, NOW) == []
    policy = ReminderPolicy(pre_event_1d=True)
    assert pre_event_reminders(two_days_away, NOW, policy) == [
        ReminderSpec(ReminderKind.PRE_EVENT_1D, NOW + timedelta(days=1))
    ]


def test_pre_event_reminder_due_exactly_now_is_skipped():
    assert pre_event_reminders(NOW + timedelta(days=3), NOW) == []


def test_ticket_nag_window_starts_at_the_pre_event_reminder():
    assert ticket_nag_window_start(TEN_DAYS_AWAY) == datetime(2026, 11, 9, 19, 0, tzinfo=UTC)


def nag_at(*args) -> ReminderSpec:
    return ReminderSpec(ReminderKind.TICKET_NAG, datetime(*args, tzinfo=UTC))


def test_ticket_nag_chain_every_three_days_until_the_window():
    first = next_ticket_nag(after=NOW, starts_at=TEN_DAYS_AWAY, now=NOW)
    assert first == nag_at(2026, 11, 5, 10)
    second = next_ticket_nag(after=first.due_at, starts_at=TEN_DAYS_AWAY, now=first.due_at)
    assert second == nag_at(2026, 11, 8, 10)
    # 11 Nov 10:00 is inside the pre-event window (from 9 Nov 19:00): the chain stops.
    assert next_ticket_nag(after=second.due_at, starts_at=TEN_DAYS_AWAY, now=second.due_at) is None


def test_ticket_nag_exactly_at_window_start_is_skipped():
    starts_at = NOW + timedelta(days=6)  # window starts NOW + 3 days, same as the nag
    assert next_ticket_nag(after=NOW, starts_at=starts_at, now=NOW) is None


def test_late_job_continues_the_chain_on_the_original_schedule():
    # Nag was due 5 Nov 10:00 but the job only ran on 6 Nov 11:00.
    event = datetime(2026, 11, 30, 19, 0, tzinfo=UTC)
    late = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)
    nag = next_ticket_nag(after=datetime(2026, 11, 5, 10, tzinfo=UTC), starts_at=event, now=late)
    assert nag == nag_at(2026, 11, 8, 10)


def test_job_late_by_several_intervals_skips_missed_nags():
    event = datetime(2026, 11, 30, 19, 0, tzinfo=UTC)
    very_late = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)  # missed 8 Nov and 11 Nov
    nag = next_ticket_nag(
        after=datetime(2026, 11, 5, 10, tzinfo=UTC), starts_at=event, now=very_late
    )
    assert nag == nag_at(2026, 11, 14, 10)


def test_late_job_inside_the_window_stops_the_chain():
    nag = next_ticket_nag(
        after=datetime(2026, 11, 5, 10, tzinfo=UTC),
        starts_at=TEN_DAYS_AWAY,
        now=datetime(2026, 11, 10, 0, 0, tzinfo=UTC),  # already in the pre-event window
    )
    assert nag is None


def test_nag_chain_resumes_after_a_snooze_resurface():
    resurface_due = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    event = datetime(2026, 12, 10, 19, 0, tzinfo=UTC)
    nag = next_ticket_nag(after=resurface_due, starts_at=event, now=resurface_due)
    assert nag == nag_at(2026, 11, 23, 10)


def test_custom_nag_interval():
    policy = ReminderPolicy(ticket_nag_every_days=2)
    nag = next_ticket_nag(after=NOW, starts_at=TEN_DAYS_AWAY, now=NOW, policy=policy)
    assert nag == ReminderSpec(ReminderKind.TICKET_NAG, NOW + timedelta(days=2))


def test_custom_lead_days_move_the_nag_window():
    policy = ReminderPolicy(pre_event_lead_days=5)
    assert ticket_nag_window_start(TEN_DAYS_AWAY, policy) == datetime(2026, 11, 7, 19, tzinfo=UTC)
    second = next_ticket_nag(
        after=datetime(2026, 11, 5, 10, tzinfo=UTC), starts_at=TEN_DAYS_AWAY, now=NOW, policy=policy
    )
    assert second is None  # 8 Nov is after the 7 Nov window start


def test_next_ticket_nag_arguments_are_keyword_only():
    with pytest.raises(TypeError):
        next_ticket_nag(NOW, TEN_DAYS_AWAY, NOW)  # type: ignore[misc]


def test_spring_dst_pre_event_keeps_local_time():
    # Mon 30 Mar 2026 19:00 IST (18:00 UTC) -> Fri 27 Mar 19:00 GMT (19:00 UTC).
    starts_at = datetime(2026, 3, 30, 18, 0, tzinfo=UTC)
    now = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    assert pre_event_reminders(starts_at, now) == [
        ReminderSpec(ReminderKind.PRE_EVENT_3D, datetime(2026, 3, 27, 19, 0, tzinfo=UTC))
    ]


def test_autumn_dst_pre_event_keeps_local_time():
    # Tue 27 Oct 2026 19:00 GMT -> Sat 24 Oct 19:00 IST (18:00 UTC).
    starts_at = datetime(2026, 10, 27, 19, 0, tzinfo=UTC)
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert pre_event_reminders(starts_at, now) == [
        ReminderSpec(ReminderKind.PRE_EVENT_3D, datetime(2026, 10, 24, 18, 0, tzinfo=UTC))
    ]


def test_spring_dst_ticket_nag_keeps_local_time():
    # Thu 26 Mar 10:00 GMT -> Sun 29 Mar 10:00 IST (09:00 UTC).
    now = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
    starts_at = datetime(2026, 4, 20, 18, 0, tzinfo=UTC)
    nag = next_ticket_nag(after=now, starts_at=starts_at, now=now)
    assert nag == nag_at(2026, 3, 29, 9, 0)


@pytest.mark.parametrize("func", ["pre_event_reminders", "next_ticket_nag"])
def test_naive_datetimes_rejected(func):
    naive = datetime(2026, 11, 12, 19, 0)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        if func == "pre_event_reminders":
            pre_event_reminders(naive, NOW)
        else:
            next_ticket_nag(after=NOW, starts_at=naive, now=NOW)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pre_event_lead_days": 0},
        {"ticket_nag_every_days": 0},
        {"default_snooze_days": -1},
    ],
)
def test_policy_rejects_non_positive_days(kwargs):
    with pytest.raises(ValueError, match="positive"):
        ReminderPolicy(**kwargs)


def test_policy_rejects_day_before_reminder_overlapping_the_lead_reminder():
    with pytest.raises(ValueError, match="pre_event_lead_days"):
        ReminderPolicy(pre_event_lead_days=1, pre_event_1d=True)
