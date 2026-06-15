"""U6: publish triple-gate (reviewed + draft_verified + title) and auth light."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config
from src import publish_post


def _client(tmp_path, status="draft_verified", title="待發貼文"):
    out = tmp_path / "out"
    pkg = out / "20260615_demo"
    pkg.mkdir(parents=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "post_id": "20260615_demo",
        "content": {"title": title},
        "backend": {"status": status},
    }), encoding="utf-8")
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com", "out_dir": str(out),
        "state_path": str(tmp_path / "state.sqlite"),
        "storage_state": str(tmp_path / "ss.json")})
    return TestClient(create_app(str(cfgp))), pkg


def _review(client):
    client.get("/packages/20260615_demo")  # marks reviewed


def test_publish_without_review_400(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/packages/20260615_demo/publish", data={"title": "待發貼文"})
    assert r.status_code == 400
    assert "審核頁" in r.text


def test_publish_not_verified_400(tmp_path):
    client, _ = _client(tmp_path, status="package_built")
    _review(client)
    r = client.post("/packages/20260615_demo/publish", data={"title": "待發貼文"})
    assert r.status_code == 400
    assert "尚未驗證" in r.text


def test_publish_wrong_title_400(tmp_path):
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/publish", data={"title": "錯的標題"})
    assert r.status_code == 400
    assert "標題不符" in r.text


def test_publish_all_gates_pass_submits_job(tmp_path, monkeypatch):
    # Avoid a real browser: stub the publish command; gate is what we assert.
    called = {"n": 0}
    monkeypatch.setattr(publish_post, "_run", lambda ns: called.__setitem__("n", called["n"] + 1))
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/publish", data={"title": "待發貼文"})
    assert r.status_code == 200
    for _ in range(100):
        if called["n"]:
            break
        time.sleep(0.02)
    assert called["n"] == 1


def test_auth_light_three_states(tmp_path):
    client, _ = _client(tmp_path)
    # no storage-state file -> grey/none
    r = client.get("/auth-status")
    assert "未設定" in r.text
    # create storage-state -> ok/green
    Path(tmp_path / "ss.json").write_text("{}", encoding="utf-8")
    assert "有效" in client.get("/auth-status").text
