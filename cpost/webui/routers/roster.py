"""WebUI /roster: read-only site roster panel (U6).

Displays all discovered/monitored sites with their tier, health counters,
and crawl timestamps. Safe to visit when roster_path is unset or the DB
does not yet exist — shows an empty panel rather than a 500.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cpost.core import site_roster
from cpost.webui.routers._ctx import cfg_from_request, templates

_ALL_TIERS = (
    site_roster.ACTIVE,
    site_roster.MONITORED,
    site_roster.CANDIDATE,
    site_roster.MIRROR,
    site_roster.FAILED,
    site_roster.INACTIVE,
)

router = APIRouter()


def _load_all_sites(roster_path: str) -> list[dict]:
    """Return all sites across every tier; empty list on missing DB or empty path."""
    if not roster_path:
        return []
    if not Path(roster_path).exists():
        return []
    all_sites: list[dict] = []
    for tier in _ALL_TIERS:
        all_sites.extend(site_roster.list_by_tier(roster_path, tier))
    return all_sites


@router.get("/roster", response_class=HTMLResponse)
def roster(request: Request):
    cfg = cfg_from_request(request)
    sites = _load_all_sites(cfg.get("roster_path", ""))
    return templates.TemplateResponse(
        request, "roster.html", {"sites": sites})
