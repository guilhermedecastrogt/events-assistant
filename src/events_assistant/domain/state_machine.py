"""The `my_events.status` state machine. Pure: no I/O, the clock is passed in.

`transition()` decides the new status and which reminders to create or cancel; repositories
apply the result in one transaction. See docs/ARCHITECTURE.md "State machine".
"""

from dataclasses import dataclass
from datetime import datetime

from events_assistant.domain.enums import Action, ReminderKind, Status
from events_assistant.domain.reminders import (
    DEFAULT_POLICY,
    ReminderPolicy,
    ReminderSpec,
    next_ticket_nag,
    pre_event_reminders,
)
from events_assistant.domain.time import require_aware, shift_local_days, to_local

ALL_KINDS = frozenset(ReminderKind)

# (current, action) -> new status, for an event that hasn't started. `expire` is handled apart.
TRANSITIONS: dict[tuple[Status, Action], Status] = {
    (Status.SUGGESTED, Action.DECLINE): Status.DECLINED,
    (Status.SUGGESTED, Action.GOING_NO_TICKET): Status.GOING_NO_TICKET,
    (Status.SUGGESTED, Action.GOING_TICKET): Status.GOING_WITH_TICKET,
    (Status.SUGGESTED, Action.SNOOZE): Status.SUGGESTED,
    (Status.GOING_NO_TICKET, Action.GOT_TICKET): Status.GOING_WITH_TICKET,
    (Status.GOING_NO_TICKET, Action.GOING_TICKET): Status.GOING_WITH_TICKET,
    (Status.GOING_NO_TICKET, Action.CANCEL): Status.CANCELLED,
    (Status.GOING_NO_TICKET, Action.SNOOZE): Status.GOING_NO_TICKET,
    (Status.GOING_WITH_TICKET, Action.CANCEL): Status.CANCELLED,
}

# Repeating the action that led to the current status changes nothing (double tap, retry).
NO_OPS = frozenset(
    {
        (Status.GOING_NO_TICKET, Action.GOING_NO_TICKET),
        (Status.GOING_WITH_TICKET, Action.GOING_TICKET),
        (Status.GOING_WITH_TICKET, Action.GOT_TICKET),
        (Status.DECLINED, Action.DECLINE),
        (Status.CANCELLED, Action.CANCEL),
        (Status.EXPIRED, Action.EXPIRE),
    }
)


class InvalidTransition(Exception):  # noqa: N818  (domain name, not an *Error)
    """`action` is not allowed from `current`."""

    def __init__(self, current: Status, action: Action, reason: str | None = None) -> None:
        self.current = current
        self.action = action
        if reason is None:
            allowed = sorted(a.value for (s, a) in TRANSITIONS if s == current)
            reason = f"allowed: {', '.join(allowed)}" if allowed else f"{current.value} is final"
        super().__init__(f"cannot {action.value} an event that is {current.value} ({reason})")


class EventAlreadyStarted(InvalidTransition):
    """The event has started; only `expire` is possible now."""

    def __init__(self, current: Status, action: Action) -> None:
        super().__init__(current, action, "the event already started")


@dataclass(frozen=True)
class TransitionResult:
    new_status: Status
    reminders_to_create: tuple[ReminderSpec, ...]
    reminder_kinds_to_cancel: frozenset[ReminderKind]  # cancel *pending* reminders of these kinds
    snooze_until: datetime | None  # new value of my_events.snooze_until when changed
    changed: bool  # False: nothing to write (idempotent repeat)


def transition(
    current: Status | str,
    action: Action | str,
    event_starts_at: datetime,
    now: datetime,
    *,
    snooze_days: int | None = None,
    policy: ReminderPolicy = DEFAULT_POLICY,
) -> TransitionResult:
    """Apply `action` to an event in status `current`.

    Raises `InvalidTransition` (or `EventAlreadyStarted`) for disallowed actions and
    `ValueError` for bad inputs (unknown status/action, naive datetimes, bad snooze_days).
    """
    current = Status(current)
    action = Action(action)
    starts_at = require_aware(event_starts_at, "starts_at")
    now = require_aware(now, "now")
    if snooze_days is not None:
        if action != Action.SNOOZE:
            raise ValueError("snooze_days is only valid with the snooze action")
        if snooze_days <= 0:
            raise ValueError(f"snooze_days must be positive, got {snooze_days}")

    if (current, action) in NO_OPS:
        return TransitionResult(current, (), frozenset(), None, changed=False)

    if action == Action.EXPIRE:
        if not current.is_open:
            raise InvalidTransition(current, action)
        if now < starts_at:
            raise InvalidTransition(current, action, "the event has not started yet")
        return _result(Status.EXPIRED, cancel=ALL_KINDS)

    new_status = TRANSITIONS.get((current, action))
    if new_status is None:
        raise InvalidTransition(current, action)
    if now >= starts_at:
        raise EventAlreadyStarted(current, action)

    if action == Action.SNOOZE:
        days = snooze_days if snooze_days is not None else policy.default_snooze_days
        until = shift_local_days(now, days)
        if until >= starts_at:
            when = f"{to_local(until):%a %d %b %H:%M}"
            raise InvalidTransition(
                current, action, f"the event starts before then; can't snooze until {when}"
            )
        cancel = {ReminderKind.SNOOZE_RESURFACE}
        if current == Status.GOING_NO_TICKET:
            cancel.add(ReminderKind.TICKET_NAG)  # the resurface is the next nudge
        return _result(
            new_status,
            create=[ReminderSpec(ReminderKind.SNOOZE_RESURFACE, until)],
            cancel=frozenset(cancel),
            snooze_until=until,
        )

    if new_status in (Status.DECLINED, Status.CANCELLED):
        return _result(new_status, cancel=ALL_KINDS)

    reminders = pre_event_reminders(starts_at, now, policy)
    if new_status == Status.GOING_NO_TICKET:
        nag = next_ticket_nag(now, starts_at, now, policy)
        return _result(
            new_status,
            create=[*reminders, *([nag] if nag else [])],
            cancel=frozenset({ReminderKind.SNOOZE_RESURFACE}),
        )

    # going_with_ticket
    return _result(
        new_status,
        create=reminders,
        cancel=frozenset({ReminderKind.TICKET_NAG, ReminderKind.SNOOZE_RESURFACE}),
    )


def _result(
    new_status: Status,
    *,
    create: list[ReminderSpec] | None = None,
    cancel: frozenset[ReminderKind] = frozenset(),
    snooze_until: datetime | None = None,
) -> TransitionResult:
    return TransitionResult(new_status, tuple(create or ()), cancel, snooze_until, changed=True)
