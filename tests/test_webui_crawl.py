"""WebUI one-click crawl→stage (Unit 4) with crawl injected (no network)."""

import time

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config, pipeline


def _client(tmp_path, monkeypatch):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com/news",
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    def fake_crawl(cfg):
        return [
            {"source_id": "example.com", "url": "https://example.com/news/a",
             "canonical_url": "https://example.com/news/a", "title": "標題甲",
             "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"},
            {"source_id": "example.com", "url": "https://example.com/news/b",
             "canonical_url": "https://example.com/news/b", "title": "標題乙",
             "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"},
        ]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)
    return TestClient(create_app(str(cfgp))), tmp_path


def _job_id(html):
    # _job_status.html embeds hx-get="/jobs/<id>" while running; else parse done view
    import re
    m = re.search(r"/jobs/([0-9a-f]+)", html)
    return m.group(1) if m else None


def test_crawl_builds_packages(tmp_path, monkeypatch):
    client, tp = _client(tmp_path, monkeypatch)
    r = client.post("/crawl")
    assert r.status_code == 200
    jid = _job_id(r.text)
    assert jid

    for _ in range(100):
        s = client.get(f"/jobs/{jid}")
        if "完成" in s.text or "失敗" in s.text:
            break
        time.sleep(0.05)
    assert "完成" in s.text
    assert (tp / "out").glob("*/manifest.json")
    built = list((tp / "out").glob("*/manifest.json"))
    assert len(built) == 2


def test_crawl_without_start_url_400(tmp_path, monkeypatch):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": ""})
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    assert r.status_code == 400


def test_job_not_found_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.get("/jobs/deadbeef").status_code == 404
