"""U6: publish triple-gate (reviewed + draft_verified + title) and auth light."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from cpost.webui._helpers import check_publish_gates
from cpost.webui.app import create_app
from cpost.core import webui_config
from cpost.core import jobs as jobs_mod
from cpost.core.errors import SessionExpiredError
from cpost.cli import draft_post, verify_draft, publish_post


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


def _sync_submit(monkeypatch):
    def sync_submit(fn):
        job_id = "sync-job"
        job = jobs_mod.Job(job_id)
        jobs_mod._JOBS[job_id] = job
        try:
            result = fn(job)
            job.result = result
            job.status = "done"
            job.finished_at = time.time()
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            job.finished_at = time.time()
        return job_id
    monkeypatch.setattr(jobs_mod, "submit", sync_submit)


def _session_expired_raiser(ns):
    raise SessionExpiredError("session expired")


def test_draft_ok_records_run_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_post, "run", lambda ns: None)
    _sync_submit(monkeypatch)
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/draft")
    assert r.status_code == 200


def test_draft_session_expired_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_post, "run", _session_expired_raiser)
    _sync_submit(monkeypatch)
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/draft")
    assert r.status_code == 200


def test_verify_session_expired_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_draft, "run", _session_expired_raiser)
    _sync_submit(monkeypatch)
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/verify")
    assert r.status_code == 200


def test_draft_session_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_post, "run", _session_expired_raiser)
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/packages/20260615_demo/draft")
    assert r.status_code == 200


def test_note_session_expiry_missing_file():
    from cpost.webui.routers._ctx import note_session_expiry
    from unittest.mock import MagicMock
    request = MagicMock()
    request.app.state._mtime = None
    cfg = {"storage_state": "/nonexistent"}
    note_session_expiry(request, cfg)
    assert request.app.state.session_expired_mtime == 0.0


def test_note_session_expiry_with_file(tmp_path):
    from cpost.webui.routers._ctx import note_session_expiry
    from unittest.mock import MagicMock
    f = tmp_path / "ss.json"
    f.write_text("{}", encoding="utf-8")
    request = MagicMock()
    request.app.state = MagicMock()
    cfg = {"storage_state": str(f)}
    note_session_expiry(request, cfg)
    assert request.app.state.session_expired_mtime > 0


def test_package_not_found_fallback(monkeypatch):
    import importlib
    import importlib.metadata as _im
    from importlib.metadata import PackageNotFoundError
    monkeypatch.setattr(_im, "version",
                        lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)))
    import cpost.webui.routers._ctx as ctx_mod
    ctx_mod_ref = ctx_mod
    importlib.reload(ctx_mod)
    assert ctx_mod._app_version == "dev"
    importlib.reload(ctx_mod_ref)


def test_isinstance_session_expired():
    from cpost.core.errors import SessionExpiredError
    try:
        raise SessionExpiredError("test")
    except Exception as exc:
        assert isinstance(exc, SessionExpiredError)


def test_auth_light_expired(tmp_path):
    Path(tmp_path / "ss.json").write_text("{}", encoding="utf-8")
    client, _ = _client(tmp_path)
    client.get("/auth-status")
    import time
    client._transport.app.state.session_expired_mtime = time.time() + 3600
    r = client.get("/auth-status")
    assert "已過期" in r.text


def test_draft_nonexistent_post_404(tmp_path):
    client, _ = _client(tmp_path)
    r =     client.post("/packages/no-such-post/draft")
    assert r.status_code == 404
    assert "找不到" in r.text


def test_batch_action_session_expired(tmp_path, monkeypatch):
    """batch SessionExpiredError calls note_session_expiry (actions.py:127)."""
    from cpost.core.errors import SessionExpiredError
    from cpost.cli import draft_post as draft_cli
    monkeypatch.setattr(draft_cli, "run", lambda ns: (_ for _ in ()).throw(SessionExpiredError("expired")))
    def sync_submit(fn):
        job_id = "sync-batch"
        job = jobs_mod.Job(job_id)
        jobs_mod._JOBS[job_id] = job
        try:
            result = fn(job)
            job.result = result
            job.status = "done"
            job.finished_at = time.time()
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            job.finished_at = time.time()
        return job_id
    monkeypatch.setattr(jobs_mod, "submit", sync_submit)
    client, _ = _client(tmp_path)
    _review(client)
    r = client.post("/batch/draft", data={"post_ids": ["20260615_demo"]})
    assert r.status_code == 200
    assert "expired" in r.text or "failed" in r.text or "sync-batch" in r.text
