import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from core import webui_config
from core.errors import CliError, ValidationError
from webui.routers._ctx import auth_light, cfg_from_request, templates


_VALIDATION_FIELD_RE = re.compile(r"^(?:invalid )?(\w+) must|^invalid (\w+):")


def _extract_field(message: str) -> str | None:
    """Extract field name from a validation error message, or None if not field-specific."""
    m = _VALIDATION_FIELD_RE.match(message)
    if m:
        return m.group(1) or m.group(2)
    return None

router = APIRouter()


def _diag(cfg: dict, config_path: str) -> dict:
    from pathlib import Path as _P
    return {
        "config_path": config_path,
        "state_path": cfg.get("state_path", ""),
        "state_exists": _P(cfg.get("state_path", "")).exists() if cfg.get("state_path") else False,
        "storage_state": cfg.get("storage_state", ""),
        "ss_exists": _P(cfg.get("storage_state", "")).exists() if cfg.get("storage_state") else False,
        "out_dir": cfg.get("out_dir", ""),
        "out_exists": _P(cfg.get("out_dir", "")).exists() if cfg.get("out_dir") else False,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    cfg = cfg_from_request(request)
    return templates.TemplateResponse(
        request, "settings.html", {"cfg": cfg, "saved": False,
                                   "diag": _diag(cfg, request.app.state.config_path)})


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
    except ValidationError as exc:
        field = _extract_field(exc.message)
        cfg_in = cfg_from_request(request)
        return templates.TemplateResponse(
            request, "settings.html", {"cfg": cfg_in, "saved": False,
                                        "field_error": {field: exc.message} if field else None})
    except CliError as exc:
        return HTMLResponse(f'<p class="error">{exc.message}</p>', status_code=400)
    return templates.TemplateResponse(
        request, "settings.html", {"cfg": cfg, "saved": True,
                                   "diag": _diag(cfg, request.app.state.config_path)})


@router.get("/auth-status", response_class=HTMLResponse)
def auth_status(request: Request):
    cfg = cfg_from_request(request)
    return templates.TemplateResponse(request, "_auth_status.html",
                                      {"light": auth_light(request, cfg)})
