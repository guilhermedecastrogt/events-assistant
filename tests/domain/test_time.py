from datetime import UTC, datetime, timedelta, timezone

import pytest

from events_assistant.domain.time import LOCAL_TZ, require_aware, shift_local_days, to_local


def test_local_timezone_is_dublin():
    assert str(LOCAL_TZ) == "Europe/Dublin"


def test_require_aware_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="starts_at must be timezone-aware"):
        require_aware(datetime(2026, 11, 2, 10, 0), "starts_at")  # noqa: DTZ001


def test_require_aware_returns_utc():
    plus_two = timezone(timedelta(hours=2))
    result = require_aware(datetime(2026, 11, 2, 12, 0, tzinfo=plus_two), "now")
    assert result == datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    ("utc", "local_hour", "tzname"),
    [
        (datetime(2026, 1, 15, 12, 0, tzinfo=UTC), 12, "GMT"),
        (datetime(2026, 7, 15, 12, 0, tzinfo=UTC), 13, "IST"),
    ],
)
def test_to_local_displays_dublin_time(utc, local_hour, tzname):
    local = to_local(utc)
    assert local.hour == local_hour
    assert local.tzname() == tzname


def test_to_local_rejects_naive():
    with pytest.raises(ValueError, match="must be timezone-aware"):
        to_local(datetime(2026, 1, 15, 12, 0))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        # Plain winter shift: 72 hours.
        (datetime(2026, 11, 2, 10, 0, tzinfo=UTC), 3, datetime(2026, 11, 5, 10, 0, tzinfo=UTC)),
        # Across spring-forward (29 Mar 2026): 10:00 GMT -> 10:00 IST = 09:00 UTC (71h).
        (datetime(2026, 3, 26, 10, 0, tzinfo=UTC), 3, datetime(2026, 3, 29, 9, 0, tzinfo=UTC)),
        # Backwards across spring-forward: Mon 19:00 IST -> Fri 19:00 GMT.
        (datetime(2026, 3, 30, 18, 0, tzinfo=UTC), -3, datetime(2026, 3, 27, 19, 0, tzinfo=UTC)),
        # Backwards across fall-back (25 Oct 2026): Tue 19:00 GMT -> Sat 19:00 IST = 18:00 UTC.
        (datetime(2026, 10, 27, 19, 0, tzinfo=UTC), -3, datetime(2026, 10, 24, 18, 0, tzinfo=UTC)),
        # Landing in the spring gap (01:30 local doesn't exist): resolved, not an error.
        (datetime(2026, 3, 26, 1, 30, tzinfo=UTC), 3, datetime(2026, 3, 29, 1, 30, tzinfo=UTC)),
        # Landing on the repeated autumn hour (01:30 happens twice on 25 Oct): the first one
        # (IST, 00:30 UTC) is used.
        (datetime(2026, 10, 28, 1, 30, tzinfo=UTC), -3, datetime(2026, 10, 25, 0, 30, tzinfo=UTC)),
    ],
)
def test_shift_local_days_keeps_dublin_wall_clock(start, days, expected):
    result = shift_local_days(start, days)
    assert result == expected
    assert result.tzinfo is UTC
