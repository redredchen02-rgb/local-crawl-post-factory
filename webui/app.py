"""Local WebUI (FastAPI + HTMX) — settings, one-click crawl→stage, package list.

Localhost-only by design. The UI automates only up to build-manifest; it never
drafts or publishes (publishing stays a manual CLI action with --approve).
"""

import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import webui_config, jobs, pipeline
from core.errors import CliError

WEBUI_CONFIG_PATH = "./configs/webui.yaml"
_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def create_app(config_path: str = WEBUI_CONFIG_PATH) -> FastAPI:
    app = FastAPI(title="local-crawl-post-factory")
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.state.config_path = config_path

    @app.get("/", response_class=HTMLResponse)
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        cfg = webui_config.load(app.state.config_path)
        return templates.TemplateResponse(
            request, "settings.html", {"cfg": cfg, "saved": False})

    @app.post("/settings", response_class=HTMLResponse)
    def save_settings(request: Request,
                      start_url: str = Form(""),
                      item_regex: str = Form(""),
                      deny_regex: str = Form(""),
                      limit: str = Form("30"),
                      source_id: str = Form("")):
        incoming = {"start_url": start_url.strip(), "item_regex": item_regex,
                    "deny_regex": deny_regex, "limit": limit, "source_id": source_id}
        try:
            cfg = webui_config.save(app.state.config_path, {**webui_config.load(app.state.config_path), **incoming})
        except CliError as exc:
            return HTMLResponse(f'<p class="error">{exc.message}</p>', status_code=400)
        return templates.TemplateResponse(
            request, "settings.html", {"cfg": cfg, "saved": True})

    @app.post("/crawl", response_class=HTMLResponse)
    def start_crawl(request: Request):
        cfg = webui_config.load(app.state.config_path)
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
    def packages(request: Request):
        cfg = webui_config.load(app.state.config_path)
        return templates.TemplateResponse(
            request, "packages.html", {"packages": _scan_packages(cfg["out_dir"])})

    @app.get("/packages/{post_id}", response_class=HTMLResponse)
    def package_detail(request: Request, post_id: str):
        cfg = webui_config.load(app.state.config_path)
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None or not (pkg / "manifest.json").exists():
            return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
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
            "backend_config": "configs/backend.yaml",
        })

    @app.get("/packages/{post_id}/cover")
    def package_cover(post_id: str):
        cfg = webui_config.load(app.state.config_path)
        pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
        if pkg is None:
            return PlainTextResponse("not found", status_code=404)
        for name in ("watermarked_cover.jpg", "cover.jpg"):
            f = pkg / name
            if f.exists():
                return FileResponse(str(f))
        return PlainTextResponse("no cover", status_code=404)

    return app


def _safe_pkg_dir(out_dir: str, post_id: str):
    """Resolve out_dir/post_id, rejecting path traversal."""
    if not post_id or "/" in post_id or "\\" in post_id or ".." in post_id:
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
