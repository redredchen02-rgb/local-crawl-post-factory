"""Run history (R9/R11): a queryable time-series log of pipeline/backend actions.

Lives in the same SQLite file as ``items`` (state path). Division of truth:
  - ``items``  = the published source-of-truth (dedupe reads this)
  - ``runs``   = time-series history of what happened (this module)
  - audit.jsonl = low-level append log

``runs`` never drives dedupe; it is for observability only.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cpost.core.db import connect as _db_connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  stage     TEXT NOT NULL,
  post_id   TEXT,
  status    TEXT NOT NULL,
  detail    TEXT,
  error     TEXT,
  run_id    TEXT,
  severity  TEXT
);
"""

_MIGRATIONS = [
    (1, "ALTER TABLE runs ADD COLUMN run_id TEXT;"),
    (2, "ALTER TABLE runs ADD COLUMN severity TEXT;"),
]

_EXTRA = [
    "CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_runs_post ON runs(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id)",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_run_seq = 0


def new_run_id() -> str:
    """A process-unique correlation id for one pipeline/batch run.

    Timestamp + in-process sequence (no random source needed); good enough to
    group every record of a single run for life-cycle lookups.
    """
    global _run_seq
    _run_seq += 1
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{_run_seq}"


@contextmanager
def _connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    with _db_connect(path, _SCHEMA, migrations=_MIGRATIONS, extra=_EXTRA) as conn:
        yield conn


@contextmanager
def open_run_conn(path: str) -> Generator[sqlite3.Connection, None, None]:
    """Reusable connection for batched record_run calls within one pipeline run.

    Keeps a single SQLite connection open across multiple record_run() calls,
    amortising the open/schema-check/close cost for bulk writes. Each
    record_run(conn=conn) call commits immediately (per-row durability), so a
    failure mid-batch leaves prior records intact.

    Usage::

        with runs.open_run_conn(state_path) as conn:
            for item in items:
                runs.record_run(state_path, ..., conn=conn)
    """
    with _connect(path) as conn:
        yield conn


def record_run(path: str, *, stage: str, status: str, post_id: str | None = None,
               detail: str | None = None, error: str | None = None,
               run_id: str | None = None, severity: str | None = None,
               conn: sqlite3.Connection | None = None) -> None:
    """Append one run record. Never raises on a missing table (auto-created).

    Pass ``conn`` (from :func:`open_run_conn`) to reuse an existing connection
    and avoid repeated open/schema-check overhead during bulk writes. When
    ``conn`` is provided each insert is committed immediately to preserve
    per-row durability semantics.
    """
    if conn is not None:
        conn.execute(
            "INSERT INTO runs (ts, stage, post_id, status, detail, error, run_id, severity) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_now(), stage, post_id, status, detail, error, run_id, severity),
        )
        conn.commit()
    else:
        with _connect(path) as conn_inner:
            conn_inner.execute(
                "INSERT INTO runs (ts, stage, post_id, status, detail, error, run_id, severity) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_now(), stage, post_id, status, detail, error, run_id, severity),
            )


def purge_before(path: str, cutoff_iso: str) -> int:
    """Delete run records older than ``cutoff_iso`` (UTC ISO 8601).

    Returns the number of deleted rows. No-op if the DB file doesn't exist.
    """
    if not Path(path).exists():
        return 0
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM runs WHERE ts < ?", (cutoff_iso,))
    return cur.rowcount


def list_runs(path: str, limit: int = 100, *, post_id: str | None = None,
              severity: str | None = None, run_id: str | None = None) -> list[dict]:
    """Return the most recent runs (newest first) as dicts, optionally filtered.

    Filter by ``run_id`` to pull the whole lifecycle of one pipeline/batch run
    (build…publish) as a single correlated group (Q7).
    """
    if not Path(path).exists():
        return []
    where: list[str] = []
    params: list[str | int] = []
    if post_id is not None:
        where.append("post_id = ?")
        params.append(post_id)
    if severity is not None:
        where.append("severity = ?")
        params.append(severity)
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(int(limit))
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, ts, stage, post_id, status, detail, error, run_id, severity "
            "FROM runs" + clause + " ORDER BY id DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in cur.fetchall()]
