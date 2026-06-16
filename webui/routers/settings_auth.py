from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from core import webui_config
from core.errors import CliError
from webui.routers._ctx import auth_light, cfg_from_request, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    cfg = cfg_from_request(request)
    return templates.TemplateResponse(
        request, "settings.html", {"cfg": cfg, "saved": False})


@router.post("/settings", response_class=HTMLResponse)
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
        cfg = webui_config.save(request.app.state.config_path, {**cfg_from_request(request), **incoming})
    except CliError as exc:
        return HTMLResponse(f'<p class="error">{exc.message}</p>', status_code=400)
    return templates.TemplateResponse(
        request, "settings.html", {"cfg": cfg, "saved": True})


@router.get("/auth-status", response_class=HTMLResponse)
def auth_status(request: Request):
    cfg = cfg_from_request(request)
    return templates.TemplateResponse(request, "_auth_status.html",
                                      {"light": auth_light(request, cfg)})
