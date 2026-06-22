"""Shared SQLite connection + schema lifecycle for all core modules.

Replaces the duplicated ``_connect`` / schema-ensure boilerplate across
``state.py``, ``runs.py``, and ``reviewed.py`` (U4.1). Each module still
owns its own schema and migration list; only the connection/setup/teardown
pattern lives here.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from cpost.core.errors import DependencyError


@contextmanager
def connect(path: str, schema: str,
            migrations: list[tuple[int, str]] | None = None,
            extra: list[str] | None = None
            ) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with WAL mode, ensure schema, run migrations.

    Parameters
    ----------
    path
        Path to the SQLite file (created if missing).
    schema
        DDL to run when the file is first created (``CREATE TABLE IF NOT EXISTS``).
    migrations
        Pending-migration list: ``[(version, ddl), ...]``. Applied in order
        when the stored schema version is older.
    extra
        Extra DDL (indexes, triggers) run every connection after schema+migrations.

    Yields the connection with an open transaction; caller's body commits.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(p))
    except sqlite3.Error as exc:  # pragma: no cover - environment dependent
        raise DependencyError(f"sqlite unavailable: {exc}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(schema)
        if migrations:
            _apply_migrations(conn, migrations)
        if extra:
            for ddl in extra:
                conn.execute(ddl)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _apply_migrations(conn: sqlite3.Connection, migrations: list[tuple[int, str]]) -> None:
    """Apply pending migrations against ``_schema_meta`` (U4.2).

    Creates the meta table on first use, then runs any migration whose
    version number is higher than the stored ``schema_version``. Each
    migration runs inside a savepoint so a single failed step never
    corrupts the rest.
    """
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS _schema_meta ("
        "  schema_version INTEGER PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    from datetime import datetime, timezone as tz

    row = conn.execute(
        "SELECT MAX(schema_version) FROM _schema_meta"
    ).fetchone()
    current = row[0] if row and row[0] is not None else 0

    for version, ddl in migrations:
        if version > current:
            sp = f"sp_migrate_{version}"
            conn.execute(f"SAVEPOINT {sp}")
            try:
                for stmt in ddl.split(";"):
                    if stmt.strip():
                        conn.execute(stmt)
                conn.execute(
                    "INSERT OR IGNORE INTO _schema_meta (schema_version, applied_at) "
                    "VALUES (?, ?)",
                    (version, datetime.now(tz.utc).isoformat()),
                )
                conn.execute(f"RELEASE {sp}")
            except sqlite3.OperationalError as exc:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute(f"RELEASE {sp}")
                if "duplicate column name" in str(exc).lower():
                    conn.execute(
                        "INSERT OR IGNORE INTO _schema_meta (schema_version, applied_at) "
                        "VALUES (?, ?)",
                        (version, datetime.now(tz.utc).isoformat()),
                    )
                    continue
                raise
