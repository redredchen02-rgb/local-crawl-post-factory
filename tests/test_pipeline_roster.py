"""Tests for U4: roster integration in crawl_all_sources.

Covers:
- Happy path: roster adds sites not in YAML
- YAML priority: roster host matching YAML host is skipped
- Empty roster_path: only YAML sources crawled (backward compat)
- Missing DB: silently skip, continue with YAML
- Non-allowlist roster key: ignored (not passed to crawl_items)
- update_crawled_at called after successful roster crawl
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from cpost.core.pipeline import crawl_all_sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_cfg(**extra: object) -> dict:
    """Minimal webui_cfg for crawl_all_sources tests."""
    return {
        "start_url": "https://yaml-site.com/",
        "source_id": "yaml-site",
        "item_regex": "",
        "deny_regex": "",
        "limit": 30,
        "max_pages": 200,
        "download_delay": 0.0,
        "concurrency": 8,
        "max_text_chars": 0,
        "min_text_chars": 0,
        "body_selector": "",
        "image_selector": "",
        "date_selector": "",
        "sources": [],
        "roster_path": "",
        **extra,
    }


def _roster_site(
    domain: str = "roster-site.com",
    start_url: str = "https://roster-site.com/",
    source_id: str = "roster-site",
    item_regex: str | None = None,
) -> dict:
    return {
        "domain": domain,
        "start_url": start_url,
        "source_id": source_id,
        "tier": "active",
        "item_regex": item_regex,
        "body_selector": None,
        "last_crawled_at": None,
    }


# ---------------------------------------------------------------------------
# Happy path: roster adds sites not already in YAML sources
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_roster_sites_crawled_when_no_yaml_sources(
        mock_crawl, mock_list_active, mock_update_at):
    """No YAML sources: start_url fallback + 2 roster sites = 3 crawl calls."""
    cfg = _base_cfg(roster_path="/fake/roster.sqlite")
    mock_list_active.return_value = [
        _roster_site("roster-a.com", "https://roster-a.com/", "roster-a"),
        _roster_site("roster-b.com", "https://roster-b.com/", "roster-b"),
    ]
    # Use side_effect (returns a fresh list each call) to avoid the shared-list
    # mutation problem: if return_value is the same list object, extend(items)
    # where items is that same object mutates it in-place, causing incorrect counts.
    mock_crawl.side_effect = lambda *a, **kw: [{"title": "item"}]

    result = crawl_all_sources(cfg)

    # 1 fallback (start_url) + 2 roster = 3 total crawl calls
    assert mock_crawl.call_count == 3
    assert len(result) == 3


@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_roster_sites_added_alongside_yaml_sources(
        mock_crawl, mock_list_active, mock_update_at):
    """YAML has 1 enabled source + roster has 2 distinct sites = 3 crawl calls."""
    cfg = _base_cfg(
        roster_path="/fake/roster.sqlite",
        sources=[
            {"source_id": "yaml-src", "start_url": "https://yaml-src.com/", "enabled": True}
        ],
    )
    mock_list_active.return_value = [
        _roster_site("roster-a.com", "https://roster-a.com/", "roster-a"),
        _roster_site("roster-b.com", "https://roster-b.com/", "roster-b"),
    ]
    mock_crawl.side_effect = lambda *a, **kw: [{"title": "item"}]

    result = crawl_all_sources(cfg)

    assert mock_crawl.call_count == 3
    assert len(result) == 3


# ---------------------------------------------------------------------------
# YAML priority: roster site whose host matches a YAML source is skipped
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_yaml_host_takes_priority_over_roster(
        mock_crawl, mock_list_active, mock_update_at):
    """Roster site with same host as a YAML source is skipped; only 1 crawl call."""
    cfg = _base_cfg(
        roster_path="/fake/roster.sqlite",
        sources=[
            {"source_id": "yaml-src", "start_url": "https://shared-host.com/", "enabled": True}
        ],
    )
    # Roster site has the SAME host as the YAML source.
    mock_list_active.return_value = [
        _roster_site("shared-host.com", "https://shared-host.com/other/", "roster-dup"),
    ]
    mock_crawl.side_effect = lambda *a, **kw: [{"title": "item"}]

    result = crawl_all_sources(cfg)

    # Only the YAML source is crawled; roster duplicate is skipped.
    assert mock_crawl.call_count == 1
    mock_update_at.assert_not_called()


# ---------------------------------------------------------------------------
# Empty roster_path: only YAML sources crawled (backward compat)
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_empty_roster_path_skips_roster(mock_crawl, mock_list_active):
    """roster_path='' → list_active never called; behaves like pre-U4."""
    cfg = _base_cfg(roster_path="")  # explicitly empty
    mock_crawl.return_value = []

    crawl_all_sources(cfg)

    mock_list_active.assert_not_called()


@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_absent_roster_path_skips_roster(mock_crawl, mock_list_active):
    """roster_path absent from cfg → same as empty; list_active never called."""
    cfg = _base_cfg()
    del cfg["roster_path"]
    mock_crawl.return_value = []

    crawl_all_sources(cfg)

    mock_list_active.assert_not_called()


# ---------------------------------------------------------------------------
# Missing DB: silently skip, continue with YAML
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_missing_roster_db_silently_skipped(mock_crawl, mock_list_active):
    """If list_active raises (DB missing/corrupt), YAML crawl continues normally."""
    cfg = _base_cfg(
        roster_path="/nonexistent/roster.sqlite",
        sources=[
            {"source_id": "yaml-src", "start_url": "https://yaml-src.com/", "enabled": True}
        ],
    )
    mock_list_active.side_effect = Exception("no such file or directory")
    mock_crawl.return_value = [{"title": "yaml-item"}]

    result = crawl_all_sources(cfg)

    # YAML source still crawled despite roster error.
    assert mock_crawl.call_count == 1
    assert result == [{"title": "yaml-item"}]


# ---------------------------------------------------------------------------
# Non-allowlist roster key: ignored
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_non_allowlist_roster_key_not_forwarded(
        mock_crawl, mock_list_active, mock_update_at):
    """A roster site's non-allowlisted field (e.g. 'state_path') must NOT be
    forwarded to crawl_items — only _PER_SOURCE_OVERRIDE_KEYS are allowed (B4)."""
    cfg = _base_cfg(roster_path="/fake/roster.sqlite")
    roster_entry = _roster_site()
    # Inject a non-allowlisted key (would redirect state if forwarded).
    roster_entry["state_path"] = "/attacker/evil.sqlite"
    roster_entry["out_dir"] = "/attacker/out"
    mock_list_active.return_value = [roster_entry]
    mock_crawl.side_effect = lambda *a, **kw: []

    crawl_all_sources(cfg)

    # The merged dict passed to crawl_items must use cfg's state_path, NOT the
    # roster entry's injected value.
    assert mock_crawl.call_count == 2  # fallback + 1 roster
    roster_call_cfg = mock_crawl.call_args_list[1][0][0]  # second call, first positional arg
    assert roster_call_cfg.get("state_path") != "/attacker/evil.sqlite"
    assert roster_call_cfg.get("out_dir") != "/attacker/out"


# ---------------------------------------------------------------------------
# Integration: update_crawled_at called after successful roster crawl
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_update_crawled_at_called_after_roster_crawl(
        mock_crawl, mock_list_active, mock_update_at):
    """After a successful roster site crawl, update_crawled_at is called with
    the site's domain and a non-empty ISO timestamp."""
    cfg = _base_cfg(roster_path="/fake/roster.sqlite")
    mock_list_active.return_value = [
        _roster_site("roster-a.com", "https://roster-a.com/", "roster-a"),
    ]
    mock_crawl.side_effect = lambda *a, **kw: [{"title": "item"}]

    crawl_all_sources(cfg)

    mock_update_at.assert_called_once()
    call_args = mock_update_at.call_args
    assert call_args[0][0] == "/fake/roster.sqlite"   # path
    assert call_args[0][1] == "roster-a.com"           # domain
    ts = call_args[1].get("last_crawled_at") or call_args[0][2]
    assert "T" in ts  # rough ISO-8601 check


@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_update_crawled_at_not_called_on_crawl_failure(
        mock_crawl, mock_list_active, mock_update_at):
    """If crawl_items raises for a roster site, update_crawled_at is NOT called
    (we only record timestamp on success)."""
    cfg = _base_cfg(roster_path="/fake/roster.sqlite")
    mock_list_active.return_value = [
        _roster_site("roster-a.com", "https://roster-a.com/", "roster-a"),
    ]
    # Fallback start_url crawl succeeds; roster site crawl fails.
    mock_crawl.side_effect = [[], Exception("crawl error")]

    crawl_all_sources(cfg)

    mock_update_at.assert_not_called()


@patch("cpost.core.pipeline.site_roster.update_crawled_at")
@patch("cpost.core.pipeline.site_roster.list_active")
@patch("cpost.core.pipeline.crawl_items")
def test_update_crawled_at_error_does_not_abort_remaining(
        mock_crawl, mock_list_active, mock_update_at):
    """If update_crawled_at raises, the remaining roster sites still get crawled."""
    cfg = _base_cfg(roster_path="/fake/roster.sqlite")
    mock_list_active.return_value = [
        _roster_site("roster-a.com", "https://roster-a.com/", "roster-a"),
        _roster_site("roster-b.com", "https://roster-b.com/", "roster-b"),
    ]
    mock_crawl.side_effect = lambda *a, **kw: [{"title": "item"}]
    # update_crawled_at fails for the first site.
    mock_update_at.side_effect = [Exception("db write error"), None]

    result = crawl_all_sources(cfg)

    # Both roster sites (plus fallback start_url) were crawled.
    assert mock_crawl.call_count == 3
    assert mock_update_at.call_count == 2
