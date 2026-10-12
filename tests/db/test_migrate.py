import hashlib
import shutil
import threading
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from events_assistant.db import connection
from events_assistant.db.migrate import (
    ADVISORY_LOCK_KEY,
    MigrationError,
    apply_migrations,
    default_migrations_dir,
    discover,
    main,
)

EXPECTED_TABLES = {
    "events",
    "my_events",
    "reminders",
    "messages",
    "preferences",
    "ingest_runs",
    "schema_migrations",
}


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body)
    return path


def _tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    return {row[0] for row in rows}


def _applied(conn: psycopg.Connection) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()


# --- discovery (no database) -------------------------------------------------


def test_discover_sorts_by_version_and_ignores_non_sql(tmp_path):
    _write(tmp_path, "002_second.sql", "SELECT 2;")
    _write(tmp_path, "001_first.sql", "SELECT 1;")
    _write(tmp_path, "README.md", "not a migration")

    migrations = discover(tmp_path)

    assert [m.version for m in migrations] == ["001_first", "002_second"]
    assert migrations[0].checksum == hashlib.sha256(b"SELECT 1;").hexdigest()


@pytest.mark.parametrize("name", ["1_init.sql", "001-init.sql", "001_Init.sql", "init.sql"])
def test_discover_rejects_bad_filenames(tmp_path, name):
    _write(tmp_path, name, "SELECT 1;")
    with pytest.raises(MigrationError, match="filename"):
        discover(tmp_path)


def test_discover_rejects_duplicate_version_numbers(tmp_path):
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    _write(tmp_path, "001_b.sql", "SELECT 1;")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_discover_rejects_missing_directory(tmp_path):
    with pytest.raises(MigrationError, match=r"not found.*MIGRATIONS_DIR"):
        discover(tmp_path / "nope")


@pytest.mark.parametrize(
    "body",
    [
        "CREATE TABLE a (id int);\nCOMMIT;\nCREATE TABLE b (id int);",
        "BEGIN;\nCREATE TABLE a (id int);",
        "begin ;\nCREATE TABLE a (id int);",
        "START TRANSACTION;",
        "CREATE TABLE a (id int);\nROLLBACK;",
        "CREATE INDEX CONCURRENTLY a_idx ON a (id);",
    ],
)
def test_discover_rejects_transaction_control(tmp_path, body):
    _write(tmp_path, "001_a.sql", body)
    with pytest.raises(MigrationError, match="transaction control"):
        discover(tmp_path)


def test_discover_allows_plpgsql_blocks_and_comments(tmp_path):
    body = """
    -- COMMIT; inside a comment is fine, so is CONCURRENTLY here
    CREATE FUNCTION f() RETURNS void AS $$
    BEGIN
        PERFORM 1;
    END;
    $$ LANGUAGE plpgsql;
    """
    _write(tmp_path, "001_a.sql", body)
    assert [m.version for m in discover(tmp_path)] == ["001_a"]


def test_repo_migrations_are_well_formed():
    versions = [m.version for m in discover(default_migrations_dir())]
    assert versions[0] == "001_init"


# --- applying (integration) --------------------------------------------------


@pytest.mark.integration
def test_applies_repo_migrations_on_empty_database_then_is_a_noop(fresh_database_url):
    with psycopg.connect(fresh_database_url(), autocommit=True) as conn:
        first = apply_migrations(conn, default_migrations_dir())
        second = apply_migrations(conn, default_migrations_dir())

        assert first == [m.version for m in discover(default_migrations_dir())]
        assert second == []
        assert _tables(conn) >= EXPECTED_TABLES
        assert _applied(conn) == [
            (m.version, m.checksum) for m in discover(default_migrations_dir())
        ]


@pytest.mark.integration
def test_applies_only_new_migrations(tmp_path, fresh_database_url):
    _write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    with psycopg.connect(fresh_database_url(), autocommit=True) as conn:
        assert apply_migrations(conn, tmp_path) == ["001_a"]
        _write(tmp_path, "002_b.sql", "CREATE TABLE b (id int);")
        assert apply_migrations(conn, tmp_path) == ["002_b"]
        assert {"a", "b"} <= _tables(conn)


@pytest.mark.integration
def test_failing_migration_rolls_back_completely(tmp_path, fresh_database_url):
    _write(tmp_path, "001_ok.sql", "CREATE TABLE ok (id int);")
    _write(tmp_path, "002_bad.sql", "CREATE TABLE partial (id int);\nSELECT * FROM missing_table;")
    with psycopg.connect(fresh_database_url(), autocommit=True) as conn:
        with pytest.raises(MigrationError, match="002_bad"):
            apply_migrations(conn, tmp_path)

        assert "ok" in _tables(conn)
        assert "partial" not in _tables(conn)
        assert [v for v, _ in _applied(conn)] == ["001_ok"]


@pytest.mark.integration
def test_lock_is_released_after_a_failed_migration(tmp_path, fresh_database_url):
    _write(tmp_path, "001_bad.sql", "SELECT * FROM missing_table;")
    url = fresh_database_url()
    with (
        psycopg.connect(url, autocommit=True) as conn,
        psycopg.connect(url, autocommit=True) as other,
    ):
        with pytest.raises(MigrationError):
            apply_migrations(conn, tmp_path)
        # conn is still open, so if it had kept the session lock this would return false.
        (got,) = other.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()
        assert got is True


@pytest.mark.integration
def test_out_of_order_migration_is_rejected(tmp_path, fresh_database_url):
    _write(tmp_path, "002_b.sql", "CREATE TABLE b (id int);")
    with psycopg.connect(fresh_database_url(), autocommit=True) as conn:
        apply_migrations(conn, tmp_path)
        _write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
        with pytest.raises(MigrationError, match="out of order"):
            apply_migrations(conn, tmp_path)


@pytest.mark.integration
def test_waiting_for_the_lock_times_out(tmp_path, fresh_database_url):
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    url = fresh_database_url()
    with (
        psycopg.connect(url, autocommit=True) as holder,
        psycopg.connect(url, autocommit=True) as conn,
    ):
        holder.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        with pytest.raises(MigrationError, match="lock"):
            apply_migrations(conn, tmp_path, lock_timeout=timedelta(milliseconds=200))
        # The timeout must not leak into the session.
        (setting,) = conn.execute("SHOW lock_timeout").fetchone()
        assert setting == "0"


@pytest.mark.integration
def test_edited_migration_is_rejected(tmp_path, fresh_database_url):
    path = _write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    with psycopg.connect(fresh_database_url(), autocommit=True) as conn:
        apply_migrations(conn, tmp_path)
        path.write_text("CREATE TABLE a (id bigint);")
        with pytest.raises(MigrationError, match="changed"):
            apply_migrations(conn, tmp_path)


@pytest.mark.integration
def test_applied_migration_missing_from_disk_is_rejected(tmp_path, fresh_database_url):
    path = _write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    with psycopg.connect(fresh_database_url(), autocommit=True) as conn:
        apply_migrations(conn, tmp_path)
        path.unlink()
        with pytest.raises(MigrationError, match="missing"):
            apply_migrations(conn, tmp_path)


@pytest.mark.integration
def test_requires_autocommit_connection(tmp_path, fresh_database_url):
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    with (
        psycopg.connect(fresh_database_url()) as conn,
        pytest.raises(ValueError, match="autocommit"),
    ):
        apply_migrations(conn, tmp_path)


@pytest.mark.integration
def test_concurrent_runners_apply_each_migration_once(tmp_path, fresh_database_url):
    shutil.copy(default_migrations_dir() / "001_init.sql", tmp_path / "001_init.sql")
    url = fresh_database_url()
    results: list[list[str]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(3)

    def run() -> None:
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                barrier.wait(timeout=10)  # start all runners at the same moment
                results.append(apply_migrations(conn, tmp_path))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(results) == [[], [], ["001_init"]]


# --- CLI and connection --------------------------------------------------------


@pytest.mark.integration
def test_main_applies_then_reports_up_to_date(tmp_path, fresh_database_url, monkeypatch, capsys):
    _write(tmp_path, "001_a.sql", "CREATE TABLE a (id int);")
    monkeypatch.setenv("DATABASE_URL", fresh_database_url())

    assert main(["--dir", str(tmp_path)]) == 0
    assert "applied 001_a" in capsys.readouterr().out

    assert main(["--dir", str(tmp_path)]) == 0
    assert "up to date" in capsys.readouterr().out


@pytest.mark.integration
def test_main_reports_applied_versions_before_a_failure(
    tmp_path, fresh_database_url, monkeypatch, capsys
):
    _write(tmp_path, "001_ok.sql", "CREATE TABLE ok (id int);")
    _write(tmp_path, "002_bad.sql", "SELECT * FROM missing_table;")
    monkeypatch.setenv("DATABASE_URL", fresh_database_url())

    assert main(["--dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "applied 001_ok" in captured.out
    assert "002_bad" in captured.err


def test_main_reports_unreachable_database(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")

    assert main(["--dir", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("error:")


def test_main_reports_missing_database_url(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    monkeypatch.setattr(connection, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert main(["--dir", str(tmp_path)]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err


def test_main_returns_error_code_on_migration_error(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "bad.sql", "SELECT 1;")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    assert main(["--dir", str(tmp_path)]) == 1
    assert "filename" in capsys.readouterr().err


def test_default_dir_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    assert default_migrations_dir() == tmp_path


def test_get_database_url_requires_env(monkeypatch):
    monkeypatch.setattr(connection, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        connection.get_database_url()


def test_get_database_url_reads_env(monkeypatch):
    monkeypatch.setattr(connection, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/db")
    assert connection.get_database_url() == "postgresql://x@localhost/db"
