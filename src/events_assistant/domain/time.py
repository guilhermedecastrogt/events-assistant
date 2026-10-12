"""Time helpers. The domain works on UTC instants; "N days" means Dublin calendar days.

No clock lives here: callers always pass `now` in.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Dublin")


def require_aware(dt: datetime, name: str) -> datetime:
    """Return `dt` as UTC, rejecting naive datetimes (their instant is ambiguous)."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {dt.isoformat()}")
    return dt.astimezone(UTC)


def to_local(dt: datetime) -> datetime:
    """`dt` in Europe/Dublin, for display."""
    return require_aware(dt, "dt").astimezone(LOCAL_TZ)


def shift_local_days(dt: datetime, days: int) -> datetime:
    """Move `dt` by whole calendar days on the Dublin wall clock; return UTC.

    "3 days before a 19:00 event" stays at 19:00 local even across a DST change, so the
    result can be 71 or 73 hours away. A wall-clock time that falls in the spring-forward
    gap resolves with the pre-transition offset, and one that occurs twice in autumn resolves
    to the first occurrence (PEP 495, fold=0).
    """
    local = require_aware(dt, "dt").astimezone(LOCAL_TZ)
    return (local + timedelta(days=days)).astimezone(UTC)
