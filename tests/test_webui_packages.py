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


def _pkg(out_dir, post_id, title, status="package_built", caption="文案內容", cover=False):
    d = Path(out_dir) / post_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"post_id": post_id, "content": {"title": title},
         "source": {"canonical_url": f"https://example.com/{post_id}"},
         "backend": {"status": status}}),
        encoding="utf-8")
    (d / "caption.txt").write_text(caption, encoding="utf-8")
    if cover:
        from PIL import Image
        Image.new("RGB", (16, 16), "white").save(d / "watermarked_cover.jpg")


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
    _pkg(out, "20260615_a", "甲文", caption="這是甲文的文案", cover=True)
    client = _client(tmp_path, out)
    r = client.get("/packages/20260615_a")
    assert r.status_code == 200
    assert "這是甲文的文案" in r.text
    assert "https://example.com/20260615_a" in r.text
    # publish guidance is shown as CLI text, not an action
    assert "publish-post" in r.text and "--approve" in r.text


def test_detail_cover_served(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文", cover=True)
    client = _client(tmp_path, out)
    r = client.get("/packages/20260615_a/cover")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


def test_detail_unknown_404(tmp_path):
    client = _client(tmp_path, tmp_path / "out")
    assert client.get("/packages/nope").status_code == 404


def test_detail_path_traversal_blocked(tmp_path):
    out = tmp_path / "out"
    _pkg(out, "20260615_a", "甲文")
    client = _client(tmp_path, out)
    # encoded traversal must not escape out_dir
    assert client.get("/packages/..%2f..%2fetc").status_code == 404


def test_no_publish_endpoint_in_app(tmp_path):
    """W6: the WebUI must never expose a publish action."""
    source = Path("webui/app.py").read_text(encoding="utf-8")
    assert "publish_post" not in source
    assert "publish_draft" not in source
    # routes: ensure no path contains 'publish'
    app = create_app(str(tmp_path / "webui.yaml"))
    paths = [r.path for r in app.routes]
    assert not any("publish" in p for p in paths)
