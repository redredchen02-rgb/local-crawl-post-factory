"""SQLite dedupe / publish state (origin spec §9).

Key decision (origin R9): dedupe only treats an item as "processed" once it has
actually been *published*. ``is_processed`` therefore matches rows with
status='published' only. In the first release (no publish stage yet) this means
dedupe effectively always passes items through -- that is expected behaviour,
not a bug.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core.errors import DependencyError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  canonical_url TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  title_hash    TEXT NOT NULL,
  content_hash  TEXT,
  post_id       TEXT,
  status        TEXT NOT NULL,
  draft_url     TEXT,
  published_url TEXT,
  first_seen_at TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  last_error    TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_title_hash ON items(title_hash);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
"""

PUBLISHED = "published"


@contextmanager
def connect(path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(p))
    except sqlite3.Error as exc:  # pragma: no cover - environment dependent
        raise DependencyError(f"sqlite unavailable: {exc}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_processed(conn, canonical_url: str, title_hash: str) -> bool:
    """True only if a *published* row matches this canonical_url or title_hash."""
    cur = conn.execute(
        "SELECT 1 FROM items WHERE status = ? AND (canonical_url = ? OR title_hash = ?) LIMIT 1",
        (PUBLISHED, canonical_url, title_hash),
    )
    return cur.fetchone() is not None


def upsert(conn, *, canonical_url, title, title_hash, status, now,
           content_hash=None, post_id=None, draft_url=None,
           published_url=None, last_error=None) -> None:
    """Insert or update a row, preserving first_seen_at on conflict."""
    conn.execute(
        """
        INSERT INTO items (canonical_url, title, title_hash, content_hash, post_id,
                           status, draft_url, published_url, first_seen_at, updated_at, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url) DO UPDATE SET
            title=excluded.title,
            title_hash=excluded.title_hash,
            content_hash=COALESCE(excluded.content_hash, items.content_hash),
            post_id=COALESCE(excluded.post_id, items.post_id),
            status=excluded.status,
            draft_url=COALESCE(excluded.draft_url, items.draft_url),
            published_url=COALESCE(excluded.published_url, items.published_url),
            updated_at=excluded.updated_at,
            last_error=excluded.last_error
        """,
        (canonical_url, title, title_hash, content_hash, post_id, status,
         draft_url, published_url, now, now, last_error),
    )
