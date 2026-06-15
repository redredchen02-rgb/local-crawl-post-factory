"""U4: WebUI run-history and audit views."""

from pathlib import Path

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config, runs


def _client(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com",
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })
    return TestClient(create_app(str(cfgp))), tmp_path


def test_history_lists_runs(tmp_path):
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="publish", post_id="p1",
                    status="ok", detail="https://example.com/p1")
    r = client.get("/history")
    assert r.status_code == 200
    assert "publish" in r.text and "p1" in r.text


def test_history_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/history")
    assert r.status_code == 200
    assert "尚無運行紀錄" in r.text


def test_audit_shows_lines(tmp_path):
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{"ts":"2026-06-15T02:00:00Z","post_id":"p1","stage":"draft-post","status":"ok"}\n',
        encoding="utf-8")
    r = client.get("/audit")
    assert r.status_code == 200
    assert "draft-post" in r.text


def test_audit_skips_broken_lines(tmp_path):
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{ broken\n{"ts":"t","stage":"publish-post","status":"ok","post_id":"p2"}\n',
        encoding="utf-8")
    r = client.get("/audit")
    assert r.status_code == 200
    assert "publish-post" in r.text  # good line shown, broken skipped


def test_audit_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/audit")
    assert r.status_code == 200
    assert "尚無 audit" in r.text
