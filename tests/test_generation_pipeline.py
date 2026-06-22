"""core.scoop_pipeline.run_generation_pipeline: scoop selection -> packages.

generate-article is stubbed (it is fully covered in test_generate_article); these
tests exercise the orchestration: per-cluster build, failure isolation, no key
leak, and that generated packages land in the existing packages console.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from core import scoop_pipeline, webui_config
from core.errors import ExternalError
from src import generate_article
from webui.app import create_app


def _cfg(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com",
                                  "out_dir": str(tmp_path / "out")})
    return webui_config.load(str(cfgp)), str(cfgp)


def _fake_item(cid, now):
    return {"title": f"標題{cid}", "caption": f"正文內容{cid}", "text": f"正文內容{cid}",
            "canonical_url": f"https://scoop.cpost.local/{cid}", "source_id": "scoop",
            "url": "https://rep.example.com/x",
            "published_at": "2026-06-15T10:00:00+08:00", "discovered_at": now}


def test_generation_builds_packages(tmp_path, monkeypatch):
    cfg, _ = _cfg(tmp_path)
    monkeypatch.setattr(generate_article, "generate",
                        lambda conn, cid, lc, pr, now, **kw: _fake_item(cid, now))
    result = scoop_pipeline.run_generation_pipeline(["c1", "c2"], cfg)
    assert result["kind"] == "generate"
    assert len(result["built"]) == 2 and not result["failed"]
    for b in result["built"]:
        manifest = json.loads(
            (Path(cfg["out_dir"]) / b["post_id"] / "manifest.json").read_text("utf-8"))
        assert manifest["content"]["body"].startswith("正文內容")   # caption -> body


def test_generation_isolates_failure(tmp_path, monkeypatch):
    cfg, _ = _cfg(tmp_path)

    def fake(conn, cid, lc, pr, now, **kw):
        if cid == "bad":
            raise ExternalError("LLM 端点错误 HTTP 500：boom")
        return _fake_item(cid, now)

    monkeypatch.setattr(generate_article, "generate", fake)
    result = scoop_pipeline.run_generation_pipeline(["ok", "bad"], cfg)
    assert len(result["built"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["cluster_id"] == "bad"
    assert result["failed"][0]["stage"] == "generate"


def test_generation_error_does_not_leak_key(tmp_path, monkeypatch):
    cfg, _ = _cfg(tmp_path)
    monkeypatch.setenv("CPOST_LLM_API_KEY", "SUPERSECRET123")

    def fake(conn, cid, lc, pr, now, **kw):
        raise ExternalError("LLM 端点错误 HTTP 401：unauthorized")

    monkeypatch.setattr(generate_article, "generate", fake)
    result = scoop_pipeline.run_generation_pipeline(["c1"], cfg)
    assert "SUPERSECRET123" not in json.dumps(result, ensure_ascii=False)


def test_generated_package_enters_packages_console(tmp_path, monkeypatch):
    cfg, cfgp = _cfg(tmp_path)
    monkeypatch.setattr(generate_article, "generate",
                        lambda conn, cid, lc, pr, now, **kw: _fake_item(cid, now))
    result = scoop_pipeline.run_generation_pipeline(["c1"], cfg)
    post_id = result["built"][0]["post_id"]
    client = TestClient(create_app(cfgp))
    r = client.get("/packages")
    assert r.status_code == 200
    assert post_id in r.text          # generated package shows up for manual review


def test_generate_route_zero_selection_rejected(tmp_path):
    _, cfgp = _cfg(tmp_path)
    client = TestClient(create_app(cfgp))
    r = client.post("/today/generate", data={})
    assert r.status_code == 400
