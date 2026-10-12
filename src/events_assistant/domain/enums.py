"""Domain enums. Values match the CHECK constraints in migrations/001_init.sql."""

from enum import StrEnum


class Status(StrEnum):
    """`my_events.status`."""

    SUGGESTED = "suggested"
    GOING_NO_TICKET = "going_no_ticket"
    GOING_WITH_TICKET = "going_with_ticket"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_open(self) -> bool:
        """Still undecided or going: can change and can expire when the event starts."""
        return self in OPEN_STATUSES

    @property
    def is_terminal(self) -> bool:
        return not self.is_open


OPEN_STATUSES = frozenset({Status.SUGGESTED, Status.GOING_NO_TICKET, Status.GOING_WITH_TICKET})


class Action(StrEnum):
    """What I (or the system, for `expire`) do to an event."""

    DECLINE = "decline"
    GOING_NO_TICKET = "going_no_ticket"
    GOING_TICKET = "going_ticket"
    GOT_TICKET = "got_ticket"
    CANCEL = "cancel"
    SNOOZE = "snooze"
    EXPIRE = "expire"


class ReminderKind(StrEnum):
    """`reminders.kind`."""

    PRE_EVENT_3D = "pre_event_3d"
    PRE_EVENT_1D = "pre_event_1d"
    TICKET_NAG = "ticket_nag"
    SNOOZE_RESURFACE = "snooze_resurface"
