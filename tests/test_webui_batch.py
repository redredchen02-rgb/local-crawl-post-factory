"""U8 (R8): batch draft/verify endpoint — isolation, run_id aggregation,
path-traversal safety, and no batch-publish entry."""

import json
import re
import time

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config, runs
from src import draft_post, verify_draft


def _mkpkg(out, post_id, status="package_built"):
    pkg = out / post_id
    pkg.mkdir(parents=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "post_id": post_id, "content": {"title": post_id},
        "backend": {"status": status},
    }), encoding="utf-8")
    return pkg


def _client(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com", "out_dir": str(out),
        "state_path": str(tmp_path / "state.sqlite"),
        "storage_state": str(tmp_path / "ss.json")})
    return TestClient(create_app(str(cfgp))), out, str(tmp_path / "state.sqlite")


def _await_done(client, html):
    jid = re.search(r"/jobs/([0-9a-f]+)", html)
    if not jid:
        return html
    jid = jid.group(1)
    for _ in range(100):
        s = client.get(f"/jobs/{jid}").text
        if "完成" in s or "失敗" in s:
            return s
        time.sleep(0.05)
    return s


def test_batch_draft_isolates_failures_and_shares_run_id(tmp_path, monkeypatch):
    client, out, state = _client(tmp_path)
    _mkpkg(out, "a")
    _mkpkg(out, "b")

    calls = []

    def fake_run(ns):
        # Fail exactly one item; the rest succeed -> isolation.
        if "/b/" in ns.manifest.replace("\\", "/"):
            raise RuntimeError("boom on b")
        calls.append(ns.manifest)

    monkeypatch.setattr(draft_post, "run", fake_run)

    r = client.post("/batch/draft", data={"post_ids": ["a", "b"]})
    text = _await_done(client, r.text)
    assert "完成" in text  # the job itself succeeds even with a failed item

    rows = [r for r in runs.list_runs(state) if r["stage"] == "draft"]
    assert {r["status"] for r in rows} == {"ok", "failed"}
    assert len({r["run_id"] for r in rows}) == 1  # one run_id for the batch


def test_batch_traversal_post_id_skipped(tmp_path, monkeypatch):
    client, out, state = _client(tmp_path)
    _mkpkg(out, "good")
    monkeypatch.setattr(verify_draft, "run", lambda ns: None)

    r = client.post("/batch/verify",
                    data={"post_ids": ["../../etc/passwd", "/abs/path", "good"]})
    text = _await_done(client, r.text)
    assert "完成" in text

    rows = {r["post_id"]: r["status"] for r in runs.list_runs(state)}
    assert rows["good"] == "ok"
    assert rows["../../etc/passwd"] == "failed"
    assert rows["/abs/path"] == "failed"


def test_batch_empty_selection_no_side_effects(tmp_path):
    client, out, state = _client(tmp_path)
    r = client.post("/batch/draft", data={"post_ids": []})
    assert r.status_code == 200
    assert "未選取" in r.text
    assert runs.list_runs(state) == []


def test_batch_publish_not_supported(tmp_path):
    client, out, state = _client(tmp_path)
    _mkpkg(out, "a")
    r = client.post("/batch/publish", data={"post_ids": ["a"]})
    assert r.status_code == 400
    assert runs.list_runs(state) == []
