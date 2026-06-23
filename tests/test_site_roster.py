"""Tests for cpost.core.site_roster — U1."""

import os
import pytest

from cpost.core.site_roster import (
    ACTIVE,
    CANDIDATE,
    MIRROR,
    upsert_site,
    list_active,
    list_by_tier,
    update_health,
    update_crawled_at,
    set_tier,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: object) -> str:
    assert isinstance(tmp_path, type(tmp_path))  # satisfy mypy: it's a Path
    from pathlib import Path
    p: Path = tmp_path  # type: ignore[assignment]
    return str(p / "roster.db")


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------

def test_upsert_then_list_active(db_path: str) -> None:
    """Upsert a single active site → list_active returns it."""
    upsert_site(db_path, "example.com", "https://example.com/", tier=ACTIVE)
    results = list_active(db_path)
    assert len(results) == 1
    assert results[0]["domain"] == "example.com"
    assert results[0]["tier"] == ACTIVE


def test_list_active_excludes_mirror(db_path: str) -> None:
    """Two sites (active + mirror) → list_active returns only the active one."""
    upsert_site(db_path, "active.com", "https://active.com/", tier=ACTIVE)
    upsert_site(db_path, "mirror.com", "https://mirror.com/", tier=MIRROR, is_mirror=True)
    results = list_active(db_path)
    assert len(results) == 1
    assert results[0]["domain"] == "active.com"


def test_update_health_reflected_in_list_by_tier(db_path: str) -> None:
    """update_health increments fail_count → list_by_tier shows updated value."""
    upsert_site(db_path, "site.com", "https://site.com/", tier=CANDIDATE)
    update_health(
        db_path,
        "site.com",
        fail_count=3,
        monitored_ok_count=1,
        last_checked_at="2026-06-23T00:00:00Z",
    )
    rows = list_by_tier(db_path, CANDIDATE)
    assert len(rows) == 1
    assert rows[0]["fail_count"] == 3
    assert rows[0]["monitored_ok_count"] == 1
    assert rows[0]["last_checked_at"] == "2026-06-23T00:00:00Z"


def test_update_crawled_at(db_path: str) -> None:
    """update_crawled_at stores the timestamp correctly."""
    upsert_site(db_path, "crawl.com", "https://crawl.com/", tier=ACTIVE)
    update_crawled_at(db_path, "crawl.com", last_crawled_at="2026-06-23T12:00:00Z")
    rows = list_by_tier(db_path, ACTIVE)
    assert rows[0]["last_crawled_at"] == "2026-06-23T12:00:00Z"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_upsert_twice_same_domain_uses_latest_start_url(db_path: str) -> None:
    """INSERT OR REPLACE: second upsert with different start_url wins."""
    upsert_site(db_path, "dup.com", "https://dup.com/v1/", tier=CANDIDATE)
    upsert_site(db_path, "dup.com", "https://dup.com/v2/", tier=ACTIVE)
    rows = list_by_tier(db_path, ACTIVE)
    assert len(rows) == 1
    assert rows[0]["start_url"] == "https://dup.com/v2/"
    # old candidate row should be gone
    assert list_by_tier(db_path, CANDIDATE) == []


def test_db_path_parent_dir_auto_created(tmp_path: object) -> None:
    """db.py creates parent dirs; calling upsert_site must not crash."""
    from pathlib import Path
    p: Path = tmp_path  # type: ignore[assignment]
    deep_path = str(p / "nested" / "dir" / "roster.db")
    # Should not raise — db.py does mkdir(parents=True, exist_ok=True)
    upsert_site(deep_path, "new.com", "https://new.com/", tier=CANDIDATE)
    rows = list_by_tier(deep_path, CANDIDATE)
    assert rows[0]["domain"] == "new.com"


def test_tier_none_not_returned_by_list_active(db_path: str) -> None:
    """A row with tier=NULL is not returned by list_active (tier=ACTIVE filter)."""
    upsert_site(db_path, "nulltier.com", "https://nulltier.com/", tier=CANDIDATE)
    # Forcibly set tier to NULL via set_tier with None-coerced string path
    # We use raw connect to bypass the tier parameter type for this edge test.
    from cpost.core.site_roster import connect
    with connect(db_path) as conn:
        conn.execute("UPDATE sites SET tier = NULL WHERE domain = 'nulltier.com'")
    assert list_active(db_path) == []
    # Also check list_by_tier for ACTIVE doesn't match it
    assert list_by_tier(db_path, ACTIVE) == []


# ---------------------------------------------------------------------------
# set_tier smoke test
# ---------------------------------------------------------------------------

def test_set_tier_promotes_candidate_to_active(db_path: str) -> None:
    """set_tier changes tier from CANDIDATE to ACTIVE."""
    upsert_site(db_path, "promote.com", "https://promote.com/", tier=CANDIDATE)
    assert list_active(db_path) == []
    set_tier(db_path, "promote.com", ACTIVE)
    rows = list_active(db_path)
    assert len(rows) == 1
    assert rows[0]["domain"] == "promote.com"
