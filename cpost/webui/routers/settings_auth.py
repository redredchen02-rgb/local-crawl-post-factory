import html
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from cpost.core import webui_config
from cpost.core.errors import CliError, ValidationError
from cpost.core.url_utils import host_of, make_source_id
from cpost.core.validators import is_safe_external_host, valid_url
from cpost.webui.routers._ctx import auth_light, cfg_from_request, templates


_VALIDATION_FIELD_RE = re.compile(
    r"^(?:invalid )?(\w+) must"       # e.g. "limit must be >= 0"
    r"|^invalid (\w+):"               # e.g. "invalid start_url: ..."
    r"|^sources\[\d+\]\.(\w+)"        # e.g. "sources[0].enabled must be a boolean"
)


def _extract_field(message: str) -> str | None:
    """Extract field name from a validation error message, or None if not field-specific."""
    m = _VALIDATION_FIELD_RE.match(message)
    if m:
        return m.group(1) or m.group(2) or m.group(3)
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


def _sources_partial(request: Request, *, hint: str = "") -> HTMLResponse:
    """Re-render _sources_list.html as an HTMX swap fragment."""
    cfg = cfg_from_request(request)
    sources, malformed = _sources_view(cfg)
    return templates.TemplateResponse(
        request, "_sources_list.html",
        {"sources": sources, "sources_malformed": malformed, "batch_hint": hint})


def _save_sources(config_path: str, sources: list[dict]) -> None:
    """Persist an updated sources list, merging over the existing raw config."""
    raw = webui_config.load_raw(config_path)
    raw["sources"] = sources
    webui_config.save(config_path, raw)


@router.post("/sources/add", response_class=HTMLResponse)
def sources_add(request: Request,
                source_id: str = Form(""),
                start_url: str = Form(""),
                enabled: str = Form("")):
    config_path = request.app.state.config_path
    cfg = cfg_from_request(request)
    sources, _ = _sources_view(cfg)
    new_entry: dict = {
        "source_id": source_id.strip(),
        "start_url": start_url.strip(),
        "enabled": enabled == "on",
    }
    try:
        _save_sources(config_path, [*sources, new_entry])
    except ValidationError as exc:
        field = _extract_field(exc.message)
        return HTMLResponse(
            f'<p class="error" data-field="{html.escape(field or "")}">{html.escape(exc.message)}</p>',
            status_code=400)
    return _sources_partial(request)


@router.post("/sources/edit/{sid}", response_class=HTMLResponse)
def sources_edit(request: Request, sid: str,
                 start_url: str = Form(""),
                 enabled: str = Form("")):
    config_path = request.app.state.config_path
    cfg = cfg_from_request(request)
    sources, _ = _sources_view(cfg)
    updated = []
    found = False
    for src in sources:
        if src.get("source_id") == sid:
            entry = dict(src)  # preserve unknown per-source keys
            if start_url.strip():
                entry["start_url"] = start_url.strip()
            entry["enabled"] = enabled == "on"
            updated.append(entry)
            found = True
        else:
            updated.append(src)
    if not found:
        return HTMLResponse('<p class="error">找不到此來源</p>', status_code=404)
    try:
        _save_sources(config_path, updated)
    except ValidationError as exc:
        field = _extract_field(exc.message)
        return HTMLResponse(
            f'<p class="error" data-field="{html.escape(field or "")}">{html.escape(exc.message)}</p>',
            status_code=400)
    return _sources_partial(request)


@router.post("/sources/delete/{sid}", response_class=HTMLResponse)
def sources_delete(request: Request, sid: str):
    config_path = request.app.state.config_path
    cfg = cfg_from_request(request)
    sources, _ = _sources_view(cfg)
    updated = [s for s in sources if s.get("source_id") != sid]
    if len(updated) == len(sources):
        return HTMLResponse('<p class="error">找不到此來源</p>', status_code=404)
    try:
        _save_sources(config_path, updated)
    except ValidationError as exc:
        return HTMLResponse(f'<p class="error">{html.escape(exc.message)}</p>', status_code=400)
    return _sources_partial(request)


@router.post("/sources/toggle/{sid}", response_class=HTMLResponse)
def sources_toggle(request: Request, sid: str):
    config_path = request.app.state.config_path
    cfg = cfg_from_request(request)
    sources, _ = _sources_view(cfg)
    updated = []
    found = False
    for src in sources:
        if src.get("source_id") == sid:
            entry = dict(src)
            entry["enabled"] = not bool(src.get("enabled", True))
            updated.append(entry)
            found = True
        else:
            updated.append(src)
    if not found:
        return HTMLResponse('<p class="error">找不到此來源</p>', status_code=404)
    _save_sources(config_path, updated)
    return _sources_partial(request)


@router.post("/sources/batch-add", response_class=HTMLResponse)
def sources_batch_add(request: Request, urls: str = Form("")):
    config_path = request.app.state.config_path
    cfg = cfg_from_request(request)
    sources, _ = _sources_view(cfg)
    existing_ids = {s.get("source_id") for s in sources if s.get("source_id")}

    lines = [line.strip() for line in urls.split("\n") if line.strip()]
    new_entries: list[dict] = []
    skipped_count = 0

    for line in lines:
        if not valid_url(line):
            skipped_count += 1
            continue
        h = host_of(line)
        if not h:  # pragma: no cover — valid_url() already rejects URLs without hostname
            skipped_count += 1
            continue
        if not is_safe_external_host(h):
            skipped_count += 1
            continue
        sid = make_source_id(h)
        if sid in existing_ids:
            skipped_count += 1
            continue
        existing_ids.add(sid)
        new_entries.append({"source_id": sid, "start_url": line, "enabled": True})

    if new_entries:
        try:
            _save_sources(config_path, [*sources, *new_entries])
        except ValidationError as exc:
            return _sources_partial(request, hint=html.escape(exc.message))

    parts = []
    if new_entries:
        parts.append(f"已新增 {len(new_entries)} 筆")
    if skipped_count:
        parts.append(f"跳過 {skipped_count} 筆")
    hint = "；".join(parts) if parts else ""

    return _sources_partial(request, hint=hint)


@router.get("/auth-status", response_class=HTMLResponse)
def auth_status(request: Request):
    cfg = cfg_from_request(request)
    return templates.TemplateResponse(request, "_auth_status.html",
                                      {"light": auth_light(request, cfg)})
