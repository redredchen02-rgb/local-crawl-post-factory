"""U10 characterization: path-traversal rejection on every post_id route, plus
publish gate ordering. Written against the monolith first as a pre-split anchor;
these MUST stay green after webui/app.py is split into routers (U10).

TestClient-only (no chromium), so this anchor runs in CI Job A as well.
"""

import json

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config

_TRAVERSAL = "..%2f..%2fetc"  # encoded ../../etc -> _safe_pkg_dir must reject


def _client(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com", "out_dir": str(tmp_path / "out"),
        "state_path": str(tmp_path / "state.sqlite"),
        "storage_state": str(tmp_path / "ss.json")})
    return TestClient(create_app(str(cfgp)))


def test_draft_traversal_blocked(tmp_path):
    assert _client(tmp_path).post(f"/packages/{_TRAVERSAL}/draft").status_code == 404


def test_verify_traversal_blocked(tmp_path):
    assert _client(tmp_path).post(f"/packages/{_TRAVERSAL}/verify").status_code == 404


def test_publish_traversal_blocked(tmp_path):
    r = _client(tmp_path).post(f"/packages/{_TRAVERSAL}/publish", data={"title": "x"})
    assert r.status_code == 404


def test_dot_dir_post_id_blocked(tmp_path):
    # dot-dirs (e.g. .trash) must never resolve as a package on a post_id route
    c = _client(tmp_path)
    assert c.get("/packages/.trash/failure-image").status_code == 404
    assert c.post("/packages/.trash/publish", data={"title": "x"}).status_code == 404


def test_batch_delete_traversal_skipped(tmp_path):
    """Traversal post_id in batch/delete must be treated as not-found, not crash."""
    r = _client(tmp_path).post("/batch/delete", data={"post_ids": [_TRAVERSAL]})
    assert r.status_code == 200
    assert "移入垃圾桶：0 篇" in r.text  # deleted 0


def test_trash_restore_traversal_blocked(tmp_path):
    """Traversal post_id on restore endpoint must be rejected."""
    assert _client(tmp_path).post(f"/trash/{_TRAVERSAL}/restore").status_code == 404


def test_trash_restore_dot_dir_blocked(tmp_path):
    """Dot-dir post_id on restore endpoint must be rejected."""
    assert _client(tmp_path).post("/trash/.evil/restore").status_code == 404


def test_publish_gate_order_reviewed_before_title(tmp_path):
    """Gate (1) (reviewed/content) is checked before gate (3) (title): an
    un-reviewed, draft_verified package submitted with a WRONG title returns the
    review message, not the title message -- proving gate (1) runs first."""
    pkg = tmp_path / "out" / "20260615_demo"
    pkg.mkdir(parents=True)
    pkg.joinpath("manifest.json").write_text(json.dumps({
        "post_id": "20260615_demo",
        "content": {"title": "正確標題"},
        "backend": {"status": "draft_verified"},
    }), encoding="utf-8")
    r = _client(tmp_path).post(
        "/packages/20260615_demo/publish", data={"title": "完全錯的標題"})
    assert r.status_code == 400
    assert "審核頁" in r.text         # gate (1) message
    assert "標題不符" not in r.text    # NOT the gate (3) message
