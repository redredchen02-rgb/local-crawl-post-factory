import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core import runs
from webui._helpers import _tail_audit
from webui.routers._ctx import cfg_from_request, templates

router = APIRouter()
_logger = logging.getLogger(__name__)


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, post_id: str = "", severity: str = "", run_id: str = ""):
    # P3 measure-first: log I/O latency to decide whether TTL cache is needed.
    _t0 = time.perf_counter()
    cfg = cfg_from_request(request)
    rows = runs.list_runs(cfg["state_path"], limit=200,
                          post_id=post_id or None, severity=severity or None,
                          run_id=run_id or None)
    _logger.debug(
        "history latency %.1f ms (rows=%d)", (time.perf_counter() - _t0) * 1000, len(rows)
    )
    template = "_history_table.html" if request.headers.get("HX-Request") else "history.html"
    return templates.TemplateResponse(request, template,
                                      {"runs": rows, "post_id": post_id,
                                       "severity": severity, "run_id": run_id})


@router.get("/audit", response_class=HTMLResponse)
def audit(request: Request):
    # P3 measure-first: log I/O latency to decide whether TTL cache is needed.
    _t0 = time.perf_counter()
    cfg = cfg_from_request(request)
    lines = _tail_audit(cfg["audit_log"], 200)
    _logger.debug(
        "audit latency %.1f ms (lines=%d)", (time.perf_counter() - _t0) * 1000, len(lines)
    )
    template = "_audit_table.html" if request.headers.get("HX-Request") else "audit.html"
    return templates.TemplateResponse(request, template, {"lines": lines})
