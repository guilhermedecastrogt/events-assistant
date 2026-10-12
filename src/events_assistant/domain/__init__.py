"""State machine and reminder rules. Pure Python, no I/O."""

from events_assistant.domain.enums import Action, ReminderKind, Status
from events_assistant.domain.reminders import ReminderPolicy, ReminderSpec
from events_assistant.domain.state_machine import (
    EventAlreadyStarted,
    InvalidTransition,
    TransitionResult,
    transition,
)

__all__ = [
    "Action",
    "EventAlreadyStarted",
    "InvalidTransition",
    "ReminderKind",
    "ReminderPolicy",
    "ReminderSpec",
    "Status",
    "TransitionResult",
    "transition",
]
