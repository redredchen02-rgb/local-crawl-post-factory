"""SQLite dedupe / publish state (origin spec §9).

Key decision (origin R9): dedupe only treats an item as "processed" once it has
actually been *published*. ``is_processed`` therefore matches rows with
status='published' only. In the first release (no publish stage yet) this means
dedupe effectively always passes items through -- that is expected behaviour,
not a bug.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from core.db import connect as _db_connect

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
def connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    with _db_connect(path, _SCHEMA) as conn:
        yield conn


def is_processed(conn: sqlite3.Connection, canonical_url: str,
                 title_hash: str | None = None) -> bool:
    """True only if a *published* row matches this canonical_url (Q6: URL-only).

    Dedup is URL-only: two different articles that share a title are NOT treated
    as duplicates (a shared title is not identity). ``title_hash`` is accepted for
    backward-compatible call sites but no longer affects the decision.
    """
    cur = conn.execute(
        "SELECT 1 FROM items WHERE status = ? AND canonical_url = ? LIMIT 1",
        (PUBLISHED, canonical_url),
    )
    return cur.fetchone() is not None


def skip_reason(conn: sqlite3.Connection, canonical_url: str,
                title_hash: str | None = None) -> str | None:
    """Why an item would be skipped: 'url' or None (Q6: URL-only).

    Pure query (no writes). Only a published ``canonical_url`` match causes a
    skip. ``title_hash`` is accepted for backward-compatible call sites but no
    longer affects the decision. Mirrors :func:`is_processed`'s skip condition
    (skip iff this returns non-None) for observability.
    """
    cur = conn.execute(
        "SELECT 1 FROM items WHERE status = ? AND canonical_url = ? LIMIT 1",
        (PUBLISHED, canonical_url),
    )
    return "url" if cur.fetchone() is not None else None


def upsert(conn: sqlite3.Connection, *, canonical_url: str, title: str,
           title_hash: str, status: str, now: str,
           content_hash: str | None = None, post_id: str | None = None,
           draft_url: str | None = None,
           published_url: str | None = None,
           last_error: str | None = None) -> None:
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
