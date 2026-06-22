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

from cpost.core import reviewed, runs, state
from cpost.core.backend_args import BackendInvocation
from cpost.core.errors import SessionExpiredError, ValidationError
from cpost.core.schema import AutoPipelineResult, PipelineFailed, PipelineItem, PipelineResult
from cpost.cli import (
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
        # Per-source extraction overrides (R6). crawl_all_sources merges per-source
        # values into webui_cfg before calling here; empty string = use the
        # crawler's built-in hardcoded selectors (fallback in _extract).
        "body_selector": webui_cfg.get("body_selector", ""),
        "image_selector": webui_cfg.get("image_selector", ""),
        "date_selector": webui_cfg.get("date_selector", ""),
        "start_urls": [webui_cfg["start_url"]],
    })
    return crawl_posts.crawl_items(opts, progress_cb=progress_cb, poll_sec=poll_sec)


def enabled_sources(webui_cfg: dict) -> list[dict]:
    """Return the enabled per-source dicts from ``webui_cfg["sources"]``.

    Validates the shape so a malformed config surfaces a clean
    :class:`ValidationError` (→ 400 at the router) instead of a later
    ``.get``-on-non-dict ``AttributeError`` (→ 500). ``enabled`` defaults to
    true when the key is absent; a falsy ``enabled`` drops the entry.
    """
    sources = webui_cfg.get("sources") or []
    if not isinstance(sources, list):
        raise ValidationError("webui config 'sources' must be a list")
    enabled: list[dict] = []
    for src in sources:
        if not isinstance(src, dict):
            raise ValidationError(
                f"each 'sources' entry must be a mapping, got {type(src).__name__}")
        if src.get("enabled", True):
            enabled.append(src)
    return enabled


def _safe_cb(cb: Callable[..., object] | None, *args: object) -> None:
    """Invoke an observability callback so it can never break the crawl."""
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:  # noqa: BLE001 - a broken callback must not abort the crawl
        pass


def crawl_all_sources(webui_cfg: dict,
                      progress_cb: Callable[[str], object] | None = None,
                      poll_sec: float = 0.5,
                      on_source: Callable[[str, object], object] | None = None) -> list[dict]:
    """Crawl every enabled source and return the combined raw items.

    Reads ``webui_cfg["sources"]`` -- a list of per-source dicts that override
    the base config (e.g. ``start_url``, ``source_id``, ``item_regex``). Sources
    with ``enabled: false`` are skipped (``enabled`` defaults to true when the
    key is absent). Falls back to a single crawl of ``webui_cfg["start_url"]``
    when there are no enabled sources -- covering BOTH "no sources list" and
    "every source disabled" (backward compatible; single-site = N=1).

    Per-source results are reported via ``on_source(source_id, count_or_error)``:
    an ``int`` item count on success, an error-message ``str`` on failure. One
    source failing never aborts the others -- mirroring the per-item isolation of
    :func:`run_pipeline`. ``progress_cb`` is reserved for the realtime dict-snapshot
    crawl telemetry threaded into :func:`crawl_items`; per-source success/failure
    must NOT go there (the router's dict-shaped callback would crash on a str).
    """
    enabled = enabled_sources(webui_cfg)
    if not enabled:
        return crawl_items(webui_cfg, progress_cb=progress_cb, poll_sec=poll_sec)

    combined: list[dict] = []
    for src in enabled:
        merged = {**webui_cfg, **src}
        label = src.get("source_id") or src.get("start_url") or "?"
        try:
            items = crawl_items(merged, progress_cb=progress_cb, poll_sec=poll_sec)
            combined.extend(items)
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort the batch
            # on_source is called OUTSIDE the crawl's try below; only the crawl
            # itself is guarded here so a failing callback can't be mislabeled
            # as a crawl failure.
            _safe_cb(on_source, label, f"failed: {exc}")
            continue
        _safe_cb(on_source, label, len(items))
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
           delay: float = 1.0) -> tuple[object, None] | tuple[None, Exception]:
    """Call fn() up to *times* attempts, sleeping *delay* seconds between retries."""
    last_exc: Exception | None = None
    for attempt in range(times):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < times - 1:
                time.sleep(delay)
    assert last_exc is not None  # all attempts failed → at least one exception caught
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
    failed: list[PipelineFailed] = []

    def _invocation(manifest_path: Path, *, approve: bool = False,
                    expected_content_id: str | None = None) -> BackendInvocation:
        """One typed backend-args contract (R14) — replaces the per-stage SimpleNamespace."""
        return BackendInvocation(
            manifest=str(manifest_path),
            backend=cfg["backend_config"],
            storage_state=cfg["storage_state"],
            state=cfg["state_path"],
            headless=True,
            timeout_ms=timeout_ms,
            retries=None,
            approve=approve,
            expected_content_id=expected_content_id,
        )

    def _run_stage(
        stage: str,
        header: str,
        status_label: str,
        runner: Callable[[BackendInvocation], object],
        items: list[PipelineItem],
        *,
        missing_error: str,
        prepare: Callable[[Path, PipelineItem], BackendInvocation | None] | None = None,
        on_ok: Callable[[Path], str | None] | None = None,
    ) -> tuple[list[PipelineItem], int]:
        """Run one draft/verify/publish stage over *items*.

        Does ONCE what the three loops used to duplicate: header report, per-item
        status, manifest-existence check, BackendInvocation construction, _retry
        wrapping, _note_expiry, runs.record_run, and ok/failed bookkeeping.

        Stage-specific extras are threaded as hooks, never re-duplicated:
        - *prepare* (publish only): runs the pre-step (reviewed.mark) and returns the
          invocation; returning ``None`` means it already recorded the failure and
          the item must be skipped. When absent, a default invocation is built.
        - *on_ok* (publish only): derives the ``detail`` for the success run record.

        Returns ``(ok_items, fail_count)``.
        """
        _report(header)
        ok_items: list[PipelineItem] = []
        fail_count = 0
        n = len(items)
        for i, item in enumerate(items):
            pid = item["post_id"]
            _setstatus(f"{status_label} {i + 1}/{n}：{item.get('title', pid)}")
            manifest_path = Path(item["manifest_path"])
            if not manifest_path.exists():
                failed.append({"post_id": pid, "stage": stage, "error": missing_error})
                fail_count += 1
                continue
            if prepare is not None:
                inv = prepare(manifest_path, item)
                if inv is None:  # pre-step failed and already recorded — skip item
                    fail_count += 1
                    continue
            else:
                inv = _invocation(manifest_path)
            _rv, err = _retry(lambda inv=inv: runner(inv))  # type: ignore[misc]
            if err is None:
                ok_items.append(item)
                detail = on_ok(manifest_path) if on_ok is not None else None
                runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                status="ok", detail=detail, run_id=run_id, severity="info")
            else:
                _note_expiry(err)
                failed.append({"post_id": pid, "stage": stage, "error": str(err)})
                fail_count += 1
                runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                status="failed", error=str(err), run_id=run_id, severity="error")
        return ok_items, fail_count

    # --- DRAFT ---
    drafted_ok, draft_fail = _run_stage(
        "draft", f"自動建草稿（共 {total} 篇）…", "建草稿",
        draft_post.run, built, missing_error="找不到此貼文包")
    _report(f"建草稿完成：{len(drafted_ok)}/{total} 成功")

    # --- VERIFY ---
    verify_ok, verify_fail = _run_stage(
        "verify", f"自動驗證（共 {len(drafted_ok)} 篇）…", "驗證",
        verify_draft.run, drafted_ok, missing_error="找不到此貼文包")
    verify_fail_count = len(drafted_ok) - len(verify_ok)
    _report(f"驗證完成：{len(verify_ok)}/{len(drafted_ok)} 成功")

    # --- PUBLISH ---
    # Publish carries two extras the draft/verify path lacks: a reviewed.mark
    # pre-step (gate/approve) and a published_url detail on the success record.
    def _prepare_publish(manifest_path: Path,
                         item: PipelineItem) -> BackendInvocation | None:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        cid = reviewed.content_id(m)
        try:
            reviewed.mark(cfg["state_path"], item["post_id"], cid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"post_id": item["post_id"], "stage": "publish",
                           "error": f"reviewed.mark 失敗：{exc}"})
            return None
        return _invocation(manifest_path, approve=True, expected_content_id=cid)

    def _publish_detail(manifest_path: Path) -> str | None:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        url: str | None = m.get("backend", {}).get("published_url")
        return url

    published_ok, publish_fail = _run_stage(
        "publish", f"自動發布（共 {len(verify_ok)} 篇）…", "發布",
        publish_post.run, verify_ok, missing_error="找不到 manifest",
        prepare=_prepare_publish, on_ok=_publish_detail)
    publish_ok = len(published_ok)

    _report(
        f"自動發布完成：成功 {publish_ok} / "
        f"草稿失敗 {draft_fail} / 驗證失敗 {verify_fail} / 發布失敗 {publish_fail}"
    )
    return {"ok": publish_ok, "failed": failed, "verify_fail_count": verify_fail_count}
