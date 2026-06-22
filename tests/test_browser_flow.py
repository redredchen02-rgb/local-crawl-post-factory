"""End-to-end Phase 4-5 flow against a local mock admin (origin §14.3).

draft-post -> verify-draft -> publish-post --approve, asserting manifest state
transitions, the publish receipt, and the SQLite 'published' write (R9).
"""

import json

import pytest

pytestmark = pytest.mark.browser  # Playwright end-to-end; excluded from fast-run
import yaml  # noqa: E402

from cpost.core import cli, state as state_mod, url_utils  # noqa: E402
from tests.mock_admin import MockAdmin  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")
from cpost.cli import draft_post, verify_draft, publish_post  # noqa: E402

# Skip the whole module if the chromium binary is not installed.
try:
    with playwright.sync_playwright() as _pw:
        _b = _pw.chromium.launch(headless=True)
        _b.close()
except Exception as exc:  # pragma: no cover
    pytest.skip(f"chromium unavailable: {exc}", allow_module_level=True)


def _make_package(tmp_path):
    pkg = tmp_path / "20260615_demo"
    pkg.mkdir()
    manifest = {
        "post_id": "20260615_demo",
        "source": {"canonical_url": "https://example.com/news/a"},
        "content": {"title": "整合測試貼文", "body": "內文 body", "tags": [], "category": None},
        "media": {},
        "backend": {"status": "package_built", "draft_url": None,
                    "published_url": None, "remote_id": None},
        "audit": {"created_at": None, "updated_at": None, "last_error": None},
    }
    mpath = pkg / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    return str(mpath)


def _backend_file(tmp_path, mock):
    cfg = mock.backend_cfg()
    # U15(1): login_required_url_contains is now a REQUIRED verify key (a missing
    # marker is a config error, not a silent no-op). The mock admin never redirects
    # to a login page, so any sentinel that won't appear in its URLs satisfies the
    # validator without changing behaviour.
    cfg["verify"].setdefault("login_required_url_contains", "/admin/login")
    p = tmp_path / "backend.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(p)


def _args(module, **kw):
    return module._parse(_argv(**kw))


def _argv(**kw):
    out = []
    for k, v in kw.items():
        flag = "--" + k.replace("_", "-")
        if v is True:
            out.append(flag)
        elif v is not None:
            out += [flag, str(v)]
    return out


def test_full_draft_verify_publish_flow(tmp_path):
    manifest_path = _make_package(tmp_path)
    state_path = str(tmp_path / "state.sqlite")
    with MockAdmin() as mock:
        backend = _backend_file(tmp_path, mock)

        # draft
        code = cli.run(lambda: draft_post._run(_args(
            draft_post, manifest=manifest_path, backend=backend, headless=True)))
        assert code == 0
        m = json.loads(open(manifest_path, encoding="utf-8").read())
        assert m["backend"]["status"] == "drafted"
        assert "/admin/posts/" in m["backend"]["draft_url"]

        # verify
        code = cli.run(lambda: verify_draft._run(_args(
            verify_draft, manifest=manifest_path, backend=backend, headless=True)))
        assert code == 0
        assert json.loads(open(manifest_path, encoding="utf-8").read())["backend"]["status"] == "draft_verified"

        # publish (with approval + state)
        code = cli.run(lambda: publish_post._run(_args(
            publish_post, manifest=manifest_path, backend=backend,
            headless=True, approve=True, state=state_path)))
        assert code == 0
        m = json.loads(open(manifest_path, encoding="utf-8").read())
        assert m["backend"]["status"] == "published"
        assert m["backend"]["published_url"]

    # publish receipt written
    receipt = tmp_path / "20260615_demo" / "publish_receipt.json"
    assert receipt.exists()

    # R9: canonical_url now counts as processed in dedupe state
    th = url_utils.title_hash("整合測試貼文")
    with state_mod.connect(state_path) as conn:
        assert state_mod.is_processed(conn, "https://example.com/news/a", th) is True
