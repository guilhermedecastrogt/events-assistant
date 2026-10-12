from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from events_assistant.domain import (
    Action,
    EventAlreadyStarted,
    InvalidTransition,
    ReminderKind,
    ReminderPolicy,
    ReminderSpec,
    Status,
    TransitionResult,
    transition,
)
from events_assistant.domain.reminders import ticket_nag_window_start
from tests.domain.conftest import NOW

S, A, K = Status, Action, ReminderKind
ALL_KINDS = frozenset(ReminderKind)

TEN_DAYS_AWAY = datetime(2026, 11, 12, 19, 0, tzinfo=UTC)
PRE_EVENT_3D = ReminderSpec(K.PRE_EVENT_3D, datetime(2026, 11, 9, 19, 0, tzinfo=UTC))
FIRST_NAG = ReminderSpec(K.TICKET_NAG, datetime(2026, 11, 5, 10, 0, tzinfo=UTC))
SNOOZE_3D = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)

# Transitions that change something, for an event in the future.
CHANGING = {
    (S.SUGGESTED, A.DECLINE): S.DECLINED,
    (S.SUGGESTED, A.GOING_NO_TICKET): S.GOING_NO_TICKET,
    (S.SUGGESTED, A.GOING_TICKET): S.GOING_WITH_TICKET,
    (S.SUGGESTED, A.SNOOZE): S.SUGGESTED,
    (S.GOING_NO_TICKET, A.GOT_TICKET): S.GOING_WITH_TICKET,
    (S.GOING_NO_TICKET, A.GOING_TICKET): S.GOING_WITH_TICKET,
    (S.GOING_NO_TICKET, A.CANCEL): S.CANCELLED,
    (S.GOING_NO_TICKET, A.SNOOZE): S.GOING_NO_TICKET,
    (S.GOING_WITH_TICKET, A.CANCEL): S.CANCELLED,
}

# Repeating the action that produced the current status: idempotent no-op.
NO_OPS = {
    (S.GOING_NO_TICKET, A.GOING_NO_TICKET),
    (S.GOING_WITH_TICKET, A.GOING_TICKET),
    (S.GOING_WITH_TICKET, A.GOT_TICKET),
    (S.DECLINED, A.DECLINE),
    (S.CANCELLED, A.CANCEL),
    (S.EXPIRED, A.EXPIRE),
}

OPEN = [S.SUGGESTED, S.GOING_NO_TICKET, S.GOING_WITH_TICKET]
TERMINAL = [S.DECLINED, S.CANCELLED, S.EXPIRED]


def go(current, action, starts_at=TEN_DAYS_AWAY, now=NOW, **kwargs) -> TransitionResult:
    return transition(current, action, starts_at, now, **kwargs)


# --- enums -------------------------------------------------------------------------


def test_enum_values_match_database_checks():
    assert [s.value for s in Status] == [
        "suggested",
        "going_no_ticket",
        "going_with_ticket",
        "declined",
        "cancelled",
        "expired",
    ]
    assert [k.value for k in ReminderKind] == [
        "pre_event_3d",
        "pre_event_1d",
        "ticket_nag",
        "snooze_resurface",
    ]
    assert {a.value for a in Action} == {
        "decline",
        "going_no_ticket",
        "going_ticket",
        "got_ticket",
        "cancel",
        "snooze",
        "expire",
    }


def test_open_and_terminal_statuses():
    assert [s for s in Status if s.is_open] == OPEN
    assert [s for s in Status if s.is_terminal] == TERMINAL


# --- each row of the transition table ------------------------------------------------


def test_suggested_decline():
    assert go(S.SUGGESTED, A.DECLINE) == TransitionResult(
        new_status=S.DECLINED,
        reminders_to_create=(),
        reminder_kinds_to_cancel=ALL_KINDS,
        snooze_until=None,
        changed=True,
    )


def test_suggested_going_no_ticket_schedules_pre_event_and_first_nag():
    assert go(S.SUGGESTED, A.GOING_NO_TICKET) == TransitionResult(
        new_status=S.GOING_NO_TICKET,
        reminders_to_create=(PRE_EVENT_3D, FIRST_NAG),
        reminder_kinds_to_cancel=frozenset({K.SNOOZE_RESURFACE}),
        snooze_until=None,
        changed=True,
    )


def test_suggested_going_ticket_schedules_pre_event_only():
    assert go(S.SUGGESTED, A.GOING_TICKET) == TransitionResult(
        new_status=S.GOING_WITH_TICKET,
        reminders_to_create=(PRE_EVENT_3D,),
        reminder_kinds_to_cancel=frozenset({K.TICKET_NAG, K.SNOOZE_RESURFACE}),
        snooze_until=None,
        changed=True,
    )


@pytest.mark.parametrize("action", [A.GOT_TICKET, A.GOING_TICKET])
def test_got_ticket_stops_nagging_and_keeps_pre_event(action):
    result = go(S.GOING_NO_TICKET, action)
    assert result.new_status == S.GOING_WITH_TICKET
    # Same due_at as when going_no_ticket was chosen, so the DB keeps a single row.
    assert result.reminders_to_create == (PRE_EVENT_3D,)
    assert result.reminder_kinds_to_cancel == frozenset({K.TICKET_NAG, K.SNOOZE_RESURFACE})
    assert result.changed


@pytest.mark.parametrize("current", [S.GOING_NO_TICKET, S.GOING_WITH_TICKET])
def test_cancel_cancels_everything(current):
    assert go(current, A.CANCEL) == TransitionResult(
        new_status=S.CANCELLED,
        reminders_to_create=(),
        reminder_kinds_to_cancel=ALL_KINDS,
        snooze_until=None,
        changed=True,
    )


def test_one_day_reminder_when_enabled():
    policy = ReminderPolicy(pre_event_1d=True)
    result = go(S.SUGGESTED, A.GOING_TICKET, policy=policy)
    assert result.reminders_to_create == (
        PRE_EVENT_3D,
        ReminderSpec(K.PRE_EVENT_1D, datetime(2026, 11, 11, 19, 0, tzinfo=UTC)),
    )


# --- snooze --------------------------------------------------------------------------


def test_snooze_suggested_resurfaces_after_default_days():
    assert go(S.SUGGESTED, A.SNOOZE) == TransitionResult(
        new_status=S.SUGGESTED,
        reminders_to_create=(ReminderSpec(K.SNOOZE_RESURFACE, SNOOZE_3D),),
        reminder_kinds_to_cancel=frozenset({K.SNOOZE_RESURFACE}),  # replaces a previous snooze
        snooze_until=SNOOZE_3D,
        changed=True,
    )


def test_snooze_custom_days():
    result = go(S.SUGGESTED, A.SNOOZE, snooze_days=5)
    expected = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    assert result.snooze_until == expected
    assert result.reminders_to_create == (ReminderSpec(K.SNOOZE_RESURFACE, expected),)


def test_snooze_going_no_ticket_replaces_the_pending_nag():
    result = go(S.GOING_NO_TICKET, A.SNOOZE)
    assert result.new_status == S.GOING_NO_TICKET
    assert result.reminders_to_create == (ReminderSpec(K.SNOOZE_RESURFACE, SNOOZE_3D),)
    assert result.reminder_kinds_to_cancel == frozenset({K.TICKET_NAG, K.SNOOZE_RESURFACE})
    assert result.snooze_until == SNOOZE_3D


@pytest.mark.parametrize("current", [S.DECLINED, S.CANCELLED, S.EXPIRED, S.GOING_WITH_TICKET])
def test_snooze_not_allowed(current):
    with pytest.raises(InvalidTransition) as exc_info:
        go(current, A.SNOOZE)
    assert "snooze" in str(exc_info.value)
    assert current.value in str(exc_info.value)


def test_snooze_on_declined_event_is_invalid():
    with pytest.raises(InvalidTransition) as exc_info:
        go(S.DECLINED, A.SNOOZE)
    assert exc_info.value.current == S.DECLINED
    assert exc_info.value.action == A.SNOOZE
    assert not isinstance(exc_info.value, EventAlreadyStarted)


@pytest.mark.parametrize("lead", [timedelta(days=2), timedelta(days=3)])  # before / exactly at
def test_snooze_must_resurface_before_the_event(lead):
    with pytest.raises(InvalidTransition, match="starts before"):
        go(S.SUGGESTED, A.SNOOZE, starts_at=NOW + lead)


def test_snooze_going_no_ticket_must_resurface_before_the_pre_event_window():
    # Event 4 days away: a 3-day snooze would nudge about tickets after the pre-event reminder.
    starts_at = NOW + timedelta(days=4)
    with pytest.raises(InvalidTransition, match="pre-event reminder"):
        go(S.GOING_NO_TICKET, A.SNOOZE, starts_at=starts_at)
    # A suggested event can still be snoozed to the same time.
    assert go(S.SUGGESTED, A.SNOOZE, starts_at=starts_at).changed


def test_changed_transitions_clear_snooze_until():
    snoozed = go(S.SUGGESTED, A.SNOOZE)
    assert snoozed.snooze_until is not None
    going = go(snoozed.new_status, A.GOING_TICKET)
    assert going.snooze_until is None  # repository writes NULL
    assert K.SNOOZE_RESURFACE in going.reminder_kinds_to_cancel


@pytest.mark.parametrize("days", [0, -1])
def test_snooze_days_must_be_positive(days):
    with pytest.raises(ValueError, match="snooze_days"):
        go(S.SUGGESTED, A.SNOOZE, snooze_days=days)


def test_snooze_days_only_for_snooze():
    with pytest.raises(ValueError, match="snooze_days"):
        go(S.SUGGESTED, A.DECLINE, snooze_days=2)


# --- event timing ------------------------------------------------------------------


def test_event_less_than_three_days_away_creates_no_reminders():
    result = go(S.SUGGESTED, A.GOING_NO_TICKET, starts_at=NOW + timedelta(days=2))
    assert result.new_status == S.GOING_NO_TICKET
    assert result.reminders_to_create == ()


def test_event_five_days_away_gets_pre_event_but_no_nag():
    starts_at = NOW + timedelta(days=5)
    result = go(S.SUGGESTED, A.GOING_NO_TICKET, starts_at=starts_at)
    assert result.reminders_to_create == (
        ReminderSpec(K.PRE_EVENT_3D, starts_at - timedelta(days=3)),
    )


def test_nag_exactly_at_window_start_is_not_created():
    starts_at = NOW + timedelta(days=6)
    result = go(S.SUGGESTED, A.GOING_NO_TICKET, starts_at=starts_at)
    assert [r.kind for r in result.reminders_to_create] == [K.PRE_EVENT_3D]


@pytest.mark.parametrize("current", OPEN)
def test_expire_after_event_starts(current):
    assert go(current, A.EXPIRE, now=TEN_DAYS_AWAY) == TransitionResult(
        new_status=S.EXPIRED,
        reminders_to_create=(),
        reminder_kinds_to_cancel=ALL_KINDS,
        snooze_until=None,
        changed=True,
    )


@pytest.mark.parametrize("current", OPEN)
def test_expire_exactly_at_start_is_allowed(current):
    assert go(current, A.EXPIRE, now=TEN_DAYS_AWAY).new_status == S.EXPIRED


@pytest.mark.parametrize(("current", "action"), sorted(CHANGING))
def test_actions_exactly_at_start_raise_event_already_started(current, action):
    with pytest.raises(EventAlreadyStarted):
        go(current, action, now=TEN_DAYS_AWAY)


@pytest.mark.parametrize("current", OPEN)
def test_expire_before_event_starts_is_invalid(current):
    with pytest.raises(InvalidTransition, match="has not started"):
        go(current, A.EXPIRE)


@pytest.mark.parametrize("current", [S.DECLINED, S.CANCELLED])
def test_expire_terminal_status_is_invalid(current):
    with pytest.raises(InvalidTransition):
        go(current, A.EXPIRE, now=TEN_DAYS_AWAY)


# --- double transitions ------------------------------------------------------------


@pytest.mark.parametrize(("current", "action"), sorted(NO_OPS))
def test_repeating_an_action_is_a_no_op(current, action):
    assert go(current, action) == TransitionResult(
        new_status=current,
        reminders_to_create=(),
        reminder_kinds_to_cancel=frozenset(),
        snooze_until=None,
        changed=False,
    )


@pytest.mark.parametrize(("current", "action"), sorted(NO_OPS))
def test_repeats_stay_no_ops_after_the_event_starts(current, action):
    # A webhook retry processed after the start must never error.
    assert not go(current, action, now=TEN_DAYS_AWAY + timedelta(hours=1)).changed


# --- full matrix -------------------------------------------------------------------


@pytest.mark.parametrize(("current", "action"), list(product(Status, Action)))
def test_transition_matrix_future_event(current, action):
    if (current, action) in CHANGING:
        result = go(current, action)
        assert result.new_status == CHANGING[(current, action)]
        assert result.changed
    elif (current, action) in NO_OPS:
        assert not go(current, action).changed
    else:
        with pytest.raises(InvalidTransition) as exc_info:
            go(current, action)
        message = str(exc_info.value)
        assert current.value in message
        assert action.value in message


@pytest.mark.parametrize(("current", "action"), list(product(Status, Action)))
def test_transition_matrix_started_event(current, action):
    now = TEN_DAYS_AWAY + timedelta(minutes=1)
    if current.is_open and action == A.EXPIRE:
        assert go(current, action, now=now).new_status == S.EXPIRED
    elif (current, action) in NO_OPS:
        assert not go(current, action, now=now).changed
    elif current.is_open:
        # Even actions the table never allows: the useful message is "already started".
        with pytest.raises(EventAlreadyStarted, match="already started"):
            go(current, action, now=now)
    else:
        with pytest.raises(InvalidTransition, match="is final") as exc_info:
            go(current, action, now=now)
        assert type(exc_info.value) is InvalidTransition


def test_invalid_transition_message_is_actionable():
    with pytest.raises(InvalidTransition) as exc_info:
        go(S.GOING_WITH_TICKET, A.DECLINE)
    assert str(exc_info.value) == (
        "cannot decline an event that is going_with_ticket (allowed: cancel)"
    )


# --- inputs and time ----------------------------------------------------------------


@pytest.mark.parametrize("which", ["starts_at", "now"])
def test_naive_datetimes_rejected(which):
    naive = datetime(2026, 11, 2, 10, 0)  # noqa: DTZ001
    kwargs = {"starts_at": naive} if which == "starts_at" else {"now": naive}
    with pytest.raises(ValueError, match=f"{which} must be timezone-aware"):
        go(S.SUGGESTED, A.DECLINE, **kwargs)


def test_accepts_string_values_for_status_and_action():
    result = transition("suggested", "going_ticket", TEN_DAYS_AWAY, NOW)
    assert result.new_status == S.GOING_WITH_TICKET


def test_unknown_status_or_action_raises_value_error():
    with pytest.raises(ValueError):
        transition("maybe", "going_ticket", TEN_DAYS_AWAY, NOW)
    with pytest.raises(ValueError):
        transition("suggested", "maybe", TEN_DAYS_AWAY, NOW)


def test_results_are_utc_even_for_local_inputs():
    from events_assistant.domain.time import LOCAL_TZ

    result = transition(
        S.SUGGESTED,
        A.GOING_NO_TICKET,
        TEN_DAYS_AWAY.astimezone(LOCAL_TZ),
        NOW.astimezone(LOCAL_TZ),
    )
    assert all(r.due_at.tzinfo is UTC for r in result.reminders_to_create)
    assert result.reminders_to_create == (PRE_EVENT_3D, FIRST_NAG)


def test_dst_week_going_no_ticket():
    # Decision Thu 26 Mar 2026 10:00 GMT; event Thu 9 Apr 19:00 IST (18:00 UTC).
    now = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
    starts_at = datetime(2026, 4, 9, 18, 0, tzinfo=UTC)
    result = transition(S.SUGGESTED, A.GOING_NO_TICKET, starts_at, now)
    assert result.reminders_to_create == (
        ReminderSpec(K.PRE_EVENT_3D, datetime(2026, 4, 6, 18, 0, tzinfo=UTC)),  # 19:00 IST
        ReminderSpec(K.TICKET_NAG, datetime(2026, 3, 29, 9, 0, tzinfo=UTC)),  # 10:00 IST
    )


# --- invariants ----------------------------------------------------------------------


def test_created_reminders_are_always_between_now_and_event_start():
    policy = ReminderPolicy(pre_event_1d=True)
    base = datetime(2026, 3, 20, 0, 0, tzinfo=UTC)  # sweep crosses the spring DST change
    checked = 0
    for now_hours in range(0, 24 * 14, 7):
        now = base + timedelta(hours=now_hours)
        for lead_hours in range(1, 24 * 21, 5):
            starts_at = now + timedelta(hours=lead_hours)
            for action in (A.GOING_NO_TICKET, A.GOING_TICKET, A.SNOOZE):
                try:
                    result = transition(S.SUGGESTED, action, starts_at, now, policy=policy)
                except InvalidTransition:
                    assert action == A.SNOOZE
                    continue
                for reminder in result.reminders_to_create:
                    assert now < reminder.due_at < starts_at
                    if reminder.kind == K.TICKET_NAG:
                        assert reminder.due_at < ticket_nag_window_start(starts_at)
                    checked += 1
    assert checked > 1000
