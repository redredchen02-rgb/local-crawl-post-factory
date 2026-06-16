"""Run history (R9/R11): a queryable time-series log of pipeline/backend actions.

Lives in the same SQLite file as ``items`` (state path). Division of truth:
  - ``items``  = the published source-of-truth (dedupe reads this)
  - ``runs``   = time-series history of what happened (this module)
  - audit.jsonl = low-level append log

``runs`` never drives dedupe; it is for observability only.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.errors import DependencyError

# Schema kept in sync with the migration in _ensure_schema(): a fresh DB gets
# run_id/severity from this CREATE; an old DB gets them via ADD COLUMN. Both
# must end with identical columns.
# Table create only. Indexes are created in _ensure_schema *after* migration so
# an index on a post-release column (run_id) is never built before ADD COLUMN.
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

# Columns added after the original release; applied to old DBs via ADD COLUMN.
_ADDED_COLUMNS = (("run_id", "TEXT"), ("severity", "TEXT"))
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_runs_post ON runs(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id)",
)


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


def _ensure_schema(conn) -> None:
    """Create the table and bring an old DB up to the current columns.

    Kept off the hot write path: callers run this once per connection at open
    time, not per insert. Tolerates a concurrent migration (duplicate column).
    """
    conn.executescript(_SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, coltype in _ADDED_COLUMNS:
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {coltype}")
            except sqlite3.OperationalError:  # pragma: no cover - concurrent migrate
                pass  # another connection added it first
    for stmt in _INDEXES:  # after migration: run_id column now exists
        conn.execute(stmt)


@contextmanager
def _connect(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(p))
    except sqlite3.Error as exc:  # pragma: no cover
        raise DependencyError(f"sqlite unavailable: {exc}")
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def open_run_conn(path):
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


def record_run(path, *, stage, status, post_id=None, detail=None, error=None,
               run_id=None, severity=None, conn=None) -> None:
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


def list_runs(path, limit=100, *, post_id=None, severity=None, run_id=None) -> list:
    """Return the most recent runs (newest first) as dicts, optionally filtered.

    Filter by ``run_id`` to pull the whole lifecycle of one pipeline/batch run
    (build…publish) as a single correlated group (Q7).
    """
    if not Path(path).exists():
        return []
    where, params = [], []
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
