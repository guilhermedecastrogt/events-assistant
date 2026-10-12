"""Helpers for throwaway Postgres databases used by integration tests."""

import os
import re
import secrets
import time

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

TEST_DB_PREFIX = "events_test_"
_NAME_RE = re.compile(rf"^{TEST_DB_PREFIX}(?P<ts>\d{{10}})_[0-9a-f]+$")
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
STALE_AFTER_SECONDS = 6 * 3600


def no_database(reason: str) -> None:
    """Skip the test, or fail it when REQUIRE_DB=1 (CI)."""
    if os.environ.get("REQUIRE_DB") == "1":
        pytest.fail(f"REQUIRE_DB=1 but {reason}")
    pytest.skip(reason)


def is_local(url: str) -> bool:
    """True if every host in the connection string is local (TCP loopback or a Unix socket)."""
    hosts = (conninfo_to_dict(url).get("host") or "").split(",")
    return all(h in LOCAL_HOSTS or h.startswith("/") for h in hosts)


def new_test_database_name() -> str:
    return f"{TEST_DB_PREFIX}{int(time.time())}_{secrets.token_hex(4)}"


def created_at_from_name(name: str) -> int | None:
    match = _NAME_RE.match(name)
    return int(match["ts"]) if match else None


def create_database(admin_url: str) -> tuple[str, str]:
    """Create an empty database; return (name, url)."""
    name = new_test_database_name()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, make_conninfo(admin_url, dbname=name)


def drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def drop_stale_test_databases(admin_url: str) -> None:
    """Drop test databases leaked by killed runs (older than STALE_AFTER_SECONDS)."""
    cutoff = time.time() - STALE_AFTER_SECONDS
    with psycopg.connect(admin_url, autocommit=True) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s",
                (TEST_DB_PREFIX + "%",),
            )
        ]
        for name in names:
            created = created_at_from_name(name)
            if created is not None and created < cutoff:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
