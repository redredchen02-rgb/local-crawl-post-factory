from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cpost.core import runs
from cpost.webui._helpers import _scan_packages
from cpost.webui.routers._ctx import cfg_from_request, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    cfg = cfg_from_request(request)
    pkgs = _scan_packages(cfg["out_dir"])
    counts: dict[str, int] = {}
    for p in pkgs:
        s = p.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1

    state_path = cfg.get("state_path", "")
    recent_runs: list = []
    if state_path:
        try:
            recent_runs = runs.list_runs(state_path, limit=5)
        except Exception:  # noqa: BLE001
            pass

    actionable = sum(v for k, v in counts.items()
                     if k not in ("published", "unknown"))
    verified = counts.get("draft_verified", 0)
    published = counts.get("published", 0)

    return templates.TemplateResponse(request, "dashboard.html", {
        "actionable": actionable,
        "verified": verified,
        "published": published,
        "counts": counts,
        "recent_runs": recent_runs,
        "cfg": cfg,
    })


@router.get("/_dashboard_stats", response_class=HTMLResponse)
def dashboard_stats(request: Request):
    cfg = cfg_from_request(request)
    pkgs = _scan_packages(cfg["out_dir"])
    counts: dict[str, int] = {}
    for p in pkgs:
        s = p.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    actionable = sum(v for k, v in counts.items() if k not in ("published", "unknown"))
    return templates.TemplateResponse(request, "_dashboard_stats.html", {
        "actionable": actionable,
        "verified": counts.get("draft_verified", 0),
        "published": counts.get("published", 0),
    })
