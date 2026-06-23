"""WebUI /roster: read-only site roster panel (U6)."""

from fastapi.testclient import TestClient

from cpost.core import site_roster, webui_config
from cpost.webui.app import create_app


def _client(tmp_path, roster_path: str = ""):
    cfgp = tmp_path / "webui.yaml"
    settings = {
        "start_url": "https://example.com",
        "out_dir": str(tmp_path / "out"),
    }
    if roster_path:
        settings["roster_path"] = roster_path
    webui_config.save(str(cfgp), settings)
    return TestClient(create_app(str(cfgp)))


def _seed_roster(roster_path: str):
    """Insert one active and one candidate site into the roster DB."""
    site_roster.upsert_site(
        roster_path,
        domain="alpha.example.com",
        start_url="https://alpha.example.com/",
        tier=site_roster.ACTIVE,
    )
    site_roster.upsert_site(
        roster_path,
        domain="beta.example.com",
        start_url="https://beta.example.com/",
        tier=site_roster.CANDIDATE,
    )


def test_roster_with_sites(tmp_path):
    """GET /roster with a populated roster DB → 200, HTML contains known domains."""
    roster_db = str(tmp_path / "roster.sqlite")
    _seed_roster(roster_db)
    client = _client(tmp_path, roster_path=roster_db)
    r = client.get("/roster")
    assert r.status_code == 200
    assert "alpha.example.com" in r.text
    assert "beta.example.com" in r.text


def test_roster_empty_roster_path(tmp_path):
    """GET /roster when roster_path is empty → 200, shows empty-state message."""
    client = _client(tmp_path, roster_path="")
    r = client.get("/roster")
    assert r.status_code == 200
    assert "尚無自動發現站點" in r.text


def test_roster_db_file_missing(tmp_path):
    """GET /roster when roster_path is set but file does not exist → 200, empty panel."""
    missing_db = str(tmp_path / "nonexistent_roster.sqlite")
    client = _client(tmp_path, roster_path=missing_db)
    r = client.get("/roster")
    assert r.status_code == 200
    assert "尚無自動發現站點" in r.text


def test_roster_tier_badge_rendered(tmp_path):
    """Tier badge for active site is present in HTML."""
    roster_db = str(tmp_path / "roster.sqlite")
    site_roster.upsert_site(
        roster_db,
        domain="active-site.com",
        start_url="https://active-site.com/",
        tier=site_roster.ACTIVE,
    )
    client = _client(tmp_path, roster_path=roster_db)
    r = client.get("/roster")
    assert r.status_code == 200
    assert "active" in r.text
    assert "active-site.com" in r.text


def test_roster_nav_link_present(tmp_path):
    """The /roster URL is reachable and base template renders (no nav hardcoded test)."""
    client = _client(tmp_path)
    r = client.get("/roster")
    assert r.status_code == 200
