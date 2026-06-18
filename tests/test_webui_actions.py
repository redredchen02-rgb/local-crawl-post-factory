"""U5: UI backend-action endpoints (draft/verify) drive the real mock admin."""

import json
import re
import time

import pytest
import yaml
pytestmark = pytest.mark.browser  # Playwright end-to-end; excluded from fast-run

from fastapi.testclient import TestClient  # noqa: E402

from webui.app import create_app  # noqa: E402
from core import webui_config  # noqa: E402
from tests.mock_admin import MockAdmin  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")
try:
    with playwright.sync_playwright() as _pw:
        _b = _pw.chromium.launch(headless=True)
        _b.close()
except Exception as exc:  # pragma: no cover
    pytest.skip(f"chromium unavailable: {exc}", allow_module_level=True)


def _setup(tmp_path, mock):
    out = tmp_path / "out"
    pkg = out / "20260615_demo"
    pkg.mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (16, 16), "white").save(pkg / "watermarked_cover.jpg")
    (pkg / "manifest.json").write_text(json.dumps({
        "post_id": "20260615_demo",
        "source": {"canonical_url": "https://example.com/news/a"},
        "content": {"title": "動作測試", "body": "內文", "tags": [], "category": None},
        "media": {"watermarked_cover_path": "./watermarked_cover.jpg"},
        "backend": {"status": "package_built", "draft_url": None,
                    "published_url": None, "remote_id": None},
        "audit": {},
    }), encoding="utf-8")
    backend = tmp_path / "backend.yaml"
    backend.write_text(yaml.safe_dump(mock.backend_cfg()), encoding="utf-8")
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com", "out_dir": str(out),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
        "backend_config": str(backend), "storage_state": str(tmp_path / "nostate.json"),
    })
    return TestClient(create_app(str(cfgp))), pkg


def _wait_done(client, html):
    jid = re.search(r"/jobs/([0-9a-f]+)", html)
    if not jid:
        return html
    jid = jid.group(1)
    for _ in range(120):
        s = client.get(f"/jobs/{jid}").text
        if "完成" in s or "失敗" in s:
            return s
        time.sleep(0.05)
    return s


def test_draft_then_verify_via_ui(tmp_path):
    with MockAdmin() as mock:
        client, pkg = _setup(tmp_path, mock)
        r = client.post("/packages/20260615_demo/draft")
        assert r.status_code == 200
        assert "完成" in _wait_done(client, r.text)
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        assert m["backend"]["status"] == "drafted"

        r = client.post("/packages/20260615_demo/verify")
        assert "完成" in _wait_done(client, r.text)
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        assert m["backend"]["status"] == "draft_verified"


def test_draft_unknown_package_404(tmp_path):
    with MockAdmin() as mock:
        client, _ = _setup(tmp_path, mock)
        assert client.post("/packages/nope/draft").status_code == 404
