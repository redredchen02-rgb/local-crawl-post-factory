"""WebUI settings page + localhost binding (Unit 3)."""

import yaml
from fastapi.testclient import TestClient

from cpost.webui.app import create_app, run
from cpost.core import webui_config


def _client(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com/news"})
    return TestClient(create_app(str(cfgp))), str(cfgp)


def test_settings_page_shows_current(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "https://example.com/news" in r.text


def test_post_settings_persists(tmp_path):
    client, cfgp = _client(tmp_path)
    r = client.post("/settings", data={
        "start_url": "https://example.com/blog", "item_regex": "/blog/",
        "deny_regex": "login", "limit": "7", "source_id": "ex"})
    assert r.status_code == 200
    saved = yaml.safe_load(open(cfgp, encoding="utf-8"))
    assert saved["start_url"] == "https://example.com/blog"
    assert saved["limit"] == 7


def test_post_settings_invalid_url_400(tmp_path):
    client, cfgp = _client(tmp_path)
    r = client.post("/settings", data={"start_url": "not-a-url", "limit": "30"})
    assert r.status_code == 200  # HTMX inline: re-renders form with per-field error
    assert "field-err" in r.text
    assert "invalid start_url" in r.text
    # original config not corrupted
    assert yaml.safe_load(open(cfgp, encoding="utf-8"))["start_url"] == "https://example.com/news"


def test_post_settings_keeps_config_portable(tmp_path):
    """#3: saving settings via the WebUI must not rewrite infra paths to absolute
    machine paths (which would break standalone relocation)."""
    cfgp = tmp_path / "webui.yaml"
    cfgp.write_text(
        "start_url: https://example.com/news\n"
        "state_path: ../state/db.sqlite\n"
        "out_dir: ../out\n"
        "download_dir: ../out/assets\n"
        "audit_log: ../logs/audit.jsonl\n"
        "storage_state: ../auth/ss.json\n",
        encoding="utf-8")
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/settings", data={"start_url": "https://example.com/changed",
                                        "limit": "30", "download_delay": "0",
                                        "concurrency": "8"})
    assert r.status_code == 200
    raw = yaml.safe_load(cfgp.read_text(encoding="utf-8"))
    for field in ("state_path", "out_dir", "download_dir", "audit_log", "storage_state"):
        assert not str(raw[field]).startswith("/"), f"{field} became absolute: {raw[field]!r}"


def test_run_binds_localhost(monkeypatch):
    captured = {}

    def fake_run(app, host, port):
        captured["host"] = host

    import cpost.webui.app as appmod
    monkeypatch.setattr(appmod, "uvicorn", type("U", (), {"run": staticmethod(fake_run)}), raising=False)
    # inject a fake uvicorn module import
    import sys
    import types
    fake = types.ModuleType("uvicorn")
    fake.run = fake_run
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    run()
    assert captured["host"] == "127.0.0.1"


def test_htmx_asset_served_nonempty(tmp_path):
    """HTMX must be a real vendored file — an empty file silently breaks all hx-* UI."""
    client, _ = _client(tmp_path)
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert len(r.content) > 1000
    assert b"htmx" in r.content


def test_base_references_htmx(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings")
    assert '<script src="/static/htmx.min.js">' in r.text


def test_stylesheet_served_and_linked(tmp_path):
    client, _ = _client(tmp_path)
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert len(css.content) > 100
    page = client.get("/settings")
    assert '<link rel="stylesheet" href="/static/app.css">' in page.text
    # inline <style> block was removed in favour of the external sheet
    assert "<style>" not in page.text


def test_root_is_dashboard(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "待處理" in r.text or "總覽" in r.text


def test_dashboard_stats_partial(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/_dashboard_stats")
    assert r.status_code == 200
    assert "待處理" in r.text


def test_settings_still_reachable(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "https://example.com/news" in r.text
