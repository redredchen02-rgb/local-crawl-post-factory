"""U7: crawl politeness (download_delay / concurrency) settings + passthrough."""

import pytest
import yaml
from fastapi.testclient import TestClient

from cpost.webui.app import create_app
from cpost.core import webui_config, pipeline
from cpost.core.errors import ValidationError


def test_download_delay_passed_to_crawler(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(pipeline.crawl_posts, "crawl_items", lambda opts, **kw: captured.update(opts) or [])
    cfg = {"start_url": "https://example.com/news", "download_delay": 1.5, "concurrency": 3, "limit": 10}
    pipeline.crawl_items(cfg)
    assert captured["download_delay"] == 1.5
    assert captured["concurrency"] == 3


def test_settings_persists_politeness(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com"})
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/settings", data={
        "start_url": "https://example.com", "limit": "30",
        "download_delay": "2.0", "concurrency": "4"})
    assert r.status_code == 200
    saved = yaml.safe_load(open(cfgp, encoding="utf-8"))
    assert saved["download_delay"] == 2.0
    assert saved["concurrency"] == 4


def test_negative_delay_rejected(tmp_path):
    with pytest.raises(ValidationError):
        webui_config.save(str(tmp_path / "w.yaml"),
                          {"start_url": "https://x.com", "download_delay": -1})


def test_zero_concurrency_rejected(tmp_path):
    with pytest.raises(ValidationError):
        webui_config.save(str(tmp_path / "w.yaml"),
                          {"start_url": "https://x.com", "concurrency": 0})
