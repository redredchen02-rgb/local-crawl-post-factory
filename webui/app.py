"""Local WebUI (FastAPI + HTMX) — settings, one-click crawl→stage, package list.

Localhost-only by design. Manual mode automates up to build-manifest; publishing
stays a manual CLI action with --approve unless auto_pipeline is enabled in settings.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webui.routers import (
    actions,
    crawl,
    history_audit,
    packages,
    settings_auth,
    trash,
)

WEBUI_CONFIG_PATH = "./configs/webui.yaml"
_HERE = Path(__file__).parent


def create_app(config_path: str = WEBUI_CONFIG_PATH) -> FastAPI:
    app = FastAPI(title="local-crawl-post-factory")
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.state.config_path = config_path
    app.state.session_expired_mtime = None  # storage-state mtime when expiry last seen
    # Gate ① ("reviewed") is now persisted in the state DB, bound to the reviewed
    # content version (core.reviewed) -- survives restart, fails closed on edits.

    app.include_router(settings_auth.router)
    app.include_router(crawl.router)
    app.include_router(packages.router)
    app.include_router(actions.router)
    app.include_router(trash.router)
    app.include_router(history_audit.router)

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


def run():  # console-script entry point
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


app = create_app()
