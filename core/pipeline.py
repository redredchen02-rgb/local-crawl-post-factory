"""In-process pipeline orchestrator (single source of pipeline logic).

Reuses the existing CLI stage functions directly instead of shelling out, so the
WebUI and the CLI share one implementation. crawl stays in its own subprocess
(Scrapy reactor cannot restart in-process) via crawl_posts.crawl_items.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from core import reviewed, runs, state
from core.errors import SessionExpiredError, ValidationError
from core.schema import AutoPipelineResult, PipelineFailed, PipelineItem, PipelineResult
from src import (
    build_manifest,
    crawl_posts,
    dedupe_posts,
    draft_post,
    normalize_items,
    publish_post,
    render_caption,
    verify_draft,
)


def crawl_items(webui_cfg: dict,
                progress_cb: Callable[[str], object] | None = None,
                poll_sec: float = 0.5) -> list[dict]:
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
        "max_pages": int(webui_cfg.get("max_pages", 200)),
        "download_delay": float(webui_cfg.get("download_delay", 0.0)),
        "concurrency": int(webui_cfg.get("concurrency", 8)),
        "source_id": webui_cfg.get("source_id", ""),
        # Wire the text-length knobs through to the crawl subprocess; without these
        # the config field is silently ignored and the crawler falls back to its
        # own CONFIG_DEFAULTS. max_text_chars=0 means "no clamp".
        "max_text_chars": int(webui_cfg.get(
            "max_text_chars", crawl_posts.CONFIG_DEFAULTS["max_text_chars"])),
        "min_text_chars": int(webui_cfg.get(
            "min_text_chars", crawl_posts.CONFIG_DEFAULTS["min_text_chars"])),
        "start_urls": [webui_cfg["start_url"]],
    })
    return crawl_posts.crawl_items(opts, progress_cb=progress_cb, poll_sec=poll_sec)


def crawl_all_sources(webui_cfg: dict,
                      progress_cb: Callable[[str], object] | None = None,
                      poll_sec: float = 0.5) -> list[dict]:
    """Crawl every configured source and return the combined raw items.

    Reads ``webui_cfg["sources"]`` -- a list of per-source dicts that override
    the base config (e.g. ``start_url``, ``source_id``, ``item_regex``). Falls
    back to a single crawl of ``webui_cfg["start_url"]`` when no ``sources`` list
    is present (backward compatible). One source failing is reported via
    ``progress_cb`` and never aborts the others -- mirroring the per-item
    isolation of :func:`run_pipeline`.
    """
    sources = webui_cfg.get("sources")
    if not sources:
        return crawl_items(webui_cfg, progress_cb=progress_cb, poll_sec=poll_sec)

    combined: list[dict] = []
    for src in sources:
        merged = {**webui_cfg, **src}
        label = src.get("source_id") or src.get("start_url") or "?"
        try:
            combined.extend(crawl_items(merged, progress_cb=progress_cb, poll_sec=poll_sec))
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort the batch
            if progress_cb:
                progress_cb(f"source {label} failed: {exc}")
    return combined


def run_pipeline(items: list[dict], webui_cfg: dict,
                 progress_cb: Callable[[str], object] | None = None) -> PipelineResult:
    """Run normalize→dedupe→caption→build over ``items``.

    A single bad item is recorded under ``result["failed"]`` and never aborts
    the batch.
    """
    def _report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    def _error_class(exc: Exception) -> str:
        # ValidationError = bad data (expected, skip the item); anything else is
        # an unexpected system fault worth distinguishing for observability.
        return "validation" if isinstance(exc, ValidationError) else "system"

    template_cfg = render_caption.load_template(webui_cfg["template_path"])
    out_dir = webui_cfg["out_dir"]
    audit_log = webui_cfg["audit_log"]

    run_id = runs.new_run_id()  # correlates every record of this run (R9)
    built: list[PipelineItem] = []
    failed: list[PipelineFailed] = []

    # Stage 1: normalize (per-item, so one bad record doesn't kill the batch).
    normalized = []
    for raw in items:
        try:
            normalized.append(normalize_items.normalize_one(raw))
        except Exception as exc:  # noqa: BLE001 - classify, record, keep batch alive
            failed.append({"post_id": None, "stage": "normalize",
                           "error": str(exc), "error_class": _error_class(exc)})
    _report(f"normalized {len(normalized)} item(s)")

    # Stage 2: dedupe against published state (R9). Record each skip so it is
    # visible in run history instead of silently dropped (R5). dedupe stays
    # read-only; the on_skip callback below owns the observability write.
    before = len(normalized)
    skips = []

    def _on_skip(record: dict, reason: str) -> None:
        skips.append((record, reason))

    with state.connect(webui_cfg["state_path"]) as conn:
        deduped = list(dedupe_posts.dedupe(normalized, conn, on_skip=_on_skip))
    skipped = before - len(deduped)
    _report(f"deduped: {len(deduped)} new, {skipped} skipped")

    # Stages 3-4: caption → build, per item.
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
                failed.append({"post_id": None, "stage": "build",
                               "error": str(exc), "error_class": error_class})
                runs.record_run(webui_cfg["state_path"], stage="build", post_id=None,
                                status="failed", detail=title, error=str(exc),
                                run_id=run_id,
                                severity="warning" if error_class == "validation" else "error",
                                conn=run_conn)
                _report(f"failed: {title}: {exc}")

    return {"built": built, "failed": failed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Auto-pipeline: draft → verify → publish (public unified entry point, P7)
# ---------------------------------------------------------------------------

def _retry(fn: Callable[[], object], times: int = 3,
           delay: float = 1.0) -> tuple:  # (result, error_or_None)
    """Call fn() up to *times* attempts, sleeping *delay* seconds between retries."""
    last_exc: Exception | None = None
    for attempt in range(times):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < times - 1:
                time.sleep(delay)
    return None, last_exc


def run_auto_pipeline(
    built: list[PipelineItem],
    cfg: dict,
    *,
    timeout_ms: int = 30_000,
    on_progress: Callable[[str], object] | None = None,
    on_status: Callable[[str], object] | None = None,
    on_session_expired: Callable[[dict], object] | None = None,
) -> AutoPipelineResult:
    """Draft→verify→publish for each item in *built*.

    *built* is the list from run_pipeline()["built"]:
    [{"post_id": str, "title": str, "manifest_path": str}, ...]

    Callbacks (all optional):
    - on_progress(msg: str)  — milestone log line (replaces jobs.report)
    - on_status(msg: str)    — current-task label (replaces jobs.set_current)
    - on_session_expired(cfg) — called on SessionExpiredError

    Returns {"ok": int, "failed": list[dict], "verify_fail_count": int}.
    """

    def _report(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def _setstatus(msg: str) -> None:
        if on_status:
            on_status(msg)

    def _note_expiry(exc: Exception) -> None:
        if on_session_expired is not None and isinstance(exc, SessionExpiredError):
            on_session_expired(cfg)

    if not built:
        _report("無新稿件，跳過自動發布")
        return {"ok": 0, "failed": [], "verify_fail_count": 0}

    total = len(built)
    run_id = runs.new_run_id()
    drafted_ok: list[PipelineItem] = []
    verify_ok: list[PipelineItem] = []
    failed: list[PipelineFailed] = []

    # --- DRAFT LOOP ---
    _report(f"自動建草稿（共 {total} 篇）…")
    for i, item in enumerate(built):
        pid = item["post_id"]
        _setstatus(f"建草稿 {i + 1}/{total}：{item.get('title', pid)}")
        manifest_path = Path(item["manifest_path"])
        if not manifest_path.exists():
            failed.append({"post_id": pid, "stage": "draft", "error": "找不到此貼文包"})
            continue
        ns = SimpleNamespace(
            manifest=str(manifest_path),
            backend=cfg["backend_config"],
            storage_state=cfg["storage_state"],
            headless=True,
            timeout_ms=timeout_ms,
            retries=None,
            state=cfg["state_path"],
            dry_run=False,
        )
        _rv, err = _retry(lambda ns=ns: draft_post.run(ns))  # type: ignore[misc]
        if err is None:
            drafted_ok.append(item)
            runs.record_run(cfg["state_path"], stage="draft", post_id=pid,
                            status="ok", run_id=run_id, severity="info")
        else:
            _note_expiry(err)
            failed.append({"post_id": pid, "stage": "draft", "error": str(err)})
            runs.record_run(cfg["state_path"], stage="draft", post_id=pid,
                            status="failed", error=str(err), run_id=run_id, severity="error")

    _report(f"建草稿完成：{len(drafted_ok)}/{total} 成功")

    # --- VERIFY LOOP ---
    _report(f"自動驗證（共 {len(drafted_ok)} 篇）…")
    for i, item in enumerate(drafted_ok):
        pid = item["post_id"]
        _setstatus(f"驗證 {i + 1}/{len(drafted_ok)}：{item.get('title', pid)}")
        manifest_path = Path(item["manifest_path"])
        if not manifest_path.exists():
            failed.append({"post_id": pid, "stage": "verify", "error": "找不到此貼文包"})
            continue
        ns = SimpleNamespace(
            manifest=str(manifest_path),
            backend=cfg["backend_config"],
            storage_state=cfg["storage_state"],
            headless=True,
            timeout_ms=timeout_ms,
            retries=None,
            state=cfg["state_path"],
            dry_run=False,
        )
        _rv, err = _retry(lambda ns=ns: verify_draft.run(ns))  # type: ignore[misc]
        if err is None:
            verify_ok.append(item)
            runs.record_run(cfg["state_path"], stage="verify", post_id=pid,
                            status="ok", run_id=run_id, severity="info")
        else:
            _note_expiry(err)
            failed.append({"post_id": pid, "stage": "verify", "error": str(err)})
            runs.record_run(cfg["state_path"], stage="verify", post_id=pid,
                            status="failed", error=str(err), run_id=run_id, severity="error")

    verify_fail_count = len(drafted_ok) - len(verify_ok)
    _report(f"驗證完成：{len(verify_ok)}/{len(drafted_ok)} 成功")

    # --- PUBLISH LOOP ---
    _report(f"自動發布（共 {len(verify_ok)} 篇）…")
    publish_ok = 0
    for i, item in enumerate(verify_ok):
        pid = item["post_id"]
        _setstatus(f"發布 {i + 1}/{len(verify_ok)}：{item.get('title', pid)}")
        manifest_path = Path(item["manifest_path"])
        if not manifest_path.exists():
            failed.append({"post_id": pid, "stage": "publish", "error": "找不到 manifest"})
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        cid = reviewed.content_id(m)
        try:
            reviewed.mark(cfg["state_path"], pid, cid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"post_id": pid, "stage": "publish",
                           "error": f"reviewed.mark 失敗：{exc}"})
            continue
        ns = SimpleNamespace(
            manifest=str(manifest_path),
            backend=cfg["backend_config"],
            storage_state=cfg["storage_state"],
            headless=True,
            timeout_ms=timeout_ms,
            retries=None,
            state=cfg["state_path"],
            approve=True,
            expected_content_id=cid,
        )
        _rv, err = _retry(lambda ns=ns: publish_post.run(ns))  # type: ignore[misc]
        if err is None:
            publish_ok += 1
        else:
            _note_expiry(err)
            failed.append({"post_id": pid, "stage": "publish", "error": str(err)})
            runs.record_run(cfg["state_path"], stage="publish", post_id=pid,
                            status="failed", error=str(err), run_id=run_id, severity="error")

    _report(
        f"自動發布完成：成功 {publish_ok} / 失敗 {len(failed)} / "
        f"跳過 {verify_fail_count}（驗證失敗 {verify_fail_count}）"
    )
    return {"ok": publish_ok, "failed": failed, "verify_fail_count": verify_fail_count}
