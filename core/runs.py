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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  stage     TEXT NOT NULL,
  post_id   TEXT,
  status    TEXT NOT NULL,
  detail    TEXT,
  error     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts);
CREATE INDEX IF NOT EXISTS idx_runs_post ON runs(post_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(p))
    except sqlite3.Error as exc:  # pragma: no cover
        raise DependencyError(f"sqlite unavailable: {exc}")
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_run(path, *, stage, status, post_id=None, detail=None, error=None) -> None:
    """Append one run record. Never raises on a missing table (auto-created)."""
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO runs (ts, stage, post_id, status, detail, error) VALUES (?,?,?,?,?,?)",
            (_now(), stage, post_id, status, detail, error),
        )


def list_runs(path, limit=100) -> list:
    """Return the most recent runs (newest first) as dicts."""
    if not Path(path).exists():
        return []
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, ts, stage, post_id, status, detail, error FROM runs "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        return [dict(r) for r in cur.fetchall()]
