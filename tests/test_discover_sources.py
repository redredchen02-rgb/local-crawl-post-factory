"""Unit tests for discover_sources (U2).

所有網路呼叫都用 monkeypatch/mock 攔截，不發真實 HTTP 請求。
"""

import io
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cpost.cli.discover_sources import (
    _LinkExtractor,
    _fetch_links,
    _head_ok,
    _load_yaml_sources,
    _run,
    _yaml_domains,
    discover,
)
from cpost.core import site_roster
from cpost.core.validators import is_safe_external_host


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _make_yaml(
    sources: list[dict[str, str]],
    tmpdir: str,
    top_start_url: str = "",
    top_source_id: str = "",
) -> str:
    path = os.path.join(tmpdir, "webui.yaml")
    data: dict = {"sources": sources}
    if top_start_url:
        data["start_url"] = top_start_url
    if top_source_id:
        data["source_id"] = top_source_id
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return path


def _make_roster(tmpdir: str) -> str:
    return os.path.join(tmpdir, "roster.db")


def _html_with_links(*external_urls: str) -> bytes:
    """建立含有外部連結的最小 HTML。"""
    links = "".join(f'<a href="{u}">link</a>' for u in external_urls)
    return f"<html><body>{links}</body></html>".encode()


# ---------------------------------------------------------------------------
# is_safe_external_host
# ---------------------------------------------------------------------------

class TestIsSafeExternalHost:
    def test_private_ip_10x(self) -> None:
        with patch("cpost.core.validators.socket.gethostbyname", return_value="10.0.0.1"):
            assert is_safe_external_host("internal.corp") is False

    def test_private_ip_192168(self) -> None:
        with patch("cpost.core.validators.socket.gethostbyname", return_value="192.168.1.1"):
            assert is_safe_external_host("router.local") is False

    def test_loopback(self) -> None:
        with patch("cpost.core.validators.socket.gethostbyname", return_value="127.0.0.1"):
            assert is_safe_external_host("localhost") is False

    def test_link_local(self) -> None:
        with patch("cpost.core.validators.socket.gethostbyname", return_value="169.254.0.1"):
            assert is_safe_external_host("link.local") is False

    def test_public_ip(self) -> None:
        with patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"):
            assert is_safe_external_host("example.com") is True

    def test_unresolvable_returns_false(self) -> None:
        import socket
        with patch("cpost.core.validators.socket.gethostbyname",
                   side_effect=socket.gaierror("no such host")):
            assert is_safe_external_host("no-such-host.invalid") is False

    def test_empty_string(self) -> None:
        assert is_safe_external_host("") is False


# ---------------------------------------------------------------------------
# _LinkExtractor
# ---------------------------------------------------------------------------

class TestLinkExtractor:
    def test_extracts_absolute_links(self) -> None:
        html = '<a href="https://external.com/page">x</a>'
        ext = _LinkExtractor("https://seed.com/")
        ext.feed(html)
        assert "https://external.com/page" in ext.links

    def test_resolves_relative_links(self) -> None:
        html = '<a href="/about">about</a>'
        ext = _LinkExtractor("https://seed.com/")
        ext.feed(html)
        assert "https://seed.com/about" in ext.links

    def test_ignores_mailto(self) -> None:
        html = '<a href="mailto:admin@example.com">mail</a>'
        ext = _LinkExtractor("https://seed.com/")
        ext.feed(html)
        assert ext.links == []


# ---------------------------------------------------------------------------
# Happy path: 候選域名寫入 roster
# ---------------------------------------------------------------------------

class TestDiscoverHappyPath:
    def _mock_urlopen_factory(
        self, seed_host: str, external_host: str
    ):
        """回傳一個 urlopen mock：首頁含外部連結，友鏈頁 404。"""
        homepage_html = _html_with_links(f"https://{external_host}/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                return mock_resp
            if f"{seed_host}" in url and all(p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]):
                mock_resp = MagicMock()
                mock_resp.read.return_value = homepage_html
                mock_resp.headers.get_content_charset.return_value = "utf-8"
                mock_resp.status = 200
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                return mock_resp
            # 友鏈頁 404
            raise Exception("404")

        return fake_urlopen

    def test_candidate_written_to_roster(self, tmp_path: Any) -> None:
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}], str(tmp_path)
        )
        roster_path = str(tmp_path / "roster.db")

        with (
            patch("cpost.cli.discover_sources.urlopen",
                  side_effect=self._mock_urlopen_factory("seed.com", "newsite.org")),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "newsite.org" in result
        rows = site_roster.list_by_tier(roster_path, site_roster.CANDIDATE)
        domains = [r["domain"] for r in rows]
        assert "newsite.org" in domains

    def test_dry_run_no_roster_write(self, tmp_path: Any) -> None:
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}], str(tmp_path)
        )
        roster_path = str(tmp_path / "roster.db")

        with (
            patch("cpost.cli.discover_sources.urlopen",
                  side_effect=self._mock_urlopen_factory("seed.com", "newsite.org")),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=True,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "newsite.org" in result
        # dry_run 不寫入任何候選（roster 中應無 candidate 行）
        rows = site_roster.list_by_tier(roster_path, site_roster.CANDIDATE)
        assert rows == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestDiscoverEdgeCases:
    def test_seed_homepage_404_skipped(self, tmp_path: Any) -> None:
        """種子站首頁 404 → 不 crash，其他種子繼續。"""
        yaml_path = _make_yaml(
            [
                {"start_url": "https://broken.com/"},
                {"start_url": "https://good.com/"},
            ],
            str(tmp_path),
        )
        roster_path = str(tmp_path / "roster.db")

        good_html = _html_with_links("https://discovered.org/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "broken.com" in url:
                raise Exception("connection refused")
            if "good.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = good_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "discovered.org" in result

    def test_already_in_yaml_skipped(self, tmp_path: Any) -> None:
        """候選域名已在 YAML sources → skip。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}, {"start_url": "https://already.com/"}],
            str(tmp_path),
        )
        roster_path = str(tmp_path / "roster.db")

        html = _html_with_links("https://already.com/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "seed.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = html
                m.headers.get_content_charset.return_value = "utf-8"
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        stderr_buf = io.StringIO()
        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=stderr_buf,
            )

        assert "already.com" not in result
        assert "already-known" in stderr_buf.getvalue()

    def test_head_timeout_skipped(self, tmp_path: Any) -> None:
        """候選域名 HEAD timeout → skip。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}], str(tmp_path)
        )
        roster_path = str(tmp_path / "roster.db")

        html = _html_with_links("https://timeout-site.org/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                raise TimeoutError("timed out")
            if "seed.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = html
                m.headers.get_content_charset.return_value = "utf-8"
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        stderr_buf = io.StringIO()
        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=stderr_buf,
            )

        assert "timeout-site.org" not in result
        assert "head-failed" in stderr_buf.getvalue()

    def test_friend_pages_all_404_no_crash(self, tmp_path: Any) -> None:
        """友鏈頁全 404 → 只從首頁抓，不 crash。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}], str(tmp_path)
        )
        roster_path = str(tmp_path / "roster.db")

        homepage_html = _html_with_links("https://from-homepage.org/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            # 友鏈頁全 404
            if any(p in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]):
                raise Exception("404")
            # 首頁
            m = MagicMock()
            m.read.return_value = homepage_html
            m.headers.get_content_charset.return_value = "utf-8"
            m.status = 200
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            return m

        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "from-homepage.org" in result

    def test_private_ip_skipped(self, tmp_path: Any) -> None:
        """is_safe_external_host 返回 False（私有 IP）→ skip。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}], str(tmp_path)
        )
        roster_path = str(tmp_path / "roster.db")

        html = _html_with_links("https://internal.corp/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "seed.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = html
                m.headers.get_content_charset.return_value = "utf-8"
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        def fake_gethostbyname(host: str) -> str:
            if "internal" in host:
                return "10.0.0.5"
            return "1.2.3.4"

        stderr_buf = io.StringIO()
        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", side_effect=fake_gethostbyname),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=stderr_buf,
            )

        assert "internal.corp" not in result
        assert "private-or-unresolvable" in stderr_buf.getvalue()

    def test_max_candidates_per_seed(self, tmp_path: Any) -> None:
        """達到 --max-candidates-per-seed → 停止此種子。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/"}], str(tmp_path)
        )
        roster_path = str(tmp_path / "roster.db")

        # 首頁包含 5 個外部連結
        external_links = [f"https://site{i}.org/" for i in range(5)]
        html = _html_with_links(*external_links)

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "seed.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = html
                m.headers.get_content_charset.return_value = "utf-8"
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=2,   # 限制 2
                max_total=50,
                stderr=io.StringIO(),
            )

        assert len(result) <= 2

    def test_missing_yaml_exits_nonzero(self, tmp_path: Any) -> None:
        """sources-yaml 不存在 → sys.exit(1)。"""
        with pytest.raises(SystemExit) as exc_info:
            discover(
                sources_yaml=str(tmp_path / "nonexistent.yaml"),
                roster_path=str(tmp_path / "roster.db"),
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# U1: 頂層 start_url 種子補漏
# ---------------------------------------------------------------------------

class TestDiscoverTopLevelSeed:
    """U1: 頂層 start_url 補進種子清單，_yaml_domains 也收錄其 hostname。"""

    def _mock_urlopen_factory(self, seed_host: str, external_host: str):
        homepage_html = _html_with_links(f"https://{external_host}/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if seed_host in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = homepage_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        return fake_urlopen

    def test_top_level_only_discovers_candidates(self, tmp_path: Any) -> None:
        """YAML 只有頂層 start_url（無 sources: 清單）→ 頂層站被當種子，能探索外部候選。"""
        yaml_path = _make_yaml(
            [],
            str(tmp_path),
            top_start_url="https://topseed.com/",
            top_source_id="topseed",
        )
        roster_path = str(tmp_path / "roster.db")

        with (
            patch("cpost.cli.discover_sources.urlopen",
                  side_effect=self._mock_urlopen_factory("topseed.com", "found.org")),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "found.org" in result
        rows = site_roster.list_by_tier(roster_path, site_roster.CANDIDATE)
        assert "found.org" in [r["domain"] for r in rows]

    def test_top_level_plus_sources_both_seeded(self, tmp_path: Any) -> None:
        """頂層 + sources: 清單各一個站 → 兩個站都被當種子。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://secondary.com/", "source_id": "secondary"}],
            str(tmp_path),
            top_start_url="https://primary.com/",
            top_source_id="primary",
        )
        roster_path = str(tmp_path / "roster.db")

        primary_html = _html_with_links("https://from-primary.org/")
        secondary_html = _html_with_links("https://from-secondary.org/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "primary.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = primary_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "secondary.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = secondary_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "from-primary.org" in result
        assert "from-secondary.org" in result

    def test_top_level_host_not_emitted_as_candidate(self, tmp_path: Any) -> None:
        """Critical: 頂層 hostname 已在 known 集合 → 不被自己的外連誤列為 candidate。"""
        # sources: 清單的站連到頂層站 primary.com → 應被 already-known skip
        yaml_path = _make_yaml(
            [{"start_url": "https://secondary.com/", "source_id": "secondary"}],
            str(tmp_path),
            top_start_url="https://primary.com/",
            top_source_id="primary",
        )
        roster_path = str(tmp_path / "roster.db")

        # secondary.com 的首頁連到 primary.com（頂層站本身）
        secondary_html = _html_with_links("https://primary.com/some-page")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "secondary.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = secondary_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        stderr_buf = io.StringIO()
        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=stderr_buf,
            )

        assert "primary.com" not in result
        assert "already-known" in stderr_buf.getvalue()

    def test_top_level_same_host_as_sources_deduped(self, tmp_path: Any) -> None:
        """頂層 start_url 和 sources: 清單中有相同 host → 去重後只爬一次。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://same.com/", "source_id": "same-secondary"}],
            str(tmp_path),
            top_start_url="https://same.com/",
            top_source_id="same",
        )
        roster_path = str(tmp_path / "roster.db")

        crawl_count = {"n": 0}
        homepage_html = _html_with_links("https://external.org/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "same.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                crawl_count["n"] += 1
                m = MagicMock()
                m.read.return_value = homepage_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        # same.com 的首頁只應被爬一次（頂層去重排除了 sources: 清單中的重複條目）
        assert crawl_count["n"] == 1

    def test_top_level_empty_no_crash(self, tmp_path: Any) -> None:
        """頂層 start_url 為空 → 不 crash，只用 sources: 清單中的種子。"""
        yaml_path = _make_yaml(
            [{"start_url": "https://seed.com/", "source_id": "seed"}],
            str(tmp_path),
        )
        roster_path = str(tmp_path / "roster.db")
        homepage_html = _html_with_links("https://found.org/")

        def fake_urlopen(req: Any, timeout: int = 10) -> Any:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"
            if method == "HEAD":
                m = MagicMock()
                m.status = 200
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            if "seed.com" in url and all(
                p not in url for p in ["/links/", "/friends/", "/tuijian/", "/link.html"]
            ):
                m = MagicMock()
                m.read.return_value = homepage_html
                m.headers.get_content_charset.return_value = "utf-8"
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                return m
            raise Exception("404")

        with (
            patch("cpost.cli.discover_sources.urlopen", side_effect=fake_urlopen),
            patch("cpost.cli.discover_sources.time.sleep"),
            patch("cpost.core.validators.socket.gethostbyname", return_value="1.2.3.4"),
        ):
            result = discover(
                sources_yaml=yaml_path,
                roster_path=roster_path,
                dry_run=False,
                max_per_seed=20,
                max_total=50,
                stderr=io.StringIO(),
            )

        assert "found.org" in result
