"""WebUI one-click crawl→stage (Unit 4) with crawl injected (no network)."""

import time

from fastapi.testclient import TestClient

from cpost.webui.app import create_app
from cpost.core import webui_config, pipeline, jobs as jobs_mod


def _client(tmp_path, monkeypatch):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com/news",
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    def fake_crawl(cfg, **kwargs):
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
        client.get(f"/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert jobs_mod.get(jid)["status"] == "done"
    assert (tp / "out").glob("*/manifest.json")
    built = list((tp / "out").glob("*/manifest.json"))
    assert len(built) == 2


def test_crawl_without_start_url_400(tmp_path, monkeypatch):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": ""})
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    assert r.status_code == 400


def test_crawl_current_updates(tmp_path, monkeypatch):
    from cpost.core import jobs as jobs_mod

    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com/news",
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    def fake_crawl(cfg, progress_cb=None, poll_sec=0.5, **_kw):
        if progress_cb:
            progress_cb({"responses": 3, "items": 2, "last_url": "https://ex.com/a",
                         "last_title": "標題甲"})
        return [
            {"source_id": "example.com", "url": "https://example.com/news/a",
             "canonical_url": "https://example.com/news/a", "title": "標題甲",
             "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"},
        ]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    jid = _job_id(r.text)
    assert jid

    for _ in range(100):
        client.get(f"/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert jobs_mod.get(jid)["status"] == "done"

    job = jobs_mod.get(jid)
    assert job is not None
    assert job["current"] == "建包中…"
    assert any("爬取完成" in m for m in job["progress"])


def test_crawl_auto_pipeline_result_renders(tmp_path, monkeypatch):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com/news",
        "auto_pipeline": True,
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    built = [{"post_id": "p1", "title": "標題甲",
              "manifest_path": str(tmp_path / "out" / "p1" / "manifest.json")}]
    monkeypatch.setattr(pipeline, "crawl_items", lambda cfg, **kwargs: [])
    monkeypatch.setattr(pipeline, "run_pipeline",
                        lambda items, cfg, progress_cb=None:
                        {"built": built, "failed": [], "skipped": 0})

    def fake_auto(job, cfg, built_items, **kwargs):
        time.sleep(0.1)
        return {"ok": 1, "failed": [], "verify_fail_count": 0}

    monkeypatch.setattr("cpost.webui.routers.crawl._run_auto_pipeline", fake_auto)
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    jid = _job_id(r.text)
    assert jid

    for _ in range(100):
        done = client.get(f"/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)

    assert jobs_mod.get(jid)["status"] == "done"
    assert "自動發布完成" in done.text
    assert "成功 1" in done.text


def test_job_not_found_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.get("/jobs/deadbeef").status_code == 404


# --- U2 (R2): multi-source routing + preconditions ---------------------------

def test_crawl_two_sources_both_ingest(tmp_path, monkeypatch):
    """Two enabled sources -> both crawled, merged items build two packages,
    and each source's per-source summary lands on the job progress (on_source)."""
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "",
        "sources": [
            {"source_id": "src_a", "start_url": "https://a.com/"},
            {"source_id": "src_b", "start_url": "https://b.com/"},
        ],
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    def fake_crawl(cfg, progress_cb=None, poll_sec=0.5, **_kw):
        sid = cfg["source_id"]
        return [{"source_id": sid, "url": f"https://{sid}/1",
                 "canonical_url": f"https://{sid}/1", "title": f"標題 {sid}",
                 "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    jid = _job_id(r.text)
    assert jid
    for _ in range(100):
        client.get(f"/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    job = jobs_mod.get(jid)
    assert job["status"] == "done"
    assert len(list((tmp_path / "out").glob("*/manifest.json"))) == 2
    assert any("src_a" in m for m in job["progress"])
    assert any("src_b" in m for m in job["progress"])


def test_crawl_disabled_source_skipped(tmp_path, monkeypatch):
    """A disabled source is skipped; only the enabled one builds a package."""
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "",
        "sources": [
            {"source_id": "src_a", "start_url": "https://a.com/", "enabled": False},
            {"source_id": "src_b", "start_url": "https://b.com/"},
        ],
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    crawled = []

    def fake_crawl(cfg, progress_cb=None, poll_sec=0.5, **_kw):
        sid = cfg["source_id"]
        crawled.append(sid)
        return [{"source_id": sid, "url": f"https://{sid}/1",
                 "canonical_url": f"https://{sid}/1", "title": f"標題 {sid}",
                 "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    jid = _job_id(r.text)
    for _ in range(100):
        client.get(f"/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert jobs_mod.get(jid)["status"] == "done"
    assert crawled == ["src_b"]
    assert len(list((tmp_path / "out").glob("*/manifest.json"))) == 1


def test_crawl_source_failure_does_not_crash_crawl_cb(tmp_path, monkeypatch):
    """arch F1 regression: a per-source failure must reach on_source (job
    progress) without ever passing a str to the dict-shaped _crawl_cb."""
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "",
        "sources": [
            {"source_id": "boom", "start_url": "https://boom.com/"},
            {"source_id": "ok", "start_url": "https://ok.com/"},
        ],
        "out_dir": str(tmp_path / "out"),
        "download_dir": str(tmp_path / "assets"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })

    def fake_crawl(cfg, progress_cb=None, poll_sec=0.5, **_kw):
        sid = cfg["source_id"]
        if sid == "boom":
            raise RuntimeError("kaboom")
        return [{"source_id": sid, "url": f"https://{sid}/1",
                 "canonical_url": f"https://{sid}/1", "title": "標題",
                 "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    jid = _job_id(r.text)
    for _ in range(100):
        client.get(f"/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    job = jobs_mod.get(jid)
    assert job["status"] == "done"  # job did not crash despite the str failure
    assert any("boom" in m and "failed" in m for m in job["progress"])
    assert len(list((tmp_path / "out").glob("*/manifest.json"))) == 1


def test_crawl_no_source_no_start_url_400(tmp_path):
    """State (a): no start_url and no enabled source -> add-a-source copy."""
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "", "sources": []})
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    assert r.status_code == 400
    assert "新增至少一個來源" in r.text


def test_crawl_all_disabled_400(tmp_path):
    """State (b): sources exist but all disabled (and no start_url) -> all-disabled copy."""
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "",
        "sources": [{"source_id": "a", "start_url": "https://a.com/", "enabled": False}],
    })
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    assert r.status_code == 400
    assert "停用" in r.text


def test_crawl_malformed_sources_list_of_strings_400(tmp_path):
    """A hand-edited config with sources entries that are strings (not mappings)
    must yield a clean 400, not a 500 from a later .get-on-str AttributeError."""
    # Hand-edit the yaml: webui_config.save would itself reject this, so we write
    # past it to simulate a malformed on-disk config reaching the router.
    cfgp = tmp_path / "webui.yaml"
    cfgp.write_text(
        "start_url: ''\n"
        "sources:\n"
        "  - just-a-string\n"
        "  - another-string\n",
        encoding="utf-8")
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    assert r.status_code == 400
    assert "設定錯誤" in r.text


def test_crawl_sources_as_string_400(tmp_path):
    """sources is a scalar string (not a list) -> clean 400, not 500."""
    cfgp = tmp_path / "webui.yaml"
    cfgp.write_text(
        "start_url: ''\n"
        "sources: not-a-list\n",
        encoding="utf-8")
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/crawl")
    assert r.status_code == 400
    assert "設定錯誤" in r.text
