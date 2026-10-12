"""Shared fixtures.

Integration tests never touch the dev database named in DATABASE_URL. They create throwaway
databases on the same server and drop them afterwards. Without DATABASE_URL they skip, unless
REQUIRE_DB=1 (set in CI), in which case they fail.
"""

import os
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import make_conninfo

from events_assistant.db.migrate import apply_migrations, default_migrations_dir

load_dotenv()


def _no_database(reason: str) -> None:
    if os.environ.get("REQUIRE_DB") == "1":
        pytest.fail(f"REQUIRE_DB=1 but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def admin_url() -> str:
    """URL of the server's existing database, used only to create and drop test databases."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        _no_database("DATABASE_URL is not set")
    try:
        with psycopg.connect(url, connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        _no_database(f"cannot connect to DATABASE_URL: {exc}")
    return url


def _create_database(admin_url: str) -> tuple[str, str]:
    name = f"events_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, make_conninfo(admin_url, dbname=name)


def _drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def fresh_database_url(admin_url: str) -> Iterator[Callable[[], str]]:
    """Factory returning the URL of a new, empty database. All are dropped after the test."""
    created: list[str] = []

    def factory() -> str:
        name, url = _create_database(admin_url)
        created.append(name)
        return url

    yield factory
    for name in created:
        _drop_database(admin_url, name)


@pytest.fixture(scope="session")
def migrated_database_url(admin_url: str) -> Iterator[str]:
    """One database per test session with all migrations applied."""
    name, url = _create_database(admin_url)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            apply_migrations(conn, default_migrations_dir())
        yield url
    finally:
        _drop_database(admin_url, name)


@pytest.fixture
def db(migrated_database_url: str) -> Iterator[psycopg.Connection]:
    """Connection to the migrated database; everything is rolled back after the test.

    Wrap statements expected to fail in ``db.transaction()`` so the error only aborts a
    savepoint, not the whole test transaction.
    """
    with psycopg.connect(migrated_database_url) as conn:
        try:
            yield conn
        finally:
            conn.rollback()
