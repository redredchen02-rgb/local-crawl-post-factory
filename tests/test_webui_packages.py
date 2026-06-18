"""WebUI staged-package list (Unit 6) + no-publish-endpoint guard (W6)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config


def _client(tmp_path, out_dir):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com", "out_dir": str(out_dir)})
    return TestClient(create_app(str(cfgp)))


def _pkg(out_dir, post_id, title, status="package_built", caption="文案內容"):
    d = Path(out_dir) / post_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"post_id": post_id, "content": {"title": title},
         "source": {"canonical_url": f"https://example.com/{post_id}"},
         "backend": {"status": status}}),
        encoding="utf-8")
    (d / "caption.txt").write_text(caption, encoding="utf-8")


def test_lists_packages(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    _pkg(out, "20260615_b", "乙文")
    client = _client(tmp_path, out)
    r = client.get("/packages")
    assert r.status_code == 200
    assert "甲文" in r.text and "乙文" in r.text
    assert "package_built" in r.text


def test_empty_state(tmp_path):
    client = _client(tmp_path, tmp_path / "out")
    r = client.get("/packages")
    assert r.status_code == 200
    assert "尚無上膛貼文" in r.text


def test_broken_manifest_does_not_crash(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_ok", "好文")
    bad = out / "20260615_bad"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{ not json", encoding="utf-8")
    client = _client(tmp_path, out)
    r = client.get("/packages")
    assert r.status_code == 200
    assert "好文" in r.text  # good one still listed


def test_detail_page_shows_caption_and_source(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", caption="這是甲文的文案")
    client = _client(tmp_path, out)
    r = client.get("/packages/20260615_a")
    assert r.status_code == 200
    assert "這是甲文的文案" in r.text
    assert "https://example.com/20260615_a" in r.text
    assert "/history?post_id=20260615_a" in r.text
    # publish guidance is shown as CLI text, not an action
    assert "publish-post" in r.text and "--approve" in r.text


def test_detail_renders_legacy_manifest_with_media(tmp_path):
    """R5 backward-compat: an OLD package whose manifest still carries a media
    section (cover_path/watermarked_cover_path) must still load and render the
    detail page without error — reviewed.content_id + template must not depend
    on the removed fields — and no cover is served."""
    out = tmp_path / "out"
    d = out / "20230317_legacy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "post_id": "20230317_legacy",
        "content": {"title": "舊文", "body": "舊文案"},
        "source": {"canonical_url": "https://example.com/legacy"},
        "media": {"cover_path": "./cover.jpg",
                  "watermarked_cover_path": "./watermarked_cover.jpg"},
        "backend": {"status": "package_built"},
    }), encoding="utf-8")
    (d / "caption.txt").write_text("舊文案", encoding="utf-8")
    client = _client(tmp_path, out)
    r = client.get("/packages/20230317_legacy")
    assert r.status_code == 200
    assert "舊文案" in r.text
    assert "/packages/20230317_legacy/cover" not in r.text  # no cover URL emitted


def test_detail_unknown_404(tmp_path):
    client = _client(tmp_path, tmp_path / "out")
    assert client.get("/packages/nope").status_code == 404


def test_detail_path_traversal_blocked(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    # encoded traversal must not escape out_dir
    assert client.get("/packages/..%2f..%2fetc").status_code == 404


def test_detail_shows_failure_evidence(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_f", "失敗文")
    pkg = out / "20260615_f"
    shot = pkg / "failure_draft_x.png"
    shot.write_bytes(b"\x89PNG\r\n")
    (pkg / "failure.json").write_text(json.dumps({
        "stage": "draft", "url": "https://example.com/admin/login",
        "error": "draft did not confirm", "screenshot": str(shot), "ts": "t"}),
        encoding="utf-8")
    client = _client(tmp_path, out)
    r = client.get("/packages/20260615_f")
    assert r.status_code == 200
    assert "上次後台動作失敗" in r.text and "draft did not confirm" in r.text
    img = client.get("/packages/20260615_f/failure-image")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")


def test_failure_image_traversal_blocked(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_f", "文")
    pkg = out / "20260615_f"
    # failure.json points outside the package dir -> must not be served
    (pkg / "failure.json").write_text(json.dumps({
        "stage": "draft", "screenshot": "/etc/hosts"}), encoding="utf-8")
    client = _client(tmp_path, out)
    assert client.get("/packages/20260615_f/failure-image").status_code == 404


def test_filter_by_status(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", status="package_built")
    _pkg(out, "20260615_b", "乙文", status="draft_verified")
    client = _client(tmp_path, out)
    r = client.get("/packages?status=draft_verified")
    assert r.status_code == 200
    assert "乙文" in r.text and "甲文" not in r.text


def test_filter_by_query_matches_title_and_id(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "貓咪日報")
    _pkg(out, "20260615_b", "狗狗新聞")
    client = _client(tmp_path, out)
    assert "貓咪日報" in client.get("/packages?q=貓").text
    assert "狗狗新聞" not in client.get("/packages?q=貓").text
    # query also matches post_id
    assert "貓咪日報" in client.get("/packages?q=615_a").text


def test_empty_query_returns_all(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    _pkg(out, "20260615_b", "乙文")
    client = _client(tmp_path, out)
    r = client.get("/packages?q=")
    assert "甲文" in r.text and "乙文" in r.text


def test_delete_moves_package_to_trash(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/delete")
    assert r.status_code == 200
    # original gone, archived under .trash (reversible)
    assert not (out / "20260615_a").exists()
    assert (out / ".trash" / "20260615_a" / "manifest.json").exists()
    # list no longer shows it
    assert "甲文" not in client.get("/packages").text


def test_delete_path_traversal_blocked(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    assert client.post("/packages/..%2f..%2fetc/delete").status_code == 404
    # nothing moved
    assert (out / "20260615_a").exists()
    assert not (out / ".trash").exists()


def test_delete_unknown_404(tmp_path):
    client = _client(tmp_path, tmp_path / "out")
    assert client.post("/packages/nope/delete").status_code == 404


def test_delete_trash_dir_itself_blocked(tmp_path):
    """Crafted POST for a dot-dir (e.g. .trash) must be rejected, not nested into itself."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/delete")  # creates out/.trash
    assert client.post("/packages/.trash/delete").status_code == 404
    assert not (out / ".trash" / ".trash").exists()


def test_trash_dir_not_listed_as_package(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/delete")
    # .trash holds a manifest.json but must never appear in the staged list
    r = client.get("/packages")
    assert "尚無上膛貼文" in r.text


def test_publish_endpoint_is_gated_not_absent(tmp_path):
    """Control-center model: a publish route exists but is gated (see
    test_webui_publish_gate). It must never publish without the triple gate."""
    client = _client(tmp_path, tmp_path / "out")
    schema = client.get("/openapi.json").json()
    assert "/packages/{post_id}/publish" in schema["paths"]


def test_default_view_hides_published(tmp_path):
    """Default (status='') excludes published; only actionable packages shown."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "未發布文", status="package_built")
    _pkg(out, "20260615_b", "已發布文", status="published")
    client = _client(tmp_path, out)
    r = client.get("/packages")
    assert "未發布文" in r.text
    assert "已發布文" not in r.text


def test_all_status_shows_published(tmp_path):
    """status='all' reveals published packages too."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "未發布文", status="package_built")
    _pkg(out, "20260615_b", "已發布文", status="published")
    client = _client(tmp_path, out)
    r = client.get("/packages?status=all")
    assert "未發布文" in r.text
    assert "已發布文" in r.text


def test_batch_delete_moves_selected_to_trash(tmp_path):
    """POST /batch/delete moves checked items to .trash; unchecked remain."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    _pkg(out, "20260615_b", "乙文")
    client = _client(tmp_path, out)
    r = client.post("/batch/delete", data={"post_ids": ["20260615_a"]})
    assert r.status_code == 200
    assert not (out / "20260615_a").exists()
    assert (out / ".trash" / "20260615_a").exists()
    assert (out / "20260615_b").exists()  # untouched


def test_trash_list_shows_trashed_items(tmp_path):
    """/trash lists packages moved to .trash with their titles."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/delete")
    r = client.get("/trash")
    assert r.status_code == 200
    assert "甲文" in r.text


def test_trash_restore_moves_back_to_out(tmp_path):
    """/trash/{id}/restore moves the package back; no longer in .trash."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/delete")
    r = client.post("/trash/20260615_a/restore")
    assert r.status_code == 200
    assert (out / "20260615_a" / "manifest.json").exists()
    assert not (out / ".trash" / "20260615_a").exists()


def test_trash_restore_unknown_404(tmp_path):
    client = _client(tmp_path, tmp_path / "out")
    assert client.post("/trash/nope/restore").status_code == 404


def test_trash_restore_conflict_409(tmp_path):
    """Restore fails with 409 when a live package with the same id already exists."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/delete")          # now in .trash
    _pkg(out, "20260615_a", "重建的甲文")               # re-create live package
    r = client.post("/trash/20260615_a/restore")
    assert r.status_code == 409
    assert (out / ".trash" / "20260615_a").exists()     # still in trash


def test_trash_empty_clears_all(tmp_path):
    """POST /trash/empty permanently deletes everything in .trash."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    _pkg(out, "20260615_b", "乙文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/delete")
    client.post("/packages/20260615_b/delete")
    r = client.post("/trash/empty")
    assert r.status_code == 200
    assert not (out / ".trash").exists() or not any((out / ".trash").iterdir())


def test_edit_updates_title(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "舊標題")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/edit", data={"title": "新標題"})
    assert r.status_code == 200
    m = json.loads((out / "20260615_a" / "manifest.json").read_text(encoding="utf-8"))
    assert m["content"]["title"] == "新標題"


def test_edit_updates_caption(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/edit", data={"caption": "新文案內容"})
    assert r.status_code == 200
    assert (out / "20260615_a" / "caption.txt").read_text(encoding="utf-8") == "新文案內容"


def test_edit_empty_both_400(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/edit", data={"title": "", "caption": ""})
    assert r.status_code == 400


def test_edit_unknown_404(tmp_path):
    out = tmp_path / "out"
    out.mkdir(parents=True)
    client = _client(tmp_path, out)
    r = client.post("/packages/nonexistent/edit", data={"title": "x"})
    assert r.status_code == 404


def test_settings_shows_diagnostics(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com"})
    client = TestClient(create_app(str(cfgp)))
    r = client.get("/settings")
    assert r.status_code == 200
    assert "診斷" in r.text
    assert str(cfgp) in r.text


def test_footer_shows_version(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com"})
    client = TestClient(create_app(str(cfgp)))
    r = client.get("/settings")
    assert "local-crawl-post-factory v" in r.text


def test_detail_shows_history_link(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", status="drafted")
    client = _client(tmp_path, out)
    r = client.get("/packages/20260615_a")
    assert r.status_code == 200
    assert "運行歷史" in r.text
    assert "/history?post_id=20260615_a" in r.text


def test_rollback_published_package(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", status="published")
    receipt = {"post_id": "20260615_a", "published_url": "https://example.com/p/1",
               "published_at": "2026-06-18T12:00:00"}
    pkg_dir = out / "20260615_a"
    (pkg_dir / "publish_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/rollback")
    assert r.status_code == 200
    assert "ok" in r.text
    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["backend"]["status"] == "draft_verified"
    assert not (pkg_dir / "publish_receipt.json").exists()


def test_rollback_non_published_rejected(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_b", "乙文", status="draft_verified")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_b/rollback")
    assert r.status_code == 400
    assert "error" in r.text


def test_rollback_unknown_404(tmp_path):
    client = _client(tmp_path, tmp_path / "out")
    assert client.post("/packages/nope/rollback").status_code == 404
