"""Local WebUI (FastAPI + HTMX) — settings, one-click crawl→stage, package list.

Localhost-only by design. The UI automates only up to build-manifest; it never
drafts or publishes (publishing stays a manual CLI action with --approve).
"""

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import webui_config, jobs, pipeline, runs
from core.errors import CliError, SessionExpiredError
from browser import backend_driver
from src import draft_post, verify_draft, publish_post

WEBUI_CONFIG_PATH = "./configs/webui.yaml"
_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def create_app(config_path: str = WEBUI_CONFIG_PATH) -> FastAPI:
    app = FastAPI(title="local-crawl-post-factory")
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.state.config_path = config_path
    app.state.reviewed = set()          # post_ids whose review page was opened (R6 gate 1)
    app.state.session_expired_mtime = None  # storage-state mtime when expiry last seen

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
                      source_id: str = Form("")):
        incoming = {"start_url": start_url.strip(), "item_regex": item_regex,
                    "deny_regex": deny_regex, "limit": limit, "source_id": source_id,
                    "download_delay": download_delay, "concurrency": concurrency}
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
            jobs.report(job, "crawling…")
            items = pipeline.crawl_items(cfg)
            jobs.report(job, f"crawled {len(items)} item(s)")
            return pipeline.run_pipeline(items, cfg, progress_cb=lambda m: jobs.report(job, m))

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
        app.state.reviewed.add(post_id)  # R6 gate 1: this package has been reviewed
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
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
        return _submit_action(request, "draft", post_id, _action_ns(post_id, "draft"))

    @app.post("/packages/{post_id}/verify", response_class=HTMLResponse)
    def action_verify(request: Request, post_id: str):
        return _submit_action(request, "verify", post_id, _action_ns(post_id, "verify"))

    def _action_ns(post_id, stage):
        """Build the argparse-style namespace for a backend command from webui cfg."""
        cfg = _cfg()
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

    def _submit_action(request, stage, post_id, prepared):
        if prepared is None:
            return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
        cfg, ns = prepared
        runner = {"draft": draft_post._run, "verify": verify_draft._run}[stage]
        return _submit_job(request, stage, post_id, cfg, lambda: runner(ns))

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
            ok, failed = [], []
            for pid in post_ids:
                prepared = _action_ns(pid, stage)
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
        """Run a backend command in a job: record the run, flag session expiry."""
        run_id = runs.new_run_id()

        def _work(job):
            try:
                call()
                runs.record_run(cfg["state_path"], stage=stage, post_id=post_id,
                                status="ok", run_id=run_id, severity="info")
                return {"stage": stage, "post_id": post_id}
            except Exception as exc:  # noqa: BLE001 - reported via job
                expired = isinstance(exc, SessionExpiredError)
                if expired:
                    _note_session_expiry(cfg)
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
        # R6 三重閘門，順序固定，任一不過即拒（不可逆動作不被繞過，R8）。
        if post_id not in app.state.reviewed:
            return HTMLResponse('<p class="error">請先開啟審核頁再發布</p>', status_code=400)
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        if m.get("backend", {}).get("status") != "draft_verified":
            return HTMLResponse('<p class="error">尚未驗證，不可發布</p>', status_code=400)
        if title.strip() != (m.get("content", {}).get("title") or "").strip():
            return HTMLResponse('<p class="error">標題不符，發布取消</p>', status_code=400)

        ns = SimpleNamespace(
            manifest=str(pkg / "manifest.json"), backend=cfg["backend_config"],
            storage_state=cfg["storage_state"], headless=True,
            timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS, retries=None,
            state=cfg["state_path"], approve=True)
        return _submit_job(request, "publish", post_id, cfg, lambda: publish_post._run(ns))

    def _note_session_expiry(cfg):
        ss = Path(cfg["storage_state"])
        app.state.session_expired_mtime = ss.stat().st_mtime if ss.exists() else 0.0

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
    def history(request: Request, post_id: str = "", severity: str = ""):
        cfg = _cfg()
        rows = runs.list_runs(cfg["state_path"], limit=200,
                              post_id=post_id or None, severity=severity or None)
        template = "_history_table.html" if request.headers.get("HX-Request") else "history.html"
        return templates.TemplateResponse(request, template,
                                          {"runs": rows, "post_id": post_id, "severity": severity})

    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request):
        cfg = _cfg()
        lines = _tail_audit(cfg["audit_log"], 200)
        template = "_audit_table.html" if request.headers.get("HX-Request") else "audit.html"
        return templates.TemplateResponse(request, template, {"lines": lines})

    return app


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
    """Filter scanned packages by case-insensitive query (title or post_id) and status."""
    q = (q or "").strip().lower()
    status = (status or "").strip()
    out = rows
    if status:
        out = [r for r in out if r.get("status") == status]
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
    rows = []
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
