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


def _sources_view(cfg: dict) -> tuple[list[dict], bool]:
    """Return (dict-only sources, malformed-flag) for the read-only panel.

    load() never validates ``sources``, so a hand-edited YAML may carry scalar
    entries (or not even be a list). Filtering to dict entries here keeps the
    read-only panel from rendering — and crashing on — malformed input. The flag
    surfaces the malformation as a visible hint instead of silently dropping it.
    """
    raw = cfg.get("sources")
    items = raw if isinstance(raw, list) else []
    clean = [s for s in items if isinstance(s, dict)]
    malformed = len(clean) != len(items) or (bool(raw) and not isinstance(raw, list))
    return clean, malformed


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
    sources, malformed = _sources_view(cfg)
    return templates.TemplateResponse(
        request, "settings.html", {"cfg": cfg, "saved": False,
                                   "sources": sources, "sources_malformed": malformed,
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
                  auto_pipeline: str = Form("")):
    incoming = {"start_url": start_url.strip(), "item_regex": item_regex,
                "deny_regex": deny_regex, "limit": limit, "source_id": source_id,
                "download_delay": download_delay, "concurrency": concurrency,
                "auto_pipeline": auto_pipeline}
    config_path = request.app.state.config_path
    try:
        # Merge over load_raw() (unresolved) -- never load() -- so non-form fields,
        # including infra paths, keep their portable on-disk form instead of being
        # rewritten to machine-absolute paths (which would break relocation, #3).
        webui_config.save(config_path, {**webui_config.load_raw(config_path), **incoming})
    except ValidationError as exc:
        field = _extract_field(exc.message)
        cfg_in = cfg_from_request(request)
        sources_in, malformed_in = _sources_view(cfg_in)
        return templates.TemplateResponse(
            request, "settings.html", {"cfg": cfg_in, "saved": False,
                                        "sources": sources_in, "sources_malformed": malformed_in,
                                        "field_error": {field: exc.message} if field else None,
                                        "diag": _diag(cfg_in, config_path)})
    except CliError as exc:
        return HTMLResponse(f'<p class="error">{exc.message}</p>', status_code=400)
    cfg = cfg_from_request(request)  # re-load resolved, for accurate diag display
    sources, malformed = _sources_view(cfg)
    return templates.TemplateResponse(
        request, "settings.html", {"cfg": cfg, "saved": True,
                                   "sources": sources, "sources_malformed": malformed,
                                   "diag": _diag(cfg, config_path)})


@router.get("/auth-status", response_class=HTMLResponse)
def auth_status(request: Request):
    cfg = cfg_from_request(request)
    return templates.TemplateResponse(request, "_auth_status.html",
                                      {"light": auth_light(request, cfg)})
