"""Shared fixtures.

Integration tests never touch the dev database named in DATABASE_URL: they create throwaway
`events_test_*` databases on the same server and drop them afterwards. They only run against
local servers (set ALLOW_REMOTE_TEST_DB=1 to override). Without a database they skip, unless
REQUIRE_DB=1 (set in CI), in which case they fail.
"""

import os
from collections.abc import Callable, Iterator

import psycopg
import pytest
from dotenv import find_dotenv, load_dotenv

from events_assistant.db.migrate import apply_migrations, default_migrations_dir
from tests import pg

load_dotenv(find_dotenv(usecwd=True))


@pytest.fixture(scope="session")
def admin_url() -> str:
    """URL of the server's existing database, used only to create and drop test databases."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pg.no_database("DATABASE_URL is not set")
    if not pg.is_local(url) and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1":
        pg.no_database("refusing to create test databases on a non-local DATABASE_URL host")
    try:
        pg.drop_stale_test_databases(url)
    except psycopg.OperationalError as exc:
        pg.no_database(f"cannot connect to DATABASE_URL: {exc}")
    return url


@pytest.fixture
def fresh_database_url(admin_url: str) -> Iterator[Callable[[], str]]:
    """Factory returning the URL of a new, empty database. All are dropped after the test."""
    created: list[str] = []

    def factory() -> str:
        name, url = pg.create_database(admin_url)
        created.append(name)
        return url

    yield factory
    for name in created:
        pg.drop_database(admin_url, name)


@pytest.fixture(scope="session")
def migrated_database_url(admin_url: str) -> Iterator[str]:
    """One database per test session with all migrations applied."""
    name, url = pg.create_database(admin_url)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            apply_migrations(conn, default_migrations_dir())
        yield url
    finally:
        pg.drop_database(admin_url, name)


@pytest.fixture
def db(migrated_database_url: str) -> Iterator[psycopg.Connection]:
    """Connection to the migrated database; everything is rolled back after the test.

    Wrap statements expected to fail in ``db.transaction()`` so the error only aborts a
    savepoint, not the whole test transaction. Code under test must not commit.
    """
    with psycopg.connect(migrated_database_url) as conn:
        try:
            yield conn
        finally:
            conn.rollback()
