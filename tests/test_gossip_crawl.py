"""Tests for cpost.core.gossip_crawl."""
import pytest
from unittest.mock import MagicMock, patch

from cpost.core import gossip_crawl, library

NOW = "2026-06-24T10:00:00+00:00"


def _cfg(tmp_path):
    return {"state_path": str(tmp_path / "state.sqlite")}


def _raw_item(url="https://foo.com/article/1", title="Test Title"):
    return {
        "url": url,
        "title": title,
        "canonical_url": url,
        "source_id": "",
        "source_text": "body text",
        "description": "desc",
        "published_at": "2026-06-24T09:00:00+00:00",
        "discovered_at": NOW,
    }


def test_crawl_url_happy_path(tmp_path):
    cfg = _cfg(tmp_path)
    raw = [_raw_item("https://foo.com/a/1"), _raw_item("https://foo.com/a/2", "Title 2")]
    with (
        patch("cpost.core.gossip_crawl.crawl_items", return_value=raw) as mock_crawl,
        patch("cpost.core.gossip_crawl.normalize_items.normalize_one",
              side_effect=lambda x: x) as mock_norm,
    ):
        with library.connect(cfg["state_path"]) as conn:
            library.submit_gossip_url(conn, "https://foo.com/a/1", None, NOW)
        result = gossip_crawl.crawl_url("https://foo.com/a/1", cfg, now=NOW)

    assert result["item_count"] == 2
    assert result["failed"] == 0
    # source_id must have been passed to crawl_items as 'user:foo.com'
    call_cfg = mock_crawl.call_args[0][0]
    assert call_cfg["source_id"] == "user:foo.com"
    assert call_cfg["start_url"] == "https://foo.com/a/1"


def test_crawl_url_deep_path_uses_single_page(tmp_path):
    cfg = _cfg(tmp_path)
    with (
        patch("cpost.core.gossip_crawl.crawl_items", return_value=[]) as mock_crawl,
    ):
        with library.connect(cfg["state_path"]) as conn:
            library.submit_gossip_url(conn, "https://foo.com/2024/article", None, NOW)
        gossip_crawl.crawl_url("https://foo.com/2024/article", cfg, now=NOW)

    call_cfg = mock_crawl.call_args[0][0]
    assert call_cfg["max_pages"] == 1


def test_crawl_url_root_path_uses_multi_page(tmp_path):
    cfg = _cfg(tmp_path)
    with (
        patch("cpost.core.gossip_crawl.crawl_items", return_value=[]) as mock_crawl,
    ):
        with library.connect(cfg["state_path"]) as conn:
            library.submit_gossip_url(conn, "https://foo.com/", None, NOW)
        gossip_crawl.crawl_url("https://foo.com/", cfg, now=NOW)

    call_cfg = mock_crawl.call_args[0][0]
    assert call_cfg["max_pages"] == 50


def test_crawl_url_overrides_item_regex(tmp_path):
    """item_regex from webui.yaml must not filter gossip crawl results."""
    cfg = {**_cfg(tmp_path), "item_regex": r"archives/\d+"}
    with (
        patch("cpost.core.gossip_crawl.crawl_items", return_value=[]) as mock_crawl,
    ):
        with library.connect(cfg["state_path"]) as conn:
            library.submit_gossip_url(conn, "https://foo.com/", None, NOW)
        gossip_crawl.crawl_url("https://foo.com/", cfg, now=NOW)

    call_cfg = mock_crawl.call_args[0][0]
    assert call_cfg["item_regex"] == ""


def test_crawl_url_progress_cb_receives_strings(tmp_path):
    """Progress callback must receive human-readable strings, not raw dicts."""
    cfg = _cfg(tmp_path)
    messages: list[object] = []
    snap = {"responses": 5, "items": 2, "last_title": "Some Page", "last_url": "https://foo.com/a"}

    def _fake_crawl(source_cfg, progress_cb=None, **_kw):
        if progress_cb:
            progress_cb(snap)
        return []

    with patch("cpost.core.gossip_crawl.crawl_items", side_effect=_fake_crawl):
        with library.connect(cfg["state_path"]) as conn:
            library.submit_gossip_url(conn, "https://foo.com/", None, NOW)
        gossip_crawl.crawl_url("https://foo.com/", cfg,
                               progress_cb=messages.append, now=NOW)

    assert messages
    assert all(isinstance(m, str) for m in messages), "progress_cb must receive strings"
    assert any("5" in m for m in messages), "response count should appear in message"


def test_crawl_url_normalize_failure_does_not_abort(tmp_path):
    cfg = _cfg(tmp_path)
    raw = [_raw_item("https://foo.com/1"), _raw_item("https://foo.com/2")]

    def _norm_one(item):
        if item["url"] == "https://foo.com/1":
            raise ValueError("bad item")
        return item

    with (
        patch("cpost.core.gossip_crawl.crawl_items", return_value=raw),
        patch("cpost.core.gossip_crawl.normalize_items.normalize_one",
              side_effect=_norm_one),
    ):
        with library.connect(cfg["state_path"]) as conn:
            library.submit_gossip_url(conn, "https://foo.com/1", None, NOW)
        result = gossip_crawl.crawl_url("https://foo.com/1", cfg, now=NOW)

    assert result["failed"] == 1
    assert result["item_count"] == 1


def test_crawl_url_crawl_failure_marks_status_failed(tmp_path):
    cfg = _cfg(tmp_path)
    with library.connect(cfg["state_path"]) as conn:
        library.submit_gossip_url(conn, "https://foo.com/", None, NOW)

    with patch("cpost.core.gossip_crawl.crawl_items",
               side_effect=RuntimeError("network error")):
        with pytest.raises(RuntimeError, match="network error"):
            gossip_crawl.crawl_url("https://foo.com/", cfg, now=NOW)

    with library.connect(cfg["state_path"]) as conn:
        rows = library.list_gossip_urls(conn)
    assert rows[0]["crawl_status"] == "failed"
    assert "network error" in rows[0]["error_msg"]
