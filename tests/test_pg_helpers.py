"""Tests for the throwaway-database helpers used by the integration fixtures."""

import time

import pytest

from tests import pg


def test_no_database_skips_by_default(monkeypatch):
    monkeypatch.delenv("REQUIRE_DB", raising=False)
    with pytest.raises(pytest.skip.Exception, match="no db"):
        pg.no_database("no db")


def test_no_database_fails_when_required(monkeypatch):
    monkeypatch.setenv("REQUIRE_DB", "1")
    with pytest.raises(pytest.fail.Exception, match="REQUIRE_DB=1 but no db"):
        pg.no_database("no db")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://events:events@localhost:5432/events",
        "postgresql://events@127.0.0.1/events",
        "postgresql://events@[::1]/events",
        "postgresql:///events",
        "host=/var/run/postgresql dbname=events",
    ],
)
def test_local_urls_are_allowed(url):
    assert pg.is_local(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@ep-cool-123.eu-west-1.aws.neon.tech/neondb",
        "postgresql://u@db.example.com/events",
        "postgresql://u@localhost,db.example.com/events",
    ],
)
def test_remote_urls_are_refused(url):
    assert not pg.is_local(url)


def test_test_database_names_carry_a_timestamp():
    name = pg.new_test_database_name()
    assert name.startswith(pg.TEST_DB_PREFIX)
    created = pg.created_at_from_name(name)
    assert created is not None
    assert abs(created - time.time()) < 5


@pytest.mark.parametrize("name", ["events", "events_test_", "events_test_abc_def", "postgres"])
def test_unrelated_names_have_no_timestamp(name):
    assert pg.created_at_from_name(name) is None
