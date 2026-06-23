"""測試 cpost.cli.health_check_sources (U3)。

涵蓋：
- Happy path：candidate 通過 → MONITORED
- Happy path：monitored 1 次通過 → ACTIVE
- Happy path：candidate 鏡像 → MIRROR
- Edge case：0 items → fail_count +1，tier 不變
- Edge case：fail_count=2 再失敗 → FAILED
- Edge case：active 失敗 3 次 → INACTIVE
- Error path：crawl_items 拋 Exception → 繼續評估其他
- Edge case：--dry-run → roster 不更新
- Edge case：library DB 不存在 → 跳過鏡像偵測
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from cpost.core import site_roster
from cpost.cli import health_check_sources as hcs
from cpost.cli.health_check_sources import run, _compute_mirror_overlap, _is_fresh


# ─── Fixtures & helpers ─────────────────────────────────────────────────────

def _roster(tmp_path: Any) -> str:
    return str(tmp_path / "roster.sqlite")


def _library_db(tmp_path: Any) -> str:
    return str(tmp_path / "library.sqlite")


def _seed_site(
    roster_path: str,
    domain: str = "example.com",
    start_url: str = "https://example.com/",
    tier: str = site_roster.CANDIDATE,
    fail_count: int = 0,
    monitored_ok_count: int = 0,
) -> None:
    """插入一個測試站點，並設好 fail_count/monitored_ok_count。"""
    site_roster.upsert_site(
        roster_path,
        domain,
        start_url,
        source_id=domain,
        tier=tier,
    )
    if fail_count or monitored_ok_count:
        # upsert_site 不帶這兩個 counter，用 update_health 設值
        site_roster.update_health(
            roster_path,
            domain,
            fail_count=fail_count,
            monitored_ok_count=monitored_ok_count,
            last_checked_at="2026-06-01T00:00:00+00:00",
        )


def _fresh_items(count: int = 5) -> list[dict[str, Any]]:
    """生成 count 個帶新鮮 published_at 的 mock crawl items。"""
    now = datetime.now(timezone.utc)
    return [
        {
            "url": f"https://example.com/post/{i}",
            "canonical_url": f"https://example.com/post/{i}",
            "title": f"Test Post {i}",
            "published_at": (now - timedelta(hours=i)).isoformat(),
        }
        for i in range(count)
    ]


def _stale_items(count: int = 5) -> list[dict[str, Any]]:
    """生成帶舊 published_at（>72h）的 items。"""
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    return [
        {
            "url": f"https://example.com/old/{i}",
            "canonical_url": f"https://example.com/old/{i}",
            "title": f"Old Post {i}",
            "published_at": (old - timedelta(hours=i)).isoformat(),
        }
        for i in range(count)
    ]


# ─── Unit tests: _is_fresh ──────────────────────────────────────────────────

def test_is_fresh_with_recent_published_at() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    assert _is_fresh(recent, 0) is True


def test_is_fresh_with_old_published_at() -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    assert _is_fresh(old, 5) is False


def test_is_fresh_no_published_at_high_count() -> None:
    # 無 published_at，item_count ≥ 3 → fresh
    assert _is_fresh(None, 3) is True
    assert _is_fresh(None, 5) is True


def test_is_fresh_no_published_at_low_count() -> None:
    assert _is_fresh(None, 2) is False
    assert _is_fresh(None, 0) is False


# ─── Unit tests: _compute_mirror_overlap ────────────────────────────────────

def test_mirror_overlap_full() -> None:
    items = [
        {"url": "https://example.com/post/1"},
        {"url": "https://example.com/post/2"},
    ]
    library_urls = {
        "https://example.com/post/1",
        "https://example.com/post/2",
        "https://other.com/post/3",
    }
    assert _compute_mirror_overlap(items, library_urls) == pytest.approx(1.0)


def test_mirror_overlap_partial() -> None:
    items = [
        {"url": "https://example.com/post/1"},
        {"url": "https://example.com/post/2"},
        {"url": "https://example.com/post/3"},
        {"url": "https://example.com/post/4"},
    ]
    # 2 of 4 exist in library → overlap = 0.5
    library_urls = {
        "https://example.com/post/1",
        "https://example.com/post/2",
    }
    assert _compute_mirror_overlap(items, library_urls) == pytest.approx(0.5)


def test_mirror_overlap_empty_library() -> None:
    items = [{"url": "https://example.com/post/1"}]
    assert _compute_mirror_overlap(items, set()) == pytest.approx(0.0)


def test_mirror_overlap_empty_items() -> None:
    library_urls = {"https://example.com/post/1"}
    assert _compute_mirror_overlap([], library_urls) == pytest.approx(0.0)


# ─── Integration tests: run() with mock crawl_items ─────────────────────────

class TestCandidateToMonitored:
    """Happy path：candidate 通過 → MONITORED。"""

    def test_candidate_passes_upgrades_to_monitored(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.CANDIDATE)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=_fresh_items(5)):
            results = run(roster, lib_db, tiers=["candidate"])

        assert len(results) == 1
        sites = site_roster.list_by_tier(roster, site_roster.MONITORED)
        assert any(s["domain"] == "example.com" for s in sites), \
            f"期望 MONITORED，但現有: {site_roster.list_by_tier(roster, site_roster.CANDIDATE)}"

    def test_candidate_passes_resets_fail_count(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.CANDIDATE,
                   fail_count=1)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=_fresh_items(5)):
            run(roster, lib_db, tiers=["candidate"])

        with site_roster.connect(roster) as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT fail_count FROM sites WHERE domain = ?", ("example.com",)
            ).fetchone()
        assert dict(row)["fail_count"] == 0


class TestMonitoredToActive:
    """Happy path：monitored 1 次通過 → ACTIVE（1-pass promotion）。"""

    def test_monitored_one_pass_upgrades_to_active(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.MONITORED)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=_fresh_items(5)):
            results = run(roster, lib_db, tiers=["monitored"])

        assert len(results) == 1
        active = site_roster.list_by_tier(roster, site_roster.ACTIVE)
        assert any(s["domain"] == "example.com" for s in active), \
            "monitored 1 次通過應立即升為 ACTIVE"


class TestCandidateMirror:
    """Happy path：candidate canonical 重疊 >60% → MIRROR。"""

    def test_candidate_mirror_detected(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="mirror.com", tier=site_roster.CANDIDATE)

        # sample items 的 URL 全部存在於 library_urls → overlap = 1.0
        mirror_items = [
            {"url": f"https://mirror.com/post/{i}",
             "published_at": (datetime.now(timezone.utc)
                              - timedelta(hours=i)).isoformat()}
            for i in range(5)
        ]
        # mock library canonical URLs：包含 mirror.com 的所有 URL
        mirror_library_urls = {f"https://mirror.com/post/{i}" for i in range(5)}

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=mirror_items), \
             patch("cpost.cli.health_check_sources._load_library_canonical_urls",
                   return_value=mirror_library_urls):
            run(roster, lib_db, tiers=["candidate"],
                mirror_overlap_threshold=0.6)

        mirrors = site_roster.list_by_tier(roster, site_roster.MIRROR)
        assert any(s["domain"] == "mirror.com" for s in mirrors), \
            "canonical 100% 重疊應被判定為 MIRROR"

    def test_candidate_mirror_is_mirror_flag_set(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="mirror.com", tier=site_roster.CANDIDATE)

        mirror_items = [
            {"url": f"https://mirror.com/post/{i}",
             "published_at": (datetime.now(timezone.utc)
                              - timedelta(hours=i)).isoformat()}
            for i in range(4)
        ]
        mirror_library_urls = {f"https://mirror.com/post/{i}" for i in range(4)}

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=mirror_items), \
             patch("cpost.cli.health_check_sources._load_library_canonical_urls",
                   return_value=mirror_library_urls):
            run(roster, lib_db, tiers=["candidate"],
                mirror_overlap_threshold=0.6)

        with site_roster.connect(roster) as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT is_mirror FROM sites WHERE domain = ?", ("mirror.com",)
            ).fetchone()
        assert dict(row)["is_mirror"] == 1, "is_mirror flag 應設為 1"


class TestFailCountIncrement:
    """Edge case：0 items → fail_count +1，tier 不變（未到 3 次）。"""

    def test_zero_items_increments_fail_count(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.CANDIDATE,
                   fail_count=0)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=[]):
            run(roster, lib_db, tiers=["candidate"])

        with site_roster.connect(roster) as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT fail_count, tier FROM sites WHERE domain = ?",
                ("example.com",)
            ).fetchone()
        data = dict(row)
        assert data["fail_count"] == 1
        assert data["tier"] == site_roster.CANDIDATE, "未到 3 次不應轉換 tier"

    def test_fail_count_2_then_fail_becomes_failed(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.CANDIDATE,
                   fail_count=2)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=[]):
            run(roster, lib_db, tiers=["candidate"])

        sites = site_roster.list_by_tier(roster, site_roster.FAILED)
        assert any(s["domain"] == "example.com" for s in sites), \
            "fail_count=2 再失敗一次，應轉為 FAILED"


class TestActiveToInactive:
    """Edge case：active 站點失敗 3 次 → INACTIVE。"""

    def test_active_fails_three_times_becomes_inactive(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.ACTIVE,
                   fail_count=2)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=[]):
            run(roster, lib_db, tiers=["active"])

        inactive = site_roster.list_by_tier(roster, site_roster.INACTIVE)
        assert any(s["domain"] == "example.com" for s in inactive), \
            "active fail_count=2 再失敗，應轉為 INACTIVE"


class TestErrorIsolation:
    """Error path：某站 crawl_items 拋 Exception → 繼續評估其他站。"""

    def test_one_site_exception_continues_others(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="bad.com", start_url="https://bad.com/",
                   tier=site_roster.CANDIDATE)
        _seed_site(roster, domain="good.com", start_url="https://good.com/",
                   tier=site_roster.CANDIDATE, fail_count=0)

        call_count = [0]

        def _mock_crawl(opts: dict, **kwargs: Any) -> list:
            call_count[0] += 1
            start_urls = opts.get("start_urls", [])
            if start_urls and "bad.com" in start_urls[0]:
                raise RuntimeError("bad site!")
            return _fresh_items(5)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   side_effect=_mock_crawl):
            results = run(roster, lib_db, tiers=["candidate"])

        # 兩個站點都應被評估到
        assert call_count[0] == 2, "應對每個站點各呼叫一次 crawl_items"
        assert len(results) == 2

        # bad.com 應有 error 記錄
        bad = next(r for r in results if r["domain"] == "bad.com")
        assert "error" in bad

        # good.com 應成功升為 MONITORED
        monitored = site_roster.list_by_tier(roster, site_roster.MONITORED)
        assert any(s["domain"] == "good.com" for s in monitored), \
            "good.com 應在 bad.com 失敗後仍然被升到 MONITORED"


class TestDryRun:
    """Edge case：--dry-run → roster 不更新。"""

    def test_dry_run_does_not_write_roster(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="example.com", tier=site_roster.CANDIDATE)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=_fresh_items(5)):
            run(roster, lib_db, tiers=["candidate"], dry_run=True)

        # Roster 中 tier 應仍為 candidate
        candidates = site_roster.list_by_tier(roster, site_roster.CANDIDATE)
        assert any(s["domain"] == "example.com" for s in candidates), \
            "dry-run 時不應修改 tier"

        monitored = site_roster.list_by_tier(roster, site_roster.MONITORED)
        assert not any(s["domain"] == "example.com" for s in monitored), \
            "dry-run 時不應升為 MONITORED"


class TestLibraryDbMissing:
    """Edge case：library DB 不存在 → 跳過鏡像偵測，is_mirror=False。"""

    def test_missing_library_db_skips_mirror_detection(self, tmp_path: Any) -> None:
        roster = _roster(tmp_path)
        nonexistent_db = str(tmp_path / "nonexistent.sqlite")
        _seed_site(roster, domain="example.com", tier=site_roster.CANDIDATE)

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=_fresh_items(5)):
            results = run(roster, nonexistent_db, tiers=["candidate"])

        assert len(results) == 1
        assert results[0]["is_mirror"] is False, \
            "library DB 不存在時，is_mirror 應為 False"

        # 站點仍應正常升為 MONITORED（非鏡像路徑）
        monitored = site_roster.list_by_tier(roster, site_roster.MONITORED)
        assert any(s["domain"] == "example.com" for s in monitored)


class TestMirrorThreshold:
    """驗證 mirror_overlap_threshold 邊界值行為。"""

    def test_overlap_at_threshold_is_not_mirror(self, tmp_path: Any) -> None:
        """重疊率 == threshold 不觸發鏡像（需 > threshold）。"""
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="borderline.com", tier=site_roster.CANDIDATE)

        # 2/4 = 0.5 overlap，threshold = 0.6 → NOT mirror
        items = [{"url": f"https://borderline.com/post/{i}",
                  "published_at": (datetime.now(timezone.utc)
                                   - timedelta(hours=i)).isoformat()}
                 for i in range(4)]
        library_urls = {
            "https://borderline.com/post/0",
            "https://borderline.com/post/1",
        }  # 2 of 4

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=items), \
             patch("cpost.cli.health_check_sources._load_library_canonical_urls",
                   return_value=library_urls):
            results = run(roster, lib_db, tiers=["candidate"],
                          mirror_overlap_threshold=0.6)

        assert not results[0]["is_mirror"], "0.5 重疊率不應超過 0.6 threshold"

    def test_overlap_above_threshold_is_mirror(self, tmp_path: Any) -> None:
        """重疊率 > threshold → is_mirror=True。"""
        roster = _roster(tmp_path)
        lib_db = _library_db(tmp_path)
        _seed_site(roster, domain="highoverlap.com", tier=site_roster.CANDIDATE)

        # 3/4 = 0.75 overlap, threshold = 0.6 → IS mirror
        items = [{"url": f"https://highoverlap.com/post/{i}",
                  "published_at": (datetime.now(timezone.utc)
                                   - timedelta(hours=i)).isoformat()}
                 for i in range(4)]
        library_urls = {
            "https://highoverlap.com/post/0",
            "https://highoverlap.com/post/1",
            "https://highoverlap.com/post/2",
        }  # 3 of 4 = 0.75

        with patch("cpost.cli.health_check_sources.crawl_posts.crawl_items",
                   return_value=items), \
             patch("cpost.cli.health_check_sources._load_library_canonical_urls",
                   return_value=library_urls):
            results = run(roster, lib_db, tiers=["candidate"],
                          mirror_overlap_threshold=0.6)

        assert results[0]["is_mirror"], "0.75 重疊率應超過 0.6 threshold 被判定為鏡像"
        assert results[0]["overlap"] == pytest.approx(0.75)
