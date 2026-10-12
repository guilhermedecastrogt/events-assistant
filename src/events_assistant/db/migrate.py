"""Minimal SQL migration runner (no ORM).

Applies `migrations/NNN_name.sql` files in order and records each one, with a checksum, in
`schema_migrations`. Each file runs in its own transaction together with its bookkeeping row,
so a failing migration leaves nothing behind. A Postgres advisory lock serialises concurrent
runners. Applied migrations must never be edited: add a new file instead.

Usage: uv run python -m events_assistant.db.migrate [--dir PATH]
"""

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from events_assistant.db.connection import connect

FILENAME_RE = re.compile(r"^(?P<number>\d{3})_[a-z0-9_]+\.sql$")

# Arbitrary constant identifying this runner's advisory lock.
ADVISORY_LOCK_KEY = 7_311_204_412

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
    """`MIGRATIONS_DIR` if set, otherwise the repo's top-level `migrations/` directory."""
    override = os.environ.get("MIGRATIONS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "migrations"


def discover(directory: Path) -> list[Migration]:
    """Return the `.sql` migrations in `directory`, validated and sorted by version."""
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")

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
        migrations.append(
            Migration(
                version=path.stem,
                path=path,
                sql=data.decode("utf-8"),
                checksum=hashlib.sha256(data).hexdigest(),
            )
        )
    return migrations


def apply_migrations(conn: psycopg.Connection, directory: Path) -> list[str]:
    """Apply pending migrations from `directory`; return the versions applied in this call.

    `conn` must be in autocommit mode so each migration gets its own real transaction.
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection")

    migrations = discover(directory)
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
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
        return newly_applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument("--dir", type=Path, default=None, help="migrations directory")
    args = parser.parse_args(argv)
    directory = args.dir or default_migrations_dir()

    try:
        discover(directory)  # fail fast on malformed files before connecting
        with connect(autocommit=True) as conn:
            applied = apply_migrations(conn, directory)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if applied:
        for version in applied:
            print(f"applied {version}")
    else:
        print("database is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
