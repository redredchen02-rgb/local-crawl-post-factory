"""Site roster — persistent registry of discovered / monitored sites.

Stores per-domain state (tier, health counters, crawl timestamps) so that
U2 (discovery), U3 (monitoring), and U4 (activation) can read/write site
status without coupling to each other.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from cpost.core.db import connect as _db_connect

# ---------------------------------------------------------------------------
# Tier constants
# ---------------------------------------------------------------------------
CANDIDATE = "candidate"
MONITORED = "monitored"
ACTIVE = "active"
MIRROR = "mirror"
FAILED = "failed"
INACTIVE = "inactive"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
  domain              TEXT PRIMARY KEY,
  start_url           TEXT,
  source_id           TEXT,
  tier                TEXT,
  is_mirror           INTEGER DEFAULT 0,
  fail_count          INTEGER DEFAULT 0,
  monitored_ok_count  INTEGER DEFAULT 0,
  item_regex          TEXT,
  body_selector       TEXT,
  last_checked_at     TEXT,
  last_crawled_at     TEXT,
  notes               TEXT
);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
@contextmanager
def connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    with _db_connect(path, _SCHEMA) as conn:
        yield conn


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def upsert_site(
    path: str,
    domain: str,
    start_url: str,
    *,
    source_id: str | None = None,
    tier: str = CANDIDATE,
    is_mirror: bool = False,
    item_regex: str | None = None,
    body_selector: str | None = None,
    notes: str | None = None,
) -> None:
    """Insert or fully replace a site row (domain is PK; all fields refreshed)."""
    with connect(path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO sites
              (domain, start_url, source_id, tier, is_mirror,
               fail_count, monitored_ok_count,
               item_regex, body_selector, last_checked_at, last_crawled_at, notes)
            VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, NULL, NULL, ?)
            """,
            (
                domain,
                start_url,
                source_id,
                tier,
                1 if is_mirror else 0,
                item_regex,
                body_selector,
                notes,
            ),
        )


def list_by_tier(path: str, tier: str) -> list[dict[str, object]]:
    """Return all sites whose tier matches *tier* exactly."""
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM sites WHERE tier = ?", (tier,))
        return [dict(row) for row in cur.fetchall()]


def update_health(
    path: str,
    domain: str,
    *,
    fail_count: int,
    monitored_ok_count: int,
    last_checked_at: str,
) -> None:
    """Overwrite health counters and last_checked_at for *domain*."""
    with connect(path) as conn:
        conn.execute(
            """
            UPDATE sites
               SET fail_count         = ?,
                   monitored_ok_count = ?,
                   last_checked_at    = ?
             WHERE domain = ?
            """,
            (fail_count, monitored_ok_count, last_checked_at, domain),
        )


def update_crawled_at(
    path: str,
    domain: str,
    *,
    last_crawled_at: str,
) -> None:
    """Record the timestamp of the most recent successful crawl."""
    with connect(path) as conn:
        conn.execute(
            "UPDATE sites SET last_crawled_at = ? WHERE domain = ?",
            (last_crawled_at, domain),
        )


def set_tier(path: str, domain: str, tier: str) -> None:
    """Promote or demote *domain* to a new tier."""
    with connect(path) as conn:
        conn.execute(
            "UPDATE sites SET tier = ? WHERE domain = ?",
            (tier, domain),
        )


def list_active(path: str) -> list[dict[str, object]]:
    """Return all sites with tier = ACTIVE."""
    return list_by_tier(path, ACTIVE)
