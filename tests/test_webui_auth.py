"""U9 (R10): session login-state light + re-login guidance.

The /auth-status view shows a green/red/grey light and, when the state is not
healthy, concrete `auth-login` guidance. Expiry detection is metadata-only and
must never read or log the credential file contents.
"""

from fastapi.testclient import TestClient

from webui.app import create_app
from core import webui_config


def _client(tmp_path, with_ss=True):
    ss = tmp_path / "ss.json"
    if with_ss:
        ss.write_text('{"cookies": ["SECRET-TOKEN-do-not-leak"]}', encoding="utf-8")
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com", "out_dir": str(tmp_path / "out"),
        "state_path": str(tmp_path / "state.sqlite"), "storage_state": str(ss)})
    return TestClient(create_app(str(cfgp))), ss


def test_valid_session_green_no_guidance(tmp_path):
    client, _ = _client(tmp_path, with_ss=True)
    text = client.get("/auth-status").text
    assert "auth-ok" in text
    assert "auth-login" not in text  # no guidance block when healthy


def test_missing_session_shows_guidance(tmp_path):
    client, _ = _client(tmp_path, with_ss=False)
    text = client.get("/auth-status").text
    assert "auth-none" in text
    assert "尚未建立登入態" in text
    assert "auth-login --login-url" in text  # concrete command shown


def test_expired_session_shows_relogin_guidance(tmp_path):
    client, ss = _client(tmp_path, with_ss=True)
    # Simulate a backend action having flagged expiry at the file's current mtime.
    app = client.app
    app.state.session_expired_mtime = ss.stat().st_mtime
    text = client.get("/auth-status").text
    assert "auth-expired" in text
    assert "已過期" in text
    assert "auth-login --login-url" in text


def test_guidance_never_leaks_credential_contents(tmp_path):
    """Expiry/status detection reads metadata only — the token must not appear."""
    client, ss = _client(tmp_path, with_ss=True)
    app = client.app
    app.state.session_expired_mtime = ss.stat().st_mtime
    text = client.get("/auth-status").text
    assert "SECRET-TOKEN-do-not-leak" not in text
