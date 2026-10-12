from datetime import UTC, datetime

import pytest

# Fixed clock for domain tests: Monday 2 Nov 2026, 10:00 UTC (10:00 GMT in Dublin).
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW
