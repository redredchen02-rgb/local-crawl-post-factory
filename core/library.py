"""Crawl library: persistent store of normalized crawled items (plan U1).

A new store distinct from ``items`` (publish truth), ``runs``, and ``reviewed``:
this is the raw crawl corpus that downstream aggregation / scoring / generation
reads. It lives in the same SQLite state file as a new table.

Never drops data: ``upsert`` is keyed on ``canonical_url`` and preserves the
first ``ingested_at``. ``cluster_id`` is written later by the clustering stage
(plan U3); this module only declares the column. ``content_fingerprint`` is
likewise reserved for later stages.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from core.db import connect as _db_connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_items (
  canonical_url       TEXT PRIMARY KEY,
  source_id           TEXT,
  url                 TEXT,
  title               TEXT NOT NULL,
  source_text         TEXT,
  description         TEXT,
  published_at        TEXT,
  discovered_at       TEXT,
  ingested_at         TEXT NOT NULL,
  content_fingerprint TEXT,
  cluster_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_library_source ON library_items(source_id);
CREATE INDEX IF NOT EXISTS idx_library_cluster ON library_items(cluster_id);
"""


@contextmanager
def connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    with _db_connect(path, _SCHEMA) as conn:
        yield conn


def upsert(conn: sqlite3.Connection, *, canonical_url: str, title: str, now: str,
           source_id: str | None = None, url: str | None = None,
           source_text: str | None = None, description: str | None = None,
           published_at: str | None = None, discovered_at: str | None = None,
           content_fingerprint: str | None = None) -> None:
    """Insert or update one library row, preserving ingested_at on conflict.

    Keyed on ``canonical_url``. On conflict the content fields are refreshed but
    ``ingested_at`` (first-seen) and ``cluster_id`` (assigned later by
    clustering) are left untouched -- re-ingesting a URL must never drop its
    cluster assignment or rewrite when it first entered the library.
    """
    conn.execute(
        """
        INSERT INTO library_items (canonical_url, source_id, url, title, source_text,
                                   description, published_at, discovered_at,
                                   ingested_at, content_fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url) DO UPDATE SET
            source_id=COALESCE(excluded.source_id, library_items.source_id),
            url=COALESCE(excluded.url, library_items.url),
            title=excluded.title,
            source_text=COALESCE(excluded.source_text, library_items.source_text),
            description=COALESCE(excluded.description, library_items.description),
            published_at=COALESCE(excluded.published_at, library_items.published_at),
            discovered_at=COALESCE(excluded.discovered_at, library_items.discovered_at),
            content_fingerprint=COALESCE(excluded.content_fingerprint,
                                         library_items.content_fingerprint)
        """,
        (canonical_url, source_id, url, title, source_text, description,
         published_at, discovered_at, now, content_fingerprint),
    )


def get(conn: sqlite3.Connection, canonical_url: str) -> dict | None:
    """Return one library row as a dict, or None when absent."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM library_items WHERE canonical_url = ? LIMIT 1",
        (canonical_url,),
    ).fetchone()
    return dict(row) if row else None


def list_items(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Return library rows (newest ingested first) as dicts."""
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM library_items ORDER BY ingested_at DESC, canonical_url"
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def count(conn: sqlite3.Connection) -> int:
    """Return the number of rows in the library."""
    return int(conn.execute("SELECT COUNT(*) FROM library_items").fetchone()[0])
