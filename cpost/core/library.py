"""Crawl library: persistent store of normalized crawled items (plan U1).

A new store distinct from ``items`` (publish truth), ``runs``, and ``reviewed``:
this is the raw crawl corpus that downstream aggregation / scoring / generation
reads. It lives in the same SQLite state file as a new table.

Never drops data: ``upsert`` is keyed on ``canonical_url`` and preserves the
first ``ingested_at``. ``cluster_id`` is written later by the clustering stage
(plan U3); this module only declares the column.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from cpost.core.db import connect as _db_connect

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
  cluster_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_library_source ON library_items(source_id);
CREATE INDEX IF NOT EXISTS idx_library_cluster ON library_items(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id           TEXT PRIMARY KEY,
  member_count         INTEGER NOT NULL,
  source_count         INTEGER NOT NULL,
  representative_url   TEXT,
  representative_title TEXT,
  earliest_published   TEXT,
  latest_published     TEXT,
  confidence           REAL,
  quality              REAL,
  score                REAL,
  updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clusters_score ON clusters(score);

CREATE TABLE IF NOT EXISTS generations (
  cache_key   TEXT PRIMARY KEY,
  cluster_id  TEXT,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  model       TEXT,
  created_at  TEXT NOT NULL
);
"""


@contextmanager
def connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    with _db_connect(path, _SCHEMA) as conn:
        yield conn


def upsert(conn: sqlite3.Connection, *, canonical_url: str, title: str, now: str,
           source_id: str | None = None, url: str | None = None,
           source_text: str | None = None, description: str | None = None,
           published_at: str | None = None, discovered_at: str | None = None) -> None:
    """Insert or update one library row, preserving ingested_at on conflict.

    Keyed on ``canonical_url``. On conflict the content fields are refreshed but
    ``ingested_at`` (first-seen) and ``cluster_id`` (assigned later by
    clustering) are left untouched -- re-ingesting a URL must never drop its
    cluster assignment or rewrite when it first entered the library.
    """
    conn.execute(
        """
        INSERT INTO library_items (canonical_url, source_id, url, title, source_text,
                                   description, published_at, discovered_at, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url) DO UPDATE SET
            source_id=COALESCE(excluded.source_id, library_items.source_id),
            url=COALESCE(excluded.url, library_items.url),
            title=excluded.title,
            source_text=COALESCE(excluded.source_text, library_items.source_text),
            description=COALESCE(excluded.description, library_items.description),
            published_at=COALESCE(excluded.published_at, library_items.published_at),
            discovered_at=COALESCE(excluded.discovered_at, library_items.discovered_at)
        """,
        (canonical_url, source_id, url, title, source_text, description,
         published_at, discovered_at, now),
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


# --- clusters (scoops): a view layer over library_items, never drops rows ------

def assign_clusters(conn: sqlite3.Connection, clusters: list[dict], now: str) -> None:
    """Replace all cluster assignments with ``clusters`` (full recompute).

    Idempotent rebuild: clears every item's ``cluster_id`` and the ``clusters``
    table, then re-stamps members and inserts one summary row per cluster. Library
    rows themselves are never deleted -- only the cluster_id *view* is rewritten,
    so re-running over the same library yields identical assignments. Scores
    (confidence/quality/score) are left NULL here and filled later by scoring.

    All statements run inside the single transaction opened by ``connect`` and
    commit together on success, so a concurrent reader sees either the old or the
    new assignment (never the half-cleared middle) and a mid-rebuild failure
    rolls the whole thing back.
    """
    conn.execute("UPDATE library_items SET cluster_id = NULL")
    conn.execute("DELETE FROM clusters")
    for c in clusters:
        for url in c["members"]:
            conn.execute(
                "UPDATE library_items SET cluster_id = ? WHERE canonical_url = ?",
                (c["cluster_id"], url),
            )
        conn.execute(
            """
            INSERT INTO clusters (cluster_id, member_count, source_count,
                                  representative_url, representative_title,
                                  earliest_published, latest_published, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (c["cluster_id"], c["member_count"], c["source_count"],
             c.get("representative_url"), c.get("representative_title"),
             c.get("earliest_published"), c.get("latest_published"), now),
        )


def list_clusters(conn: sqlite3.Connection, *, by_score: bool = False,
                  limit: int | None = None) -> list[dict]:
    """Return cluster summary rows as dicts (by score desc when ``by_score``)."""
    conn.row_factory = sqlite3.Row
    order = ("score DESC, confidence DESC, cluster_id" if by_score
             else "cluster_id")
    sql = f"SELECT * FROM clusters ORDER BY {order}"
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_cluster(conn: sqlite3.Connection, cluster_id: str) -> dict | None:
    """Return one cluster summary row as a dict, or None when absent."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM clusters WHERE cluster_id = ? LIMIT 1", (cluster_id,)
    ).fetchone()
    return dict(row) if row else None


def get_cluster_members(conn: sqlite3.Connection, cluster_id: str) -> list[dict]:
    """Return the library items belonging to ``cluster_id`` (for scoring/generation)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM library_items WHERE cluster_id = ? ORDER BY canonical_url",
        (cluster_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_cluster_scores(conn: sqlite3.Connection, cluster_id: str, *,
                       confidence: float, quality: float, score: float,
                       now: str) -> None:
    """Write the two score axes + combined score onto a cluster (idempotent)."""
    conn.execute(
        "UPDATE clusters SET confidence = ?, quality = ?, score = ?, updated_at = ? "
        "WHERE cluster_id = ?",
        (confidence, quality, score, now, cluster_id),
    )


# --- generations: cache of LLM-synthesized articles (plan U4) -------------------
# Keyed by a (member fingerprint + model + prompt version) hash so a re-run over
# an unchanged scoop reuses the same article (stable + no re-billing). Membership
# changes the key, so stale fabricated content is never served for a changed scoop.

def get_generation(conn: sqlite3.Connection, cache_key: str) -> dict | None:
    """Return a cached generation (title/body) for ``cache_key``, or None."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM generations WHERE cache_key = ? LIMIT 1", (cache_key,)
    ).fetchone()
    return dict(row) if row else None


def put_generation(conn: sqlite3.Connection, *, cache_key: str, cluster_id: str,
                   title: str, body: str, model: str | None, now: str) -> None:
    """Insert or replace a cached generation."""
    conn.execute(
        """
        INSERT INTO generations (cache_key, cluster_id, title, body, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            title=excluded.title, body=excluded.body,
            model=excluded.model, created_at=excluded.created_at
        """,
        (cache_key, cluster_id, title, body, model, now),
    )
