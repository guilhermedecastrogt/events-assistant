"""Reminder rules: when pre-event reminders and ticket nags are due. Pure functions."""

from dataclasses import dataclass, fields
from datetime import datetime

from events_assistant.domain.enums import ReminderKind
from events_assistant.domain.time import require_aware, shift_local_days


@dataclass(frozen=True)
class ReminderSpec:
    """A reminder to create: `kind` due at `due_at` (UTC)."""

    kind: ReminderKind
    due_at: datetime


@dataclass(frozen=True)
class ReminderPolicy:
    pre_event_lead_days: int = 3  # pre_event_3d reminder; also where ticket nags stop
    pre_event_1d: bool = False  # optional reminder the day before
    ticket_nag_every_days: int = 3
    default_snooze_days: int = 3

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.type is int and value <= 0:
                raise ValueError(f"{field.name} must be positive, got {value}")
        if self.pre_event_1d and self.pre_event_lead_days <= 1:
            raise ValueError("pre_event_lead_days must be more than 1 when pre_event_1d is on")


DEFAULT_POLICY = ReminderPolicy()


def pre_event_reminders(
    starts_at: datetime, now: datetime, policy: ReminderPolicy = DEFAULT_POLICY
) -> list[ReminderSpec]:
    """Pre-event reminders still in the future. Never returns past-dated reminders."""
    starts_at = require_aware(starts_at, "starts_at")
    now = require_aware(now, "now")
    candidates = [
        ReminderSpec(
            ReminderKind.PRE_EVENT_3D, shift_local_days(starts_at, -policy.pre_event_lead_days)
        )
    ]
    if policy.pre_event_1d:
        candidates.append(ReminderSpec(ReminderKind.PRE_EVENT_1D, shift_local_days(starts_at, -1)))
    return [r for r in candidates if now < r.due_at < starts_at]


def ticket_nag_window_start(
    starts_at: datetime, policy: ReminderPolicy = DEFAULT_POLICY
) -> datetime:
    """Ticket nags stop here: the pre-event reminder takes over."""
    return shift_local_days(require_aware(starts_at, "starts_at"), -policy.pre_event_lead_days)


def next_ticket_nag(
    *,
    after: datetime,
    starts_at: datetime,
    now: datetime,
    policy: ReminderPolicy = DEFAULT_POLICY,
) -> ReminderSpec | None:
    """The next ticket nag on the schedule that started at `after`, if one is still due.

    `after` is the decision time or the previous nag's (or snooze resurface's) `due_at`, not
    when it was actually sent, so a late job neither stops the chain nor drifts it: missed
    slots are skipped and the next future slot is returned. Returns None once that slot is at
    or after the pre-event window start.
    """
    after = require_aware(after, "after")
    now = require_aware(now, "now")
    window_start = ticket_nag_window_start(starts_at, policy)
    due_at = shift_local_days(after, policy.ticket_nag_every_days)
    while due_at <= now:
        due_at = shift_local_days(due_at, policy.ticket_nag_every_days)
    if due_at < window_start:
        return ReminderSpec(ReminderKind.TICKET_NAG, due_at)
    return None
