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
    dashboard,
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

    app.include_router(dashboard.router)
    app.include_router(settings_auth.router)
    app.include_router(crawl.router)
    app.include_router(packages.router)
    app.include_router(actions.router)
    app.include_router(trash.router)
    app.include_router(history_audit.router)

    return app


def run():  # console-script entry point
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


app = create_app()
