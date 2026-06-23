"""WebUI staged-package list (Unit 6) + no-publish-endpoint guard (W6)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import cpost
from cpost.webui.app import create_app
from cpost.webui.routers import packages as packages_router
from cpost.core import webui_config


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
    # U2 (R1): the publishable body — not just the displayed caption.txt — must
    # carry the edit, else publish (which reads content.body) ships stale content.
    m = json.loads((out / "20260615_a" / "manifest.json").read_text(encoding="utf-8"))
    assert m["content"]["body"] == "新文案內容"


def test_edit_caption_binds_review_gate_to_new_content(tmp_path):
    # The operator authored this version; the reviewed gate must point at the
    # edited content_id so publish is not blocked as stale (Q9).
    from cpost.core import reviewed
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    client.post("/packages/20260615_a/edit", data={"caption": "新文案內容"})
    m = json.loads((out / "20260615_a" / "manifest.json").read_text(encoding="utf-8"))
    state_path = webui_config.load(str(tmp_path / "webui.yaml"))["state_path"]
    assert reviewed.get(state_path, "20260615_a") == reviewed.content_id(m)


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
    assert f"{cpost.__dist_name__} v" in r.text


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


def test_rollback_clears_stale_published_url(tmp_path):
    """U12: after rollback the manifest must not report draft_verified while
    still carrying a stale published_url."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", status="published")
    pkg_dir = out / "20260615_a"
    m = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    m["backend"]["published_url"] = "https://example.com/p/1"
    (pkg_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/rollback")
    assert r.status_code == 200
    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["backend"]["status"] == "draft_verified"
    assert manifest["backend"]["published_url"] is None


def test_b1_rollback_clears_state_row(tmp_path):
    """B1 rollback fix: rollback_package must remove the 'published' state row so a
    subsequent re-publish triggers a fresh browser click, not a forward-complete."""
    from cpost.core import state as state_mod, url_utils
    state_db = str(tmp_path / "state.sqlite")
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", status="published")
    canonical_url = "https://example.com/20260615_a"
    # Pre-populate state row as if a prior publish succeeded
    with state_mod.connect(state_db) as conn:
        state_mod.upsert(conn, canonical_url=canonical_url, title="甲文",
                         title_hash=url_utils.title_hash("甲文"),
                         status=state_mod.PUBLISHED, now="2026-06-23T00:00:00",
                         published_url="https://example.com/live/1")
        assert state_mod.is_processed(conn, canonical_url)
    # Client with state_path in config
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com",
                                   "out_dir": str(out), "state_path": state_db})
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/packages/20260615_a/rollback")
    assert r.status_code == 200
    # State row must be gone — re-publish can now click fresh
    with state_mod.connect(state_db) as conn:
        assert not state_mod.is_processed(conn, canonical_url)
        assert not state_mod.is_publishing(conn, canonical_url)


# --- U14: generate_article dual-write consistency -----------------------------

def _stub_llm(monkeypatch, article):
    from cpost.core import llm
    monkeypatch.setattr(llm, "load_config", lambda _p: {})
    monkeypatch.setattr(llm, "load_system_prompt", lambda _c: "sys")
    monkeypatch.setattr(llm, "build_user_content", lambda _t, _m: "user")
    monkeypatch.setattr(llm, "chat", lambda _c, _s, _u: article)


def test_generate_article_dual_write_happy(tmp_path, monkeypatch):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", caption="原始素材")
    pkg = out / "20260615_a"
    (pkg / "source_text.txt").write_text("來源全文素材", encoding="utf-8")
    _stub_llm(monkeypatch, "AI 生成後的新文章")
    client = _client(tmp_path, out)
    r = client.post("/packages/20260615_a/generate")
    assert r.status_code == 200
    assert (pkg / "caption.txt").read_text(encoding="utf-8") == "AI 生成後的新文章"
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert m["content"]["body"] == "AI 生成後的新文章"


def test_generate_article_failed_second_write_keeps_pair_consistent(tmp_path, monkeypatch):
    """If the caption.txt write fails after the manifest is written, the body is
    rolled back so caption.txt and content.body stay consistent (no permanent
    desync)."""
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", caption="原始文案")
    pkg = out / "20260615_a"
    (pkg / "source_text.txt").write_text("來源全文素材", encoding="utf-8")
    # Seed a known body so we can assert it is restored.
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    m.setdefault("content", {})["body"] = "原始文案"
    (pkg / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    _stub_llm(monkeypatch, "新文章")

    real_atomic = packages_router.atomic_write_text

    def fail_on_caption(dest, text):
        if str(dest).endswith("caption.txt"):
            raise OSError("disk full")
        return real_atomic(dest, text)

    monkeypatch.setattr(packages_router, "atomic_write_text", fail_on_caption)
    client = _client(tmp_path, out)
    with pytest.raises(OSError, match="disk full"):
        client.post("/packages/20260615_a/generate")
    # caption.txt unchanged AND manifest body rolled back -> still consistent.
    assert (pkg / "caption.txt").read_text(encoding="utf-8") == "原始文案"
    m2 = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert m2["content"]["body"] == "原始文案"


# --- U19: failure-image path resolution ---------------------------------------

def test_failure_image_relative_path_in_package_served(tmp_path):
    """A relative screenshot path stored in failure.json must resolve against the
    package dir and be served (not 404)."""
    out = tmp_path / "out"
    _pkg(out, "20260615_f", "失敗文")
    pkg = out / "20260615_f"
    (pkg / "shot.png").write_bytes(b"\x89PNG\r\n")
    (pkg / "failure.json").write_text(json.dumps({
        "stage": "draft", "screenshot": "shot.png"}), encoding="utf-8")
    client = _client(tmp_path, out)
    img = client.get("/packages/20260615_f/failure-image")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")


def test_failure_image_relative_traversal_still_blocked(tmp_path):
    """A relative path escaping the package dir must still be rejected."""
    out = tmp_path / "out"
    _pkg(out, "20260615_f", "失敗文")
    pkg = out / "20260615_f"
    (pkg / "failure.json").write_text(json.dumps({
        "stage": "draft", "screenshot": "../escape.png"}), encoding="utf-8")
    # create the escape target so existence isn't what blocks it
    (out / "escape.png").write_bytes(b"\x89PNG\r\n")
    client = _client(tmp_path, out)
    assert client.get("/packages/20260615_f/failure-image").status_code == 404


# --- L2: multi-source provenance (source_id) display --------------------------

def _pkg_with_source(out_dir, post_id, title, source_id, status="package_built"):
    """Build a package whose manifest carries a source.source_id (provenance)."""
    d = Path(out_dir) / post_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"post_id": post_id, "content": {"title": title},
         "source": {"source_id": source_id,
                    "canonical_url": f"https://example.com/{post_id}"},
         "backend": {"status": status}}),
        encoding="utf-8")
    (d / "caption.txt").write_text("文案內容", encoding="utf-8")


def test_source_id_renders_in_list_and_detail(tmp_path):
    """Happy: a manifest with source_id shows it in both the list and detail."""
    out = tmp_path / "out"
    _pkg_with_source(out, "20260615_a", "甲文", "tech-blog")
    client = _client(tmp_path, out)
    assert "tech-blog" in client.get("/packages").text
    assert "tech-blog" in client.get("/packages/20260615_a").text


def test_missing_source_block_renders_placeholder(tmp_path):
    """Edge: a manifest without a source block must render '—' without error."""
    out = tmp_path / "out"
    d = out / "20260615_n"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"post_id": "20260615_n", "content": {"title": "無來源文"},
         "backend": {"status": "package_built"}}), encoding="utf-8")
    (d / "caption.txt").write_text("文案內容", encoding="utf-8")
    client = _client(tmp_path, out)
    r = client.get("/packages")
    assert r.status_code == 200
    assert "無來源文" in r.text and "—" in r.text
    d2 = client.get("/packages/20260615_n")
    assert d2.status_code == 200
    assert "—" in d2.text


def test_two_packages_show_distinct_source_ids(tmp_path):
    """Integration: each package's detail shows its own source_id."""
    out = tmp_path / "out"
    _pkg_with_source(out, "20260615_a", "甲文", "source-alpha")
    _pkg_with_source(out, "20260615_b", "乙文", "source-beta")
    client = _client(tmp_path, out)
    lst = client.get("/packages").text
    assert "source-alpha" in lst and "source-beta" in lst
    ra = client.get("/packages/20260615_a").text
    assert "source-alpha" in ra and "source-beta" not in ra
    rb = client.get("/packages/20260615_b").text
    assert "source-beta" in rb and "source-alpha" not in rb
