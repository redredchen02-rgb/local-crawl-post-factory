"""discover-sources: 從現有 YAML sources 的首頁爬友鏈，寫入 roster 為 candidate。

流程：
1. 讀 webui.yaml 取得所有種子站的 start_url
2. 對每個種子站抓首頁 + 常見友鏈路徑（/links/ 等）
3. 從 HTML 的 <a href> 抽取外部域名
4. SSRF 防護：呼叫 is_safe_external_host() 過濾私有 IP
5. 過濾已知域名（YAML 已有、roster 已有）
6. HTTP HEAD 確認可通
7. 寫入 roster（tier=candidate），或 --dry-run 只印不寫
"""

import argparse
import sys
import time
from html.parser import HTMLParser
from typing import IO
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import yaml

from cpost.core import site_roster
from cpost.core.url_utils import host_of, make_source_id
from cpost.core.validators import is_safe_external_host

# 友鏈頁常見路徑
_FRIEND_PATHS = ["/links/", "/friends/", "/tuijian/", "/link.html"]

_UA = "Mozilla/5.0 (compatible; cpost-discover/0.1)"


# ---------------------------------------------------------------------------
# HTML 連結抽取
# ---------------------------------------------------------------------------

class _LinkExtractor(HTMLParser):
    """從 HTML 中抽取所有 <a href="..."> 的絕對 URL。"""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                absolute = urljoin(self.base_url, value.strip())
                parts = urlsplit(absolute)
                if parts.scheme in ("http", "https") and parts.hostname:
                    self.links.append(absolute)


def _fetch_links(url: str, timeout: int = 10) -> list[str]:
    """GET *url*，回傳頁面中所有絕對 http/https 連結。

    遇到 HTTP 4xx/5xx、網路錯誤、timeout → 回傳空 list（不 raise）。
    """
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            charset = _guess_charset(resp.headers.get_content_charset())
            body = resp.read().decode(charset, errors="replace")
    except Exception:
        # 任何網路/HTTP/timeout 錯誤都視為無法取得此頁面，跳過
        return []
    extractor = _LinkExtractor(url)
    try:
        extractor.feed(body)
    except Exception:  # HTMLParser 遇到畸形 HTML 可能拋出
        pass
    return extractor.links


def _guess_charset(charset: str | None) -> str:
    return charset if charset else "utf-8"


def _head_ok(url: str, timeout: int = 5) -> bool:
    """HEAD 請求確認 URL 可通（非 4xx/5xx）。timeout 視為不通。"""
    req = Request(url, method="HEAD", headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            status: int = resp.status
            return status < 400
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 核心邏輯
# ---------------------------------------------------------------------------

def _load_yaml_sources(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"[ERROR] sources-yaml not found: {path}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as exc:
        print(f"[ERROR] YAML parse error in {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _yaml_domains(data: dict[str, object]) -> set[str]:
    """從 YAML 中收集所有已知的 hostname（種子站自己的域名）。"""
    domains: set[str] = set()
    # 頂層 start_url（webui.yaml 兩段式架構的主來源）
    top_url = data.get("start_url", "")
    if isinstance(top_url, str) and top_url:
        h = host_of(top_url)
        if h:
            domains.add(h)
    # sources: 清單
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return domains
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = src.get("start_url", "")
        if isinstance(url, str) and url:
            h = host_of(url)
            if h:
                domains.add(h)
    return domains


def _roster_domains(roster_path: str) -> set[str]:
    """讀 roster 中所有已知域名，所有 tier 都算。"""
    domains: set[str] = set()
    for tier in (
        site_roster.CANDIDATE, site_roster.MONITORED, site_roster.ACTIVE,
        site_roster.MIRROR, site_roster.FAILED, site_roster.INACTIVE,
    ):
        for row in site_roster.list_by_tier(roster_path, tier):
            d = row.get("domain")
            if isinstance(d, str) and d:
                domains.add(d)
    return domains


def _log(msg: str, stderr: IO[str] = sys.stderr) -> None:
    print(msg, file=stderr)


def discover(
    sources_yaml: str,
    roster_path: str,
    dry_run: bool,
    max_per_seed: int,
    max_total: int,
    stderr: IO[str] = sys.stderr,
) -> list[str]:
    """執行探索，回傳新增的候選域名列表。"""
    data = _load_yaml_sources(sources_yaml)
    yaml_doms = _yaml_domains(data)
    roster_doms = _roster_domains(roster_path)
    known = yaml_doms | roster_doms

    sources = data.get("sources", [])
    if not isinstance(sources, list):
        sources = []

    # Build seeds: prepend top-level start_url as first seed, dedup by host
    top_url = str(data.get("start_url", "") or "")
    top_sid = str(data.get("source_id", "") or "")
    seeds: list[dict] = []
    seen_hosts: set[str] = set()
    if top_url:
        top_host = host_of(top_url)
        if top_host:
            seeds.append({"start_url": top_url, "source_id": top_sid})
            seen_hosts.add(top_host)
    for src in sources:
        if not isinstance(src, dict):
            continue
        src_url = src.get("start_url", "")
        if not isinstance(src_url, str) or not src_url:
            continue
        src_host = host_of(src_url)
        if src_host and src_host not in seen_hosts:
            seeds.append(src)
            seen_hosts.add(src_host)

    if not seeds:
        _log("[WARN] no seeds found in YAML (neither top-level start_url nor sources list)", stderr)

    total_added: list[str] = []
    total_count = 0

    for src in seeds:
        if not isinstance(src, dict):
            continue
        seed_url = src.get("start_url", "")
        if not isinstance(seed_url, str) or not seed_url:
            continue

        seed_host = host_of(seed_url)
        if not seed_host:
            continue

        # 首頁 + 友鏈頁路徑
        urls_to_try = [seed_url] + [
            urljoin(seed_url.rstrip("/") + "/", p.lstrip("/"))
            for p in _FRIEND_PATHS
        ]

        per_seed_count = 0

        for page_url in urls_to_try:
            if total_count >= max_total:
                break
            if per_seed_count >= max_per_seed:
                break

            links = _fetch_links(page_url)
            time.sleep(0.5)

            for href in links:
                if total_count >= max_total or per_seed_count >= max_per_seed:
                    break

                cand_host = host_of(href)
                if not cand_host or cand_host == seed_host:
                    continue

                # 已知域名（YAML or roster）
                if cand_host in known:
                    _log(f"[SKIP] {cand_host} already-known", stderr)
                    continue

                # SSRF 防護
                if not is_safe_external_host(cand_host):
                    _log(f"[SKIP] {cand_host} private-or-unresolvable", stderr)
                    known.add(cand_host)  # 避免重複 log
                    continue

                # HTTP HEAD 存活檢查
                if not _head_ok(f"https://{cand_host}"):
                    _log(f"[SKIP] {cand_host} head-failed", stderr)
                    known.add(cand_host)
                    continue

                # 通過所有檢查
                _log(f"[CANDIDATE] {cand_host}", stderr)
                known.add(cand_host)  # 去重，避免跨頁面重複處理

                if not dry_run:
                    site_roster.upsert_site(
                        roster_path,
                        cand_host,
                        start_url=f"https://{cand_host}/",
                        source_id=make_source_id(cand_host),
                        tier=site_roster.CANDIDATE,
                    )

                total_added.append(cand_host)
                per_seed_count += 1
                total_count += 1

    return total_added


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _run(args: argparse.Namespace) -> int:
    candidates = discover(
        sources_yaml=args.sources_yaml,
        roster_path=args.roster_path,
        dry_run=args.dry_run,
        max_per_seed=args.max_candidates_per_seed,
        max_total=args.max_total_candidates,
    )
    action = "would add" if args.dry_run else "added"
    print(
        f"[discover-sources] {action} {len(candidates)} candidate(s): "
        + (", ".join(candidates) if candidates else "(none)"),
        file=sys.stderr,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="discover-sources",
        description=(
            "Crawl seed sites from webui.yaml, extract external domains, "
            "and write them to the site roster as candidates."
        ),
    )
    parser.add_argument(
        "--sources-yaml",
        required=True,
        metavar="PATH",
        help="path to webui.yaml (must contain a 'sources' list)",
    )
    parser.add_argument(
        "--roster-path",
        required=True,
        metavar="PATH",
        help="path to the SQLite roster file (created if absent)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="print candidates to stderr without writing to the roster",
    )
    parser.add_argument(
        "--max-candidates-per-seed",
        type=int,
        default=20,
        metavar="INT",
        dest="max_candidates_per_seed",
        help="maximum candidates to collect per seed site (default: 20)",
    )
    parser.add_argument(
        "--max-total-candidates",
        type=int,
        default=50,
        metavar="INT",
        dest="max_total_candidates",
        help="maximum total candidates across all seeds (default: 50)",
    )
    args = parser.parse_args()
    sys.exit(_run(args))


if __name__ == "__main__":
    main()
