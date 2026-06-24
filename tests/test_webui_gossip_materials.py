"""WebUI /gossip-materials: URL submission, crawl job, intersection view."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from cpost.core import library, webui_config
from cpost.webui.app import create_app

NOW = "2026-06-24T10:00:00+00:00"


def _client_and_state(tmp_path, **overrides):
    cfgp = tmp_path / "webui.yaml"
    settings = {"start_url": "https://example.com", "out_dir": str(tmp_path / "out")}
    settings.update(overrides)
    webui_config.save(str(cfgp), settings)
    cfg = webui_config.load(str(cfgp))
    return TestClient(create_app(str(cfgp))), cfg["state_path"]


def test_get_gossip_materials_empty(tmp_path):
    client, _ = _client_and_state(tmp_path)
    resp = client.get("/gossip-materials")
    assert resp.status_code == 200
    assert "吃瓜素材" in resp.text
    assert "素材庫" in resp.text
    assert "瓜交集" in resp.text


def test_get_gossip_materials_has_nav_link(tmp_path):
    client, _ = _client_and_state(tmp_path)
    resp = client.get("/gossip-materials")
    assert resp.status_code == 200
    assert "/gossip-materials" in resp.text


def test_post_crawl_valid_url_starts_job(tmp_path):
    client, state_path = _client_and_state(tmp_path)

    def _fake_crawl_url(url, cfg, progress_cb=None, now=""):
        return {"item_count": 3, "failed": 0}

    with patch("cpost.webui.routers.gossip_materials.gossip_crawl.crawl_url",
               side_effect=_fake_crawl_url):
        with patch("cpost.webui.routers.gossip_materials.validators.is_safe_external_host",
                   return_value=True):
            resp = client.post("/gossip-materials/crawl",
                               data={"url": "https://example.com/", "label": "test"})

    assert resp.status_code == 200
    assert "job_id" in resp.text or "狀態" in resp.text or "爬取" in resp.text

    with library.connect(state_path) as conn:
        rows = library.list_gossip_urls(conn)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/"
    assert rows[0]["label"] == "test"


def test_post_crawl_invalid_url_returns_400(tmp_path):
    client, _ = _client_and_state(tmp_path)
    resp = client.post("/gossip-materials/crawl",
                       data={"url": "not-a-url", "label": ""})
    assert resp.status_code == 400
    assert "URL" in resp.text


def test_post_crawl_ssrf_blocked_returns_400(tmp_path):
    client, _ = _client_and_state(tmp_path)
    with patch("cpost.webui.routers.gossip_materials.validators.is_safe_external_host",
               return_value=False):
        resp = client.post("/gossip-materials/crawl",
                           data={"url": "http://192.168.1.1/", "label": ""})
    assert resp.status_code == 400
    assert "私有" in resp.text or "拒絕" in resp.text


def test_get_job_not_found_returns_404(tmp_path):
    client, _ = _client_and_state(tmp_path)
    resp = client.get("/gossip-materials/jobs/nonexistent-id")
    assert resp.status_code == 404


def test_get_gossip_materials_shows_url_after_submit(tmp_path):
    client, state_path = _client_and_state(tmp_path)
    with library.connect(state_path) as conn:
        library.submit_gossip_url(conn, "https://known.com/page", "test label", NOW)

    resp = client.get("/gossip-materials")
    assert resp.status_code == 200
    assert "known.com" in resp.text
    assert "test label" in resp.text
