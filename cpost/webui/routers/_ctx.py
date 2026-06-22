"""Shared request-aware context for all webui routers."""

import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cpost.core import jobs, runs, webui_config
from cpost.core.errors import SessionExpiredError
from cpost.cli import draft_post, verify_draft

_HERE = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))

try:
    _app_version = version("local-crawl-post-factory")
except PackageNotFoundError:
    _app_version = "dev"

templates.env.globals["app_version"] = _app_version
_logger = logging.getLogger(__name__)


def cfg_from_request(request: Request) -> dict:
    """Load current webui config (re-read each request: config is editable live)."""
    return webui_config.load(request.app.state.config_path)


def note_session_expiry(request: Request, cfg: dict) -> None:
    ss = Path(cfg["storage_state"])
    request.app.state.session_expired_mtime = ss.stat().st_mtime if ss.exists() else 0.0


def auth_light(request: Request, cfg: dict) -> dict:
    # Metadata-only: never reads storage_state contents (credential-grade).
    ss = Path(cfg["storage_state"])
    cmd = ("auth-login --login-url <你的後台登入頁> "
           "--until-url-contains <登入後URL片段> "
           f"--storage-state {cfg['storage_state']}")
    if not ss.exists():
        return {"state": "none", "label": "登入態：未設定",
                "guidance": "尚未建立登入態。請在終端機執行：", "cmd": cmd}
    expired_at = request.app.state.session_expired_mtime
    if expired_at is not None and ss.stat().st_mtime <= expired_at:
        return {"state": "expired", "label": "登入態：已過期，請重跑 auth-login",
                "guidance": "登入態已過期。請在終端機重新登入：", "cmd": cmd}
    request.app.state.session_expired_mtime = None  # storage-state refreshed
    return {"state": "ok", "label": "登入態：有效"}


def submit_job(request: Request, stage: str, post_id: str, cfg: dict, call) -> HTMLResponse:
    """Run a backend command in a job: record the run, flag session expiry.

    publish records its own success run (with the manifest's build run_id)
    inside publish_post.run, so we skip the success record here for publish
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
                note_session_expiry(request, cfg)
            jobs.report(job, f"{label}失敗：{exc}")
            runs.record_run(cfg["state_path"], stage=stage, post_id=post_id,
                            status="failed", error=str(exc), run_id=run_id,
                            severity="warning" if expired else "error")
            raise

    job_id = jobs.submit(_work)
    return templates.TemplateResponse(
        request, "_job_status.html", {"job": jobs.get(job_id), "job_id": job_id})


def submit_action(request: Request, stage: str, post_id: str, prepared: Any) -> HTMLResponse:
    if prepared is None:
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    cfg, ns = prepared
    runner = {"draft": draft_post.run, "verify": verify_draft.run}[stage]
    return submit_job(request, stage, post_id, cfg, lambda: runner(ns))
