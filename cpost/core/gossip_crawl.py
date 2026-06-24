"""On-demand gossip URL crawl: fetch → normalize → ingest into shared library.

Called by the /gossip-materials WebUI route via the jobs system. Does NOT
re-run cluster/score; newly ingested articles join the next prep cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from cpost.core import library
from cpost.cli import normalize_items
from cpost.core.pipeline import crawl_items


def crawl_url(url: str, cfg: dict,
              progress_cb: Callable[[str], object] | None = None,
              now: str = "") -> dict:
    """Crawl *url*, normalize, and ingest into the shared library.

    Returns ``{"item_count": int, "failed": int}``.  Updates gossip_urls crawl
    status on completion or failure.  Progress strings are forwarded to
    *progress_cb* when provided.
    """
    def _report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    source_id = "user:" + urlparse(url).netloc

    parsed_path = urlparse(url).path
    max_pages = 50 if parsed_path in ("", "/") else 1

    source_cfg = {
        **cfg,
        "start_url": url,
        "source_id": source_id,
        "max_pages": max_pages,
        # Gossip crawls must not filter by the main pipeline's site-specific
        # item_regex (e.g. "archives/\d+"). Emit every page with a title.
        "item_regex": "",
    }

    def _progress_cb(snap: object) -> None:
        if not progress_cb:
            return
        if isinstance(snap, dict):
            r = snap.get("responses", 0)
            i = snap.get("items", 0)
            last = snap.get("last_title") or snap.get("last_url") or ""
            progress_cb(f"已抓 {r} 頁，收到 {i} 篇" + (f"：{last[:60]}" if last else ""))
        else:
            progress_cb(str(snap))

    try:
        raw = crawl_items(source_cfg, progress_cb=_progress_cb)
        _report(f"爬取完成：{len(raw)} 篇")
    except Exception as exc:  # noqa: BLE001
        with library.connect(cfg["state_path"]) as conn:
            library.update_gossip_crawl_status(
                conn, url, status="failed", error_msg=str(exc), now=now)
        raise

    normalized: list[dict] = []
    failed_count = 0
    for item in raw:
        try:
            normalized.append(normalize_items.normalize_one(item))
        except Exception:  # noqa: BLE001
            failed_count += 1

    with library.connect(cfg["state_path"]) as conn:
        for item in normalized:
            library.upsert(
                conn,
                canonical_url=item["canonical_url"],
                title=item["title"],
                now=now,
                source_id=source_id,
                url=item.get("url"),
                source_text=item.get("source_text"),
                description=item.get("description"),
                published_at=item.get("published_at"),
                discovered_at=item.get("discovered_at"),
            )
        library.update_gossip_crawl_status(
            conn, url,
            status="done",
            item_count=len(normalized),
            now=now,
        )

    _report(f"落庫 {len(normalized)} 筆，失敗 {failed_count} 筆")
    return {"item_count": len(normalized), "failed": failed_count}
