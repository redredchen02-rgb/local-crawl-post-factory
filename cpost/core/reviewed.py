"""Persistent 'reviewed' publish-gate store + content binding (Q9).

Gate ① of the publish flow records that an operator opened a package's review
page. Persisting it (vs an in-memory set) survives WebUI restarts. To stay safe,
the marker is bound to the *content the reviewer saw* via a content-subtree hash
(title + caption/body + canonical_url). A later content change (re-render) yields
a different hash, so a stale review no longer satisfies the gate and publish is
rejected (fail-closed).

Lives in the same SQLite file as items/runs (state path), on the operator side --
never inside the package manifest, which a re-render would rewrite (and which the
content the gate protects). Each call opens a fresh connection, so it is safe to
call from the request thread or a background job thread (no shared connection,
no check_same_thread concern).
"""

import hashlib
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cpost.core.db import connect as _db_connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviewed (
  post_id    TEXT PRIMARY KEY,
  content_id TEXT NOT NULL,
  ts         TEXT NOT NULL
);
"""

_SEP = "\x1f"  # unit separator: an unambiguous field boundary in the hash input


def content_id(manifest: dict) -> str:
    """Stable hash of the reviewed content subtree (Q9).

    Hashes only what the reviewer actually saw -- ``content.title``,
    ``content.body`` (caption) and ``source.canonical_url``. Deliberately
    excludes ``audit.*`` / ``backend.*`` and never hashes raw file bytes or
    mtime: lifecycle saves (draft/verify/publish rewrite the whole manifest,
    bumping mtime and audit timestamps) would otherwise invalidate a valid
    review and lock the operator out.
    """
    content = manifest.get("content", {}) or {}
    source = manifest.get("source", {}) or {}
    parts = [
        content.get("title") or "",
        content.get("body") or "",
        source.get("canonical_url") or "",
    ]
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


@contextmanager
def _connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    with _db_connect(path, _SCHEMA) as conn:
        yield conn


def mark(path: str, post_id: str, cid: str) -> None:
    """Record (or refresh) that ``post_id`` was reviewed at content-id ``cid``."""
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO reviewed (post_id, content_id, ts) VALUES (?, ?, ?) "
            "ON CONFLICT(post_id) DO UPDATE SET content_id=excluded.content_id, ts=excluded.ts",
            (post_id, cid, datetime.now(timezone.utc).isoformat()),
        )


def get(path: str, post_id: str) -> str | None:
    """Return the reviewed content-id for ``post_id``, or None (fail-closed)."""
    if not Path(path).exists():
        return None
    with _connect(path) as conn:
        cur = conn.execute(
            "SELECT content_id FROM reviewed WHERE post_id = ?", (post_id,))
        row = cur.fetchone()
    return row[0] if row else None
