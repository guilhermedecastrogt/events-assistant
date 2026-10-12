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


def test_ticket_nag_chain_every_three_days_until_the_window():
    first = next_ticket_nag(NOW, TEN_DAYS_AWAY, NOW)
    assert first == ReminderSpec(ReminderKind.TICKET_NAG, datetime(2026, 11, 5, 10, tzinfo=UTC))
    second = next_ticket_nag(first.due_at, TEN_DAYS_AWAY, first.due_at)
    assert second == ReminderSpec(ReminderKind.TICKET_NAG, datetime(2026, 11, 8, 10, tzinfo=UTC))
    # 11 Nov 10:00 is inside the pre-event window (from 9 Nov 19:00): the chain stops.
    assert next_ticket_nag(second.due_at, TEN_DAYS_AWAY, second.due_at) is None


def test_ticket_nag_exactly_at_window_start_is_skipped():
    starts_at = NOW + timedelta(days=6)  # window starts NOW + 3 days, same as the nag
    assert next_ticket_nag(NOW, starts_at, NOW) is None


def test_ticket_nag_not_due_in_the_past():
    long_ago = NOW - timedelta(days=5)
    assert next_ticket_nag(long_ago, TEN_DAYS_AWAY, NOW) is None


def test_custom_nag_interval():
    policy = ReminderPolicy(ticket_nag_every_days=2)
    nag = next_ticket_nag(NOW, TEN_DAYS_AWAY, NOW, policy)
    assert nag == ReminderSpec(ReminderKind.TICKET_NAG, NOW + timedelta(days=2))


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
    nag = next_ticket_nag(now, starts_at, now)
    assert nag == ReminderSpec(ReminderKind.TICKET_NAG, datetime(2026, 3, 29, 9, 0, tzinfo=UTC))


@pytest.mark.parametrize("func", ["pre_event_reminders", "next_ticket_nag"])
def test_naive_datetimes_rejected(func):
    naive = datetime(2026, 11, 12, 19, 0)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        if func == "pre_event_reminders":
            pre_event_reminders(naive, NOW)
        else:
            next_ticket_nag(NOW, naive, NOW)


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
