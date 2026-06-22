"""U6: publish triple-gate (reviewed + draft_verified + title) and auth light."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from cpost.webui._helpers import check_publish_gates
from cpost.webui.app import create_app
from cpost.core import webui_config
from cpost.cli import publish_post


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
    monkeypatch.setattr(publish_post, "run", lambda ns: called.__setitem__("n", called["n"] + 1))
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/publish", data={"title": "待發貼文"})
    assert r.status_code == 200
    for _ in range(100):
        if called["n"]:
            break
        time.sleep(0.02)
    assert called["n"] == 1


def test_check_publish_gates_pure():
    """U10: the extracted pure gate decision, unit-tested without the app.
    Order is ①→②→③, so an earlier failing gate masks later ones."""
    ok = ("cid", "cid", "draft_verified", "T", "T")
    assert check_publish_gates(*ok) is None
    # ① no marker / content changed -> review message (and masks wrong title/status)
    assert "審核頁" in check_publish_gates(None, "cid", "draft_verified", "X", "T")
    assert "審核頁" in check_publish_gates("old", "new", "package_built", "X", "T")
    # ② verified gate
    assert "尚未驗證" in check_publish_gates("cid", "cid", "package_built", "T", "T")
    # ③ title gate
    assert "標題不符" in check_publish_gates("cid", "cid", "draft_verified", "X", "T")


def _wait(flag):
    for _ in range(100):
        if flag["n"]:
            return
        time.sleep(0.02)


def test_publish_after_content_change_400(tmp_path):
    """Q9 security core: reviewing then editing the content invalidates the review
    (content-binding, fail-closed) -- even when the typed title matches."""
    client, pkg = _client(tmp_path)
    _review(client)
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    m["content"]["title"] = "改過的標題"  # content changed after review (re-render)
    (pkg / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    r = client.post("/packages/20260615_demo/publish", data={"title": "改過的標題"})
    assert r.status_code == 400
    assert "內容已變更" in r.text


def test_publish_survives_lifecycle_save(tmp_path, monkeypatch):
    """Q9 mtime-trap guard: a manifest re-save that changes status/audit but NOT
    the content subtree keeps the review valid (gate ① still passes)."""
    called = {"n": 0}
    monkeypatch.setattr(publish_post, "run", lambda ns: called.__setitem__("n", called["n"] + 1))
    client, pkg = _client(tmp_path)
    _review(client)
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    m.setdefault("audit", {})["updated_at"] = "2026-06-15T12:00:00Z"  # lifecycle noise
    m["backend"]["run_id"] = "r-xyz"
    (pkg / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    r = client.post("/packages/20260615_demo/publish", data={"title": "待發貼文"})
    assert r.status_code == 200
    _wait(called)
    assert called["n"] == 1


def test_review_persists_across_restart(tmp_path, monkeypatch):
    """Q9: the reviewed marker is persisted, so a fresh app (restart) still passes
    gate ① without re-opening the review page."""
    called = {"n": 0}
    monkeypatch.setattr(publish_post, "run", lambda ns: called.__setitem__("n", called["n"] + 1))
    client, _ = _client(tmp_path)
    _review(client)
    client2 = TestClient(create_app(str(tmp_path / "webui.yaml")))  # 'restart'
    r = client2.post("/packages/20260615_demo/publish", data={"title": "待發貼文"})
    assert r.status_code == 200
    _wait(called)
    assert called["n"] == 1


def test_auth_light_three_states(tmp_path):
    client, _ = _client(tmp_path)
    # no storage-state file -> grey/none
    r = client.get("/auth-status")
    assert "未設定" in r.text
    # create storage-state -> ok/green
    Path(tmp_path / "ss.json").write_text("{}", encoding="utf-8")
    assert "有效" in client.get("/auth-status").text
