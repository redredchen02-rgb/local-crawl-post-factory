"""In-process pipeline orchestrator (single source of pipeline logic).

Reuses the existing CLI stage functions directly instead of shelling out, so the
WebUI and the CLI share one implementation. crawl stays in its own subprocess
(Scrapy reactor cannot restart in-process) via crawl_posts.crawl_items.
"""

from pathlib import Path

from core import state, runs
from core.errors import ExternalError, ValidationError
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


def crawl_items(webui_cfg: dict, progress_cb=None) -> list:
    """Crawl the configured start_url and return raw crawled items.

    When *progress_cb* is given, the subprocess crawl calls it during
    execution with ``{responses, items, last_url, last_title}`` snapshots
    (every ~0.5 s while the child is alive).
    """
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
    return crawl_posts.crawl_items(opts, progress_cb=progress_cb)


def run_pipeline(items, webui_cfg: dict, progress_cb=None) -> dict:
    """Run normalize→dedupe→caption→cover→watermark→build over ``items``.

    Returns {"built": [...], "failed": [...], "skipped": int}. A single bad item
    is recorded under "failed" and never aborts the batch.
    """
    def _report(msg):
        if progress_cb:
            progress_cb(msg)

    def _error_class(exc):
        # ValidationError = bad data (expected, skip the item); anything else is
        # an unexpected system fault worth distinguishing for observability.
        return "validation" if isinstance(exc, ValidationError) else "system"

    template_cfg = render_caption.load_template(webui_cfg["template_path"])
    wm_cfg = watermark_cover.load_config(webui_cfg["watermark_config"])
    download_dir = Path(webui_cfg["download_dir"])
    out_dir = webui_cfg["out_dir"]
    audit_log = webui_cfg["audit_log"]
    cover_retries = int(webui_cfg.get("cover_retries", select_cover.DEFAULT_RETRIES))
    cover_backoff = float(webui_cfg.get("cover_backoff_sec", select_cover.DEFAULT_BACKOFF_SEC))
    cover_concurrency = int(webui_cfg.get("cover_download_concurrency", 5))

    run_id = runs.new_run_id()  # correlates every record of this run (R9)
    built, failed = [], []

    # Stage 1: normalize (per-item, so one bad record doesn't kill the batch).
    normalized = []
    for raw in items:
        try:
            normalized.append(normalize_items.normalize_one(raw))
        except Exception as exc:  # noqa: BLE001 - classify, record, keep batch alive
            failed.append({"item": raw, "stage": "normalize",
                           "error": str(exc), "error_class": _error_class(exc)})
    _report(f"normalized {len(normalized)} item(s)")

    # Stage 2: dedupe against published state (R9). Record each skip so it is
    # visible in run history instead of silently dropped (R5). dedupe stays
    # read-only; the on_skip callback below owns the observability write.
    before = len(normalized)
    skips = []

    def _on_skip(record, reason):
        skips.append((record, reason))

    with state.connect(webui_cfg["state_path"]) as conn:
        deduped = list(dedupe_posts.dedupe(normalized, conn, on_skip=_on_skip))
    skipped = before - len(deduped)
    _report(f"deduped: {len(deduped)} new, {skipped} skipped")

    # Stage 3: batch cover download (parallel).  Done before caption so the
    # per-item loop below is unmodified — just the select_cover call is removed.
    select_cover.select_all(deduped, download_dir, COVER_TIMEOUT_SEC,
                            cover_retries, cover_backoff,
                            max_workers=cover_concurrency, progress_cb=_report)

    # Stages 4-6: caption → (cover already done) → watermark → build, per item.
    # A single open_run_conn reuses one SQLite connection for all record_run
    # calls below, amortising open/schema-check cost across the batch. Each
    # call still commits immediately (per-row durability).
    with runs.open_run_conn(webui_cfg["state_path"]) as run_conn:
        for record, reason in skips:
            runs.record_run(webui_cfg["state_path"], stage="dedupe", post_id=None,
                            status="skipped", detail=str(record.get("canonical_url", "")),
                            error=f"reason={reason}", run_id=run_id, severity="info",
                            conn=run_conn)

        for rec in deduped:
            title = rec.get("title", "")
            try:
                rec = render_caption.render_record(rec, template_cfg)
                cover_err = rec.get("cover_error")
                if cover_err:
                    raise ExternalError(cover_err)
                rec = watermark_cover.watermark(rec, wm_cfg)
                rec["run_id"] = run_id  # Q7: persist into manifest.backend.run_id (publish reads it back)
                manifest_path = build_manifest.build(rec, out_dir, audit_log)
                post_id = Path(manifest_path).parent.name
                built.append({"post_id": post_id, "title": title,
                              "manifest_path": manifest_path})
                runs.record_run(webui_cfg["state_path"], stage="build", post_id=post_id,
                                status="ok", detail=title, run_id=run_id, severity="info",
                                conn=run_conn)
                _report(f"built {post_id}")
            except Exception as exc:  # noqa: BLE001 - classify, record, keep batch alive
                error_class = _error_class(exc)
                failed.append({"title": title, "stage": "build",
                               "error": str(exc), "error_class": error_class})
                runs.record_run(webui_cfg["state_path"], stage="build", post_id=None,
                                status="failed", detail=title, error=str(exc),
                                run_id=run_id,
                                severity="warning" if error_class == "validation" else "error",
                                conn=run_conn)
                _report(f"failed: {title}: {exc}")

    return {"built": built, "failed": failed, "skipped": skipped}
