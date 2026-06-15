"""In-process pipeline orchestrator (single source of pipeline logic).

Reuses the existing CLI stage functions directly instead of shelling out, so the
WebUI and the CLI share one implementation. crawl stays in its own subprocess
(Scrapy reactor cannot restart in-process) via crawl_posts.crawl_items.
"""

from pathlib import Path

from core import state, url_utils, runs
from src import (
    normalize_items,
    dedupe_posts,
    render_caption,
    select_cover,
    watermark_cover,
    build_manifest,
    crawl_posts,
)

COVER_TIMEOUT_SEC = 20


def crawl_items(webui_cfg: dict) -> list:
    """Crawl the configured start_url and return raw crawled items."""
    opts = dict(crawl_posts.CONFIG_DEFAULTS)
    opts.update({
        "item_regex": webui_cfg.get("item_regex", ""),
        "deny_regex": webui_cfg.get("deny_regex", ""),
        "limit": int(webui_cfg.get("limit", 30)),
        "download_delay": float(webui_cfg.get("download_delay", 0.0)),
        "concurrency": int(webui_cfg.get("concurrency", 8)),
        "source_id": webui_cfg.get("source_id", ""),
        "start_urls": [webui_cfg["start_url"]],
    })
    return crawl_posts.crawl_items(opts)


def run_pipeline(items, webui_cfg: dict, progress_cb=None) -> dict:
    """Run normalize→dedupe→caption→cover→watermark→build over ``items``.

    Returns {"built": [...], "failed": [...], "skipped": int}. A single bad item
    is recorded under "failed" and never aborts the batch.
    """
    def _report(msg):
        if progress_cb:
            progress_cb(msg)

    template_cfg = render_caption.load_template(webui_cfg["template_path"])
    wm_cfg = watermark_cover.load_config(webui_cfg["watermark_config"])
    download_dir = Path(webui_cfg["download_dir"])
    out_dir = webui_cfg["out_dir"]
    audit_log = webui_cfg["audit_log"]

    built, failed = [], []

    # Stage 1: normalize (per-item, so one bad record doesn't kill the batch).
    normalized = []
    for raw in items:
        try:
            normalized.append(normalize_items._normalize(raw))
        except Exception as exc:  # noqa: BLE001
            failed.append({"item": raw, "stage": "normalize", "error": str(exc)})
    _report(f"normalized {len(normalized)} item(s)")

    # Stage 2: dedupe against published state (R9).
    before = len(normalized)
    with state.connect(webui_cfg["state_path"]) as conn:
        deduped = list(dedupe_posts._dedupe(normalized, conn))
    skipped = before - len(deduped)
    _report(f"deduped: {len(deduped)} new, {skipped} skipped")

    # Stages 3-6: caption → cover → watermark → build, per item.
    for rec in deduped:
        title = rec.get("title", "")
        try:
            caption = render_caption._render(rec, template_cfg)
            rec["caption"] = caption
            rec["content_hash"] = url_utils.content_hash(
                str(rec.get("canonical_url", "")), str(title), caption)
            rec = select_cover._select(rec, download_dir, COVER_TIMEOUT_SEC)
            rec = watermark_cover._watermark(rec, wm_cfg)
            manifest_path = build_manifest._build(rec, out_dir, audit_log)
            post_id = Path(manifest_path).parent.name
            built.append({"post_id": post_id, "title": title,
                          "manifest_path": manifest_path})
            runs.record_run(webui_cfg["state_path"], stage="build", post_id=post_id,
                            status="ok", detail=title)
            _report(f"built {post_id}")
        except Exception as exc:  # noqa: BLE001
            failed.append({"title": title, "stage": "build", "error": str(exc)})
            runs.record_run(webui_cfg["state_path"], stage="build", post_id=None,
                            status="failed", detail=title, error=str(exc))
            _report(f"failed: {title}: {exc}")

    return {"built": built, "failed": failed, "skipped": skipped}
