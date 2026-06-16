"""Local WebUI (FastAPI + HTMX) — settings, one-click crawl→stage, package list.

Localhost-only by design. Manual mode automates up to build-manifest; publishing
stays a manual CLI action with --approve unless auto_pipeline is enabled in settings.
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import webui_config, jobs, pipeline, runs, reviewed
from core.errors import CliError, SessionExpiredError
from browser import backend_driver
from src import draft_post, verify_draft, publish_post

WEBUI_CONFIG_PATH = "./configs/webui.yaml"
_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


# ---------------------------------------------------------------------------
# Module-level helpers (usable from request handlers and background jobs)
# ---------------------------------------------------------------------------

def _action_ns(post_id: str, stage: str, cfg: dict):
    """Build the argparse-style namespace for a backend command from webui cfg."""
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None:
        return None
    return cfg, SimpleNamespace(
        manifest=str(pkg / "manifest.json"),
        backend=cfg["backend_config"],
        storage_state=cfg["storage_state"],
        headless=True,
        timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS,
        retries=None,
        state=cfg["state_path"],
        dry_run=False,
    )


def _retry(fn, times: int = 3, delay: float = 1.0) -> tuple[Any, Exception | None]:
    """Call fn() up to *times* attempts, sleeping *delay* seconds between retries.

    Returns (result, None) on success, (None, last_exception) after all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(times):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < times - 1:
                time.sleep(delay)
    return None, last_exc


def _run_auto_pipeline(job, cfg: dict, built: list[dict]) -> None:
    """Run draft→verify→publish for all *built* packages inside an existing job.

    *built* is the list of dicts from pipeline.run_pipeline()["built"]:
    [{"post_id": str, "title": str, "manifest_path": str}, ...]

    Gate ① (reviewed) is bypassed by calling reviewed.mark() before publish.
    Gates ② (draft_verified status) and ③ (title match) remain enforced.
    Each stage retries up to 3 times with 1s delay on failure.

    Note: reviewed.mark() is called before the publish attempt. If publish fails all
    retries the reviewed record persists — intentional for a single-user tool (the
    user can re-publish manually without re-reviewing unchanged, already-verified content).
    """
    if not built:
        jobs.report(job, "無新稿件，跳過自動發布")
        return

    total = len(built)
    run_id = runs.new_run_id()
    drafted_ok: list[dict] = []
    verify_ok: list[dict] = []
    failed: list[dict] = []

    # --- DRAFT LOOP ---
    jobs.report(job, f"自動建草稿（共 {total} 篇）…")
    for i, item in enumerate(built):
        pid = item["post_id"]
        jobs.set_current(job, f"建草稿 {i + 1}/{total}：{item.get('title', pid)}")
        prepared = _action_ns(pid, "draft", cfg)
        if prepared is None:
            failed.append({"post_id": pid, "stage": "draft", "error": "找不到此貼文包"})
            continue
        _, ns = prepared
        _, exc = _retry(lambda ns=ns: draft_post._run(ns))
        if exc is None:
            drafted_ok.append(item)
            runs.record_run(cfg["state_path"], stage="draft", post_id=pid,
                            status="ok", run_id=run_id, severity="info")
        else:
            failed.append({"post_id": pid, "stage": "draft", "error": str(exc)})
            runs.record_run(cfg["state_path"], stage="draft", post_id=pid,
                            status="failed", error=str(exc), run_id=run_id, severity="error")

    jobs.report(job, f"建草稿完成：{len(drafted_ok)}/{total} 成功")

    # --- VERIFY LOOP ---
    jobs.report(job, f"自動驗證（共 {len(drafted_ok)} 篇）…")
    for i, item in enumerate(drafted_ok):
        pid = item["post_id"]
        jobs.set_current(job, f"驗證 {i + 1}/{len(drafted_ok)}：{item.get('title', pid)}")
        prepared = _action_ns(pid, "verify", cfg)
        if prepared is None:
            failed.append({"post_id": pid, "stage": "verify", "error": "找不到此貼文包"})
            continue
        _, ns = prepared
        _, exc = _retry(lambda ns=ns: verify_draft._run(ns))
        if exc is None:
            verify_ok.append(item)
            runs.record_run(cfg["state_path"], stage="verify", post_id=pid,
                            status="ok", run_id=run_id, severity="info")
        else:
            failed.append({"post_id": pid, "stage": "verify", "error": str(exc)})
            runs.record_run(cfg["state_path"], stage="verify", post_id=pid,
                            status="failed", error=str(exc), run_id=run_id, severity="error")

    verify_fail_count = len(drafted_ok) - len(verify_ok)
    jobs.report(job, f"驗證完成：{len(verify_ok)}/{len(drafted_ok)} 成功")

    # --- PUBLISH LOOP (only verified packages) ---
    jobs.report(job, f"自動發布（共 {len(verify_ok)} 篇）…")
    publish_ok = 0
    for i, item in enumerate(verify_ok):
        pid = item["post_id"]
        jobs.set_current(job, f"發布 {i + 1}/{len(verify_ok)}：{item.get('title', pid)}")
        pkg = _safe_pkg_dir(cfg["out_dir"], pid)
        if pkg is None or not (pkg / "manifest.json").exists():
            failed.append({"post_id": pid, "stage": "publish", "error": "找不到 manifest"})
            continue
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        cid = reviewed.content_id(m)
        # Bypass Gate ① by marking as reviewed before calling publish.
        # Gates ② (draft_verified) and ③ (title match) remain enforced inside publish_post._run.
        try:
            reviewed.mark(cfg["state_path"], pid, cid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"post_id": pid, "stage": "publish", "error": f"reviewed.mark 失敗：{exc}"})
            continue
        ns = SimpleNamespace(
            manifest=str(pkg / "manifest.json"),
            backend=cfg["backend_config"],
            storage_state=cfg["storage_state"],
            headless=True,
            timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS,
            retries=None,
            state=cfg["state_path"],
            approve=True,
            expected_content_id=cid,
        )
        _, exc = _retry(lambda ns=ns: publish_post._run(ns))
        if exc is None:
            publish_ok += 1
        else:
            failed.append({"post_id": pid, "stage": "publish", "error": str(exc)})
            runs.record_run(cfg["state_path"], stage="publish", post_id=pid,
                            status="failed", error=str(exc), run_id=run_id, severity="error")

    skip_count = verify_fail_count
    jobs.report(
        job,
        f"自動發布完成：成功 {publish_ok} / 失敗 {len(failed)} / "
        f"跳過 {skip_count}（驗證失敗 {verify_fail_count}）"
    )


def create_app(config_path: str = WEBUI_CONFIG_PATH) -> FastAPI:
    app = FastAPI(title="local-crawl-post-factory")
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.state.config_path = config_path
    app.state.session_expired_mtime = None  # storage-state mtime when expiry last seen
    # Gate ① ("reviewed") is now persisted in the state DB, bound to the reviewed
    # content version (core.reviewed) -- survives restart, fails closed on edits.

    def _cfg():
        """Load the current webui config (re-read each request: config is editable live)."""
        return webui_config.load(app.state.config_path)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        cfg = _cfg()
        return templates.TemplateResponse(
            request, "settings.html", {"cfg": cfg, "saved": False})

    @app.post("/settings", response_class=HTMLResponse)
    def save_settings(request: Request,
                      start_url: str = Form(""),
                      item_regex: str = Form(""),
                      deny_regex: str = Form(""),
                      limit: str = Form("30"),
                      download_delay: str = Form("0"),
                      concurrency: str = Form("8"),
                      source_id: str = Form(""),
                      cover_download_concurrency: str = Form("5"),
                      cover_retries: str = Form("0"),
                      cover_backoff_sec: str = Form("0"),
                      auto_pipeline: str = Form("")):
        incoming = {"start_url": start_url.strip(), "item_regex": item_regex,
                    "deny_regex": deny_regex, "limit": limit, "source_id": source_id,
                    "download_delay": download_delay, "concurrency": concurrency,
                    "cover_download_concurrency": cover_download_concurrency,
                    "cover_retries": cover_retries, "cover_backoff_sec": cover_backoff_sec,
                    "auto_pipeline": auto_pipeline}
        try:
            cfg = webui_config.save(app.state.config_path, {**_cfg(), **incoming})
        except CliError as exc:
            return HTMLResponse(f'<p class="error">{exc.message}</p>', status_code=400)
        return templates.TemplateResponse(
            request, "settings.html", {"cfg": cfg, "saved": True})

    @app.post("/crawl", response_class=HTMLResponse)
    def start_crawl(request: Request):
        cfg = _cfg()
        if not cfg.get("start_url"):
            return HTMLResponse('<p class="error">請先在設定填入 start_url</p>', status_code=400)

        def _work(job):
            jobs.set_current(job, "準備爬取…")
            jobs.report(job, "爬取中…")

            def _crawl_cb(snap):
                parts = [f"爬取進度 {snap['responses']} 頁"]
                if snap.get("last_title"):
                    parts.append(snap["last_title"])
                jobs.set_current(job, " — ".join(parts))

            items = pipeline.crawl_items(cfg, progress_cb=_crawl_cb)
            jobs.report(job, f"爬取完成：{len(items)} 篇")
            jobs.set_current(job, "建包中…")
            result = pipeline.run_pipeline(items, cfg, progress_cb=lambda m: jobs.report(job, m))
            if cfg.get("auto_pipeline"):
                _run_auto_pipeline(job, cfg, result.get("built", []))
            return result

        job_id = jobs.submit(_work)
        return templates.TemplateResponse(
            request, "_job_status.html", {"job": jobs.get(job_id), "job_id": job_id})

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return HTMLResponse('<p class="error">job not found</p>', status_code=404)
        return templates.TemplateResponse(
            request, "_job_status.html", {"job": job, "job_id": job_id})

    @app.get("/packages", response_class=HTMLResponse)
    def packages(request: Request, q: str = "", status: str = ""):
        cfg = _cfg()
        rows = _filter_packages(_scan_packages(cfg["out_dir"]), q, status)
        # HTMX requests (live filtering) get just the table fragment; full nav otherwise.
        template = "_packages_table.html" if request.headers.get("HX-Request") else "packages.html"
        return templates.TemplateResponse(
            request, template, {"packages": rows, "q": q, "status": status})

    @app.post("/packages/{post_id}/delete", response_class=HTMLResponse)
    def delete_package(request: Request, post_id: str):
        cfg = _cfg()
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None:
            return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
        _move_to_trash(cfg["out_dir"], pkg)
        rows = _filter_packages(_scan_packages(cfg["out_dir"]), "", "")
        return templates.TemplateResponse(
            request, "_packages_table.html", {"packages": rows, "q": "", "status": ""})

    @app.get("/packages/{post_id}", response_class=HTMLResponse)
    def package_detail(request: Request, post_id: str):
        cfg = _cfg()
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None or not (pkg / "manifest.json").exists():
            return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        # Gate ① : record the review bound to the content version just shown (Q9).
        reviewed.mark(cfg["state_path"], post_id, reviewed.content_id(m))
        caption_file = pkg / "caption.txt"
        caption = caption_file.read_text(encoding="utf-8") if caption_file.exists() else m.get("content", {}).get("body", "")
        has_cover = (pkg / "watermarked_cover.jpg").exists() or (pkg / "cover.jpg").exists()
        return templates.TemplateResponse(request, "detail.html", {
            "post_id": post_id,
            "title": m.get("content", {}).get("title", ""),
            "status": m.get("backend", {}).get("status", "?"),
            "canonical_url": m.get("source", {}).get("canonical_url", ""),
            "caption": caption,
            "has_cover": has_cover,
            "failure": _read_failure(pkg),
            "backend_config": "configs/backend.yaml",
        })

    @app.get("/packages/{post_id}/failure-image")
    def package_failure_image(post_id: str):
        cfg = _cfg()
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None:
            return PlainTextResponse("not found", status_code=404)
        failure = _read_failure(pkg)
        shot = failure.get("screenshot") if failure else None
        # Only serve a screenshot that lives inside this package dir (no traversal).
        if shot and Path(shot).resolve().parent == pkg.resolve() and Path(shot).exists():
            return FileResponse(shot)
        return PlainTextResponse("no failure image", status_code=404)

    @app.get("/packages/{post_id}/cover")
    def package_cover(post_id: str):
        cfg = _cfg()
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None:
            return PlainTextResponse("not found", status_code=404)
        for name in ("watermarked_cover.jpg", "cover.jpg"):
            f = pkg / name
            if f.exists():
                return FileResponse(str(f))
        return PlainTextResponse("no cover", status_code=404)

    @app.post("/packages/{post_id}/draft", response_class=HTMLResponse)
    def action_draft(request: Request, post_id: str):
        return _submit_action(request, "draft", post_id, _action_ns(post_id, "draft", _cfg()))

    @app.post("/packages/{post_id}/verify", response_class=HTMLResponse)
    def action_verify(request: Request, post_id: str):
        return _submit_action(request, "verify", post_id, _action_ns(post_id, "verify", _cfg()))

    def _submit_action(request, stage, post_id, prepared):
        if prepared is None:
            return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
        cfg, ns = prepared
        runner = {"draft": draft_post._run, "verify": verify_draft._run}[stage]
        return _submit_job(request, stage, post_id, cfg, lambda: runner(ns))

    @app.post("/batch/delete", response_class=HTMLResponse)
    def batch_delete(request: Request, post_ids: list[str] = Form(default=[])):
        """Move selected packages to .trash — reversible bulk delete."""
        if not post_ids:
            return HTMLResponse('<p class="hint">未選取任何貼文。</p>')
        cfg = _cfg()
        deleted, skipped = [], []
        for pid in post_ids:
            pkg = _safe_pkg_dir(cfg["out_dir"], pid)
            if pkg is None:
                skipped.append(pid)
                continue
            _move_to_trash(cfg["out_dir"], pkg)
            deleted.append(pid)
        rows = _filter_packages(_scan_packages(cfg["out_dir"]), "", "")
        msg = f'<p class="ok">已移入垃圾桶：{len(deleted)} 篇</p>'
        if skipped:
            msg += f'<p class="error">找不到（已略過）：{", ".join(skipped)}</p>'
        return msg + templates.TemplateResponse(
            request, "_packages_table.html", {"packages": rows, "q": "", "status": ""}
        ).body.decode()

    @app.post("/batch/{stage}", response_class=HTMLResponse)
    def batch_action(request: Request, stage: str, post_ids: list[str] = Form(default=[])):
        """Batch draft/verify over selected packages (R8).

        Publish is intentionally NOT batchable — its review/title gates are
        per-item by design (see plan U8). Each item reuses the single-item
        backend command; one failure never aborts the rest; all share one run_id.
        """
        if stage not in ("draft", "verify"):
            return HTMLResponse('<p class="error">不支援的批量動作</p>', status_code=400)
        if not post_ids:
            return HTMLResponse('<p class="hint">未選取任何貼文。</p>')
        cfg = _cfg()
        run_id = runs.new_run_id()
        runner = {"draft": draft_post._run, "verify": verify_draft._run}[stage]

        def _work(job):
            label = {"draft": "建草稿", "verify": "驗證"}.get(stage, stage)
            jobs.set_current(job, f"批量{label}中…")
            jobs.report(job, f"開始批量{label}，共 {len(post_ids)} 篇")
            ok, failed = [], []
            for i, pid in enumerate(post_ids):
                jobs.set_current(job, f"批量{label}中…（{i + 1}/{len(post_ids)}）")
                prepared = _action_ns(pid, stage, cfg)
                if prepared is None:  # invalid / traversal post_id -> skip, isolate
                    failed.append({"post_id": pid, "error": "找不到此貼文包"})
                    runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                    status="failed", error="invalid post_id",
                                    run_id=run_id, severity="error")
                    continue
                _, ns = prepared
                try:
                    runner(ns)
                    ok.append(pid)
                    runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                    status="ok", run_id=run_id, severity="info")
                except Exception as exc:  # noqa: BLE001 - isolate per item
                    if isinstance(exc, SessionExpiredError):
                        _note_session_expiry(cfg)
                    failed.append({"post_id": pid, "error": str(exc)})
                    runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                    status="failed", error=str(exc), run_id=run_id,
                                    severity="warning" if isinstance(exc, SessionExpiredError)
                                    else "error")
            return {"stage": stage, "batch": True, "run_id": run_id,
                    "ok": ok, "failed": failed}

        job_id = jobs.submit(_work)
        return templates.TemplateResponse(
            request, "_job_status.html", {"job": jobs.get(job_id), "job_id": job_id})

    def _submit_job(request, stage, post_id, cfg, call):
        """Run a backend command in a job: record the run, flag session expiry.

        publish records its own success run (with the manifest's build run_id)
        inside publish_post._run, so we skip the success record here for publish
        to avoid a double write (Q7). Failures are still recorded here for every
        stage, since the backend commands don't record their own failures.
        """
        run_id = runs.new_run_id()

        def _work(job):
            label = {"draft": "建草稿", "verify": "驗證", "publish": "發布"}.get(stage, stage)
            jobs.set_current(job, f"{label}中…")
            jobs.report(job, f"開始{label}…")
            try:
                call()
                jobs.report(job, f"{label}完成")
                if stage != "publish":
                    runs.record_run(cfg["state_path"], stage=stage, post_id=post_id,
                                    status="ok", run_id=run_id, severity="info")
                return {"stage": stage, "post_id": post_id}
            except Exception as exc:  # noqa: BLE001 - reported via job
                expired = isinstance(exc, SessionExpiredError)
                if expired:
                    _note_session_expiry(cfg)
                jobs.report(job, f"{label}失敗：{exc}")
                runs.record_run(cfg["state_path"], stage=stage, post_id=post_id,
                                status="failed", error=str(exc), run_id=run_id,
                                severity="warning" if expired else "error")
                raise

        job_id = jobs.submit(_work)
        return templates.TemplateResponse(
            request, "_job_status.html", {"job": jobs.get(job_id), "job_id": job_id})

    @app.post("/packages/{post_id}/publish", response_class=HTMLResponse)
    def action_publish(request: Request, post_id: str, title: str = Form("")):
        cfg = _cfg()
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None or not (pkg / "manifest.json").exists():
            return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
        # R6 三重閘門（順序固定 ①→②→③）—— 純決策抽到 check_publish_gates 便於單測。
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        stored_cid = reviewed.get(cfg["state_path"], post_id)
        msg = check_publish_gates(
            stored_cid, reviewed.content_id(m),
            m.get("backend", {}).get("status"),
            title, m.get("content", {}).get("title"))
        if msg:
            return HTMLResponse(f'<p class="error">{msg}</p>', status_code=400)

        ns = SimpleNamespace(
            manifest=str(pkg / "manifest.json"), backend=cfg["backend_config"],
            storage_state=cfg["storage_state"], headless=True,
            timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS, retries=None,
            state=cfg["state_path"], approve=True,
            expected_content_id=stored_cid)  # opt-in publish-time re-verify (Q9)
        return _submit_job(request, "publish", post_id, cfg, lambda: publish_post._run(ns))

    def _note_session_expiry(cfg):
        ss = Path(cfg["storage_state"])
        app.state.session_expired_mtime = ss.stat().st_mtime if ss.exists() else 0.0

    @app.get("/trash", response_class=HTMLResponse)
    def trash_list(request: Request):
        cfg = _cfg()
        rows = _scan_trash(cfg["out_dir"])
        return templates.TemplateResponse(request, "trash.html", {"items": rows})

    @app.post("/trash/{post_id}/restore", response_class=HTMLResponse)
    def restore_package(request: Request, post_id: str):
        cfg = _cfg()
        result = _restore_from_trash(cfg["out_dir"], post_id)
        if result == "not_found":
            return HTMLResponse('<p class="error">找不到此垃圾桶項目</p>', status_code=404)
        if result == "conflict":
            return HTMLResponse('<p class="error">上膛清單已有同名貼文，無法復原</p>', status_code=409)
        rows = _scan_trash(cfg["out_dir"])
        return templates.TemplateResponse(request, "_trash_table.html",
                                          {"items": rows, "msg": f"已復原：{post_id}",
                                           "msg_class": "ok"})

    @app.post("/trash/empty", response_class=HTMLResponse)
    def empty_trash(request: Request):
        import shutil

        cfg = _cfg()
        trash = Path(cfg["out_dir"]) / ".trash"
        if trash.exists():
            shutil.rmtree(trash)
        return templates.TemplateResponse(request, "_trash_table.html",
                                          {"items": [], "msg": "垃圾桶已清空",
                                           "msg_class": "ok"})

    @app.get("/auth-status", response_class=HTMLResponse)
    def auth_status(request: Request):
        cfg = _cfg()
        return templates.TemplateResponse(request, "_auth_status.html",
                                          {"light": _auth_light(cfg)})

    def _auth_light(cfg):
        # Metadata-only: never reads storage_state contents (credential-grade).
        ss = Path(cfg["storage_state"])
        cmd = ("auth-login --login-url <你的後台登入頁> "
               "--until-url-contains <登入後URL片段> "
               f"--storage-state {cfg['storage_state']}")
        if not ss.exists():
            return {"state": "none", "label": "登入態：未設定",
                    "guidance": "尚未建立登入態。請在終端機執行：", "cmd": cmd}
        expired_at = app.state.session_expired_mtime
        if expired_at is not None and ss.stat().st_mtime <= expired_at:
            return {"state": "expired", "label": "登入態：已過期，請重跑 auth-login",
                    "guidance": "登入態已過期。請在終端機重新登入：", "cmd": cmd}
        app.state.session_expired_mtime = None  # storage-state refreshed
        return {"state": "ok", "label": "登入態：有效"}

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, post_id: str = "", severity: str = "", run_id: str = ""):
        cfg = _cfg()
        rows = runs.list_runs(cfg["state_path"], limit=200,
                              post_id=post_id or None, severity=severity or None,
                              run_id=run_id or None)
        template = "_history_table.html" if request.headers.get("HX-Request") else "history.html"
        return templates.TemplateResponse(request, template,
                                          {"runs": rows, "post_id": post_id,
                                           "severity": severity, "run_id": run_id})

    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request):
        cfg = _cfg()
        lines = _tail_audit(cfg["audit_log"], 200)
        template = "_audit_table.html" if request.headers.get("HX-Request") else "audit.html"
        return templates.TemplateResponse(request, template, {"lines": lines})

    return app


def check_publish_gates(stored_cid, current_cid, status, submitted_title, manifest_title):
    """Pure publish-gate decision (R6/Q9). Returns a rejection message, or None
    if all three gates pass. Order is fixed and security-critical:
    ① reviewed AND content unchanged (fail-closed) → ② draft_verified → ③ title.
    Kept pure (no I/O) so the gate logic is unit-testable without the app.
    """
    if stored_cid is None or stored_cid != current_cid:
        return "請先開啟審核頁再發布（或內容已變更，需重新審核）"
    if status != "draft_verified":
        return "尚未驗證，不可發布"
    if (submitted_title or "").strip() != (manifest_title or "").strip():
        return "標題不符，發布取消"
    return None


def _read_failure(pkg):
    """Return the latest failure.json contents for a package, or None."""
    f = Path(pkg) / "failure.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _tail_audit(audit_log: str, limit: int):
    """Return the last ``limit`` parsed audit lines (newest first); skip bad lines."""
    p = Path(audit_log)
    if not p.exists():
        return []
    parsed = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError:
            continue  # skip broken line, never crash the view
    return list(reversed(parsed))[:limit]


def _filter_packages(rows, q: str, status: str):
    """Filter scanned packages by case-insensitive query (title or post_id) and status.

    status="" (default) hides published packages so the list stays actionable.
    status="all" shows everything including published.
    Any other value filters to that exact status.
    """
    q = (q or "").strip().lower()
    status = (status or "").strip()
    out = rows
    if status == "all":
        pass  # show everything
    elif status:
        out = [r for r in out if r.get("status") == status]
    else:
        out = [r for r in out if r.get("status") != "published"]
    if q:
        out = [r for r in out
               if q in str(r.get("title", "")).lower() or q in str(r.get("post_id", "")).lower()]
    return out


def _move_to_trash(out_dir: str, pkg):
    """Move a package dir into out_dir/.trash/ — reversible delete (never hard-remove)."""
    import shutil

    trash = Path(out_dir) / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / pkg.name
    if dest.exists():
        shutil.rmtree(dest)  # replace a previously trashed package of the same id
    shutil.move(str(pkg), str(dest))


def _scan_trash(out_dir: str) -> list[dict]:
    """List packages in out_dir/.trash/; return [{post_id, title}]."""
    trash = Path(out_dir) / ".trash"
    if not trash.exists():
        return []
    rows = []
    for d in sorted(trash.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        title = d.name
        mp = d / "manifest.json"
        if mp.exists():
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
                title = m.get("content", {}).get("title", d.name) or d.name
            except (json.JSONDecodeError, OSError):
                pass
        rows.append({"post_id": d.name, "title": title})
    return rows


def _restore_from_trash(out_dir: str, post_id: str) -> str:
    """Move post_id from .trash/ back to out_dir/.

    Returns "ok" | "not_found" | "conflict".
    """
    import shutil

    if not post_id or post_id.startswith(".") or "/" in post_id or "\\" in post_id:
        return "not_found"
    trash = Path(out_dir) / ".trash"
    src = (trash / post_id).resolve()
    if src.parent != trash.resolve() or not src.is_dir():
        return "not_found"
    dest = Path(out_dir) / post_id
    if dest.exists():
        return "conflict"
    shutil.move(str(src), str(dest))
    return "ok"


def _safe_pkg_dir(out_dir: str, post_id: str):
    """Resolve out_dir/post_id, rejecting path traversal and dot-dirs (e.g. .trash)."""
    if not post_id or post_id.startswith(".") or "/" in post_id or "\\" in post_id or ".." in post_id:
        return None
    base = Path(out_dir).resolve()
    target = (base / post_id).resolve()
    if target.parent != base or not target.is_dir():
        return None
    return target


def _scan_packages(out_dir: str):
    """Read every out/<post_id>/manifest.json; skip broken ones."""
    rows: list[dict] = []
    base = Path(out_dir)
    if not base.exists():
        return rows
    for manifest_path in sorted(base.glob("*/manifest.json")):
        if manifest_path.parent.name.startswith("."):
            continue  # skip .trash and other dot dirs
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rows.append({"post_id": manifest_path.parent.name, "title": "(壞掉的 manifest)",
                         "status": "error", "broken": True})
            continue
        rows.append({
            "post_id": m.get("post_id", manifest_path.parent.name),
            "title": m.get("content", {}).get("title", ""),
            "status": m.get("backend", {}).get("status", "?"),
            "broken": False,
        })
    return rows


def run():  # console-script entry point
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


app = create_app()
