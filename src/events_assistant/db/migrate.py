"""Minimal SQL migration runner (no ORM).

Applies `migrations/NNN_name.sql` files in order and records each one, with a checksum, in
`schema_migrations`. Each file runs in its own transaction together with its bookkeeping row,
so a failing migration leaves nothing behind; files must therefore not contain transaction
control (BEGIN/COMMIT/ROLLBACK) or `CONCURRENTLY`. A Postgres advisory lock (with a timeout)
serialises concurrent runners. Applied migrations must never be edited or reordered: add a new
file with a higher number instead.

Usage: uv run python -m events_assistant.db.migrate [--dir PATH]
"""

import argparse
import contextlib
import hashlib
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg import errors, sql

from events_assistant.db.connection import connect

FILENAME_RE = re.compile(r"^(?P<number>\d{3})_[a-z0-9_]+\.sql$")

# Statements that would break the one-transaction-per-file guarantee. Checked after removing
# comments and dollar-quoted bodies, so PL/pgSQL `BEGIN ... END;` blocks are fine.
FORBIDDEN_RE = re.compile(
    r"^\s*(BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK|END)\s*;|\bCONCURRENTLY\b",
    re.IGNORECASE | re.MULTILINE,
)
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_DOLLAR_QUOTED_RE = re.compile(r"\$(?P<tag>[A-Za-z_]*)\$.*?\$(?P=tag)\$", re.DOTALL)

# Arbitrary constant identifying this runner's advisory lock.
ADVISORY_LOCK_KEY = 7_311_204_412
DEFAULT_LOCK_TIMEOUT = timedelta(seconds=30)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    """Raised when migrations are malformed, edited after being applied, or fail to apply."""


@dataclass(frozen=True)
class Migration:
    version: str  # file stem, e.g. "001_init"
    path: Path
    sql: str
    checksum: str  # sha256 of the file bytes


def default_migrations_dir() -> Path:
    """`MIGRATIONS_DIR` if set, otherwise the repo's top-level `migrations/` directory.

    The fallback only exists in a source checkout; installed packages must set MIGRATIONS_DIR.
    """
    override = os.environ.get("MIGRATIONS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "migrations"


def discover(directory: Path) -> list[Migration]:
    """Return the `.sql` migrations in `directory`, validated and sorted by version."""
    if not directory.is_dir():
        raise MigrationError(
            f"migrations directory not found: {directory} (set MIGRATIONS_DIR or pass --dir)"
        )

    migrations: list[Migration] = []
    seen_numbers: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(
                f"bad migration filename {path.name!r}: expected NNN_lowercase_name.sql"
            )
        number = match["number"]
        if number in seen_numbers:
            raise MigrationError(
                f"duplicate migration number {number}: {seen_numbers[number]} and {path.name}"
            )
        seen_numbers[number] = path.name
        data = path.read_bytes()
        text = data.decode("utf-8")
        _check_no_transaction_control(path.name, text)
        migrations.append(
            Migration(
                version=path.stem,
                path=path,
                sql=text,
                checksum=hashlib.sha256(data).hexdigest(),
            )
        )
    return migrations


def _check_no_transaction_control(name: str, text: str) -> None:
    stripped = _DOLLAR_QUOTED_RE.sub("", _COMMENT_RE.sub("", text))
    found = FORBIDDEN_RE.search(stripped)
    if found:
        raise MigrationError(
            f"{name} contains transaction control ({found.group(0).strip()!r}); each migration "
            "already runs in its own transaction"
        )


def apply_migrations(
    conn: psycopg.Connection,
    directory: Path,
    *,
    lock_timeout: timedelta = DEFAULT_LOCK_TIMEOUT,
    on_applied: Callable[[str], None] | None = None,
) -> list[str]:
    """Apply pending migrations from `directory`; return the versions applied in this call.

    `conn` must be in autocommit mode so each migration gets its own real transaction.
    `on_applied` is called with each version right after it commits.
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection")

    migrations = discover(directory)
    _acquire_lock(conn, lock_timeout)
    try:
        conn.execute(CREATE_TABLE_SQL)
        applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
        _check_applied(migrations, applied)

        newly_applied: list[str] = []
        for migration in migrations:
            if migration.version in applied:
                continue
            try:
                with conn.transaction():
                    conn.execute(migration.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (migration.version, migration.checksum),
                    )
            except psycopg.Error as exc:
                raise MigrationError(f"migration {migration.version} failed: {exc}") from exc
            newly_applied.append(migration.version)
            if on_applied:
                on_applied(migration.version)
        return newly_applied
    finally:
        # A broken connection already released the session lock; never mask the real error.
        if not conn.closed and not conn.broken:
            with contextlib.suppress(psycopg.Error):
                conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


def _acquire_lock(conn: psycopg.Connection, timeout: timedelta) -> None:
    timeout_ms = max(1, int(timeout.total_seconds() * 1000))
    conn.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(f"{timeout_ms}ms")))
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    except errors.LockNotAvailable as exc:
        raise MigrationError(
            f"could not get the migration lock within {timeout}; is another run stuck?"
        ) from exc
    finally:
        conn.execute("RESET lock_timeout")


def _check_applied(migrations: list[Migration], applied: dict[str, str]) -> None:
    on_disk = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        migration = on_disk.get(version)
        if migration is None:
            raise MigrationError(f"applied migration {version} is missing from disk")
        if migration.checksum != checksum:
            raise MigrationError(
                f"migration {version} changed after it was applied; add a new migration instead"
            )
    if applied:
        latest = max(applied)
        early = [m.version for m in migrations if m.version not in applied and m.version < latest]
        if early:
            raise MigrationError(
                f"pending migrations {early} are out of order (latest applied is {latest}); "
                "renumber them after it"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument("--dir", type=Path, default=None, help="migrations directory")
    args = parser.parse_args(argv)
    directory = args.dir or default_migrations_dir()

    def report(version: str) -> None:
        print(f"applied {version}", flush=True)

    try:
        discover(directory)  # fail fast on malformed files before connecting
        with connect(autocommit=True) as conn:
            applied = apply_migrations(conn, directory, on_applied=report)
    except (MigrationError, psycopg.Error, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not applied:
        print("database is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
