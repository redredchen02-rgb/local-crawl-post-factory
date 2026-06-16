"""auth-login: storage_state capture once the success marker is reached."""

import json

import pytest

pytestmark = pytest.mark.slow  # Playwright subprocess; excluded from fast-run

from core import cli
from tests.mock_admin import MockAdmin

playwright = pytest.importorskip("playwright.sync_api")
from src import auth_login  # noqa: E402

try:
    with playwright.sync_playwright() as _pw:
        _b = _pw.chromium.launch(headless=True)
        _b.close()
except Exception as exc:  # pragma: no cover
    pytest.skip(f"chromium unavailable: {exc}", allow_module_level=True)


def test_capture_writes_storage_state(tmp_path):
    out = tmp_path / "auth" / "storage-state.json"
    with MockAdmin() as mock:
        # The create page is reachable immediately, standing in for a
        # post-login URL: the marker is present on first load, so the helper
        # exports state without needing a human.
        args = auth_login._parse([
            "--login-url", f"{mock.base}/admin/posts/create",
            "--storage-state", str(out),
            "--until-url-contains", "/admin/posts/create",
            "--headless",
            "--timeout-sec", "10",
        ])
        code = cli.run(lambda: auth_login._run(args))
    assert code == 0
    assert out.exists()
    state = json.loads(out.read_text(encoding="utf-8"))
    assert "cookies" in state and "origins" in state


def test_timeout_when_marker_never_seen(tmp_path):
    out = tmp_path / "storage-state.json"
    with MockAdmin() as mock:
        args = auth_login._parse([
            "--login-url", f"{mock.base}/admin/posts/create",
            "--storage-state", str(out),
            "--until-url-contains", "/never-here",
            "--headless",
            "--timeout-sec", "1",
        ])
        code = cli.run(lambda: auth_login._run(args))
    assert code == 4  # ExternalError
    assert not out.exists()
