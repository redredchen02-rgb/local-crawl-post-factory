"""auth-login: storage_state capture once the success marker is reached."""

import json

import pytest

from cpost.core import cli
from cpost.browser import auth
from cpost.cli import auth_login
from tests.mock_admin import MockAdmin

# `cli.run` maps handler outcomes to exit codes (origin spec §2.3 / §13):
#   0 success, 1 usage/KeyboardInterrupt, 2 validation, 3 dependency,
#   4 external, 5 unexpected-internal (catch-all).
playwright = pytest.importorskip("playwright.sync_api")


def _chromium_available() -> bool:
    """True only if a real chromium can launch (gates the two live tests)."""
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:  # pragma: no cover - environment dependent
        return False


_BROWSER = pytest.mark.skipif(
    not _chromium_available(), reason="chromium unavailable"
)


# --- Live-browser contract tests (real Playwright + real MockAdmin) ---------
# Marked slow/browser so the fast-run excludes them; gated on chromium.


@_BROWSER
@pytest.mark.slow
@pytest.mark.browser
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


@_BROWSER
@pytest.mark.slow
@pytest.mark.browser
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


# --- Fast contract tests (collaborators mocked; no real browser) -----------
# These mock auth.capture_login / the Playwright import, so they exercise the
# CLI exit-code contract and the atomic-write guarantee without a browser.


def _args(out, *, login_url="https://admin.example/login", until="/admin"):
    """Build parsed CLI args for the auth-login command."""
    return auth_login._parse([
        "--login-url", login_url,
        "--storage-state", str(out),
        "--until-url-contains", until,
        "--headless",
        "--timeout-sec", "10",
    ])


def test_happy_login_writes_state_and_exits_zero(tmp_path, monkeypatch, capsys):
    """Successful login flow: storage_state saved, JSON on stdout, exit 0."""
    out = tmp_path / "auth" / "storage-state.json"

    def fake_capture(*, login_url, storage_state, until_contains,
                     headless, timeout_sec):
        dest = auth.Path(storage_state)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"cookies": [], "origins": []}),
                        encoding="utf-8")
        return storage_state

    monkeypatch.setattr(auth_login.auth, "capture_login", fake_capture)

    args = _args(out)
    code = cli.run(lambda: auth_login._run(args))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert out.exists()
    payload = json.loads(captured.out)
    assert payload == {"status": "logged_in", "storage_state": str(out)}
    state = json.loads(out.read_text(encoding="utf-8"))
    assert "cookies" in state and "origins" in state


def test_missing_required_arg_exits_two_with_stderr(capsys):
    """Missing a required flag: argparse usage error -> exit 2 + stderr."""
    with pytest.raises(SystemExit) as exc:
        # No --until-url-contains: argparse rejects it before any handler runs.
        auth_login._parse([
            "--login-url", "https://admin.example/login",
            "--storage-state", "/tmp/unused.json",
        ])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()
    assert "until-url-contains" in err


def test_bad_url_validation_exits_two(tmp_path, monkeypatch, capsys):
    """A bad login URL surfaces as a ValidationError -> exit 2 + stderr."""
    out = tmp_path / "storage-state.json"

    def fake_capture(**_kwargs):
        from cpost.core.errors import ValidationError
        raise ValidationError("invalid login-url: not-a-url")

    monkeypatch.setattr(auth_login.auth, "capture_login", fake_capture)

    args = _args(out, login_url="not-a-url")
    code = cli.run(lambda: auth_login._run(args))

    captured = capsys.readouterr()
    assert code == 2  # ValidationError
    assert captured.out == ""
    assert "invalid login-url" in captured.err
    assert not out.exists()


def test_unhandled_error_maps_to_exit_five(tmp_path, monkeypatch, capsys):
    """A non-CliError escaping the handler hits cli.run's catch-all -> exit 5."""
    out = tmp_path / "storage-state.json"

    def fake_capture(**_kwargs):
        raise RuntimeError("boom: unexpected collaborator failure")

    monkeypatch.setattr(auth_login.auth, "capture_login", fake_capture)

    args = _args(out)
    code = cli.run(lambda: auth_login._run(args))

    captured = capsys.readouterr()
    assert code == 5  # unexpected internal error (errors.py InternalError)
    assert captured.out == ""
    assert "internal error" in captured.err
    assert "boom" in captured.err
    assert not out.exists()


class _FakeContext:
    """Stand-in Playwright context whose storage_state crashes mid-export."""

    def __init__(self, page, on_storage_state):
        self._page = page
        self._on_storage_state = on_storage_state

    def new_page(self):
        return self._page

    def storage_state(self, path):  # noqa: ARG002 - mimics real signature
        # Real Playwright writes `path` here; we crash *before* writing a byte
        # to model an interruption mid-export.
        self._on_storage_state()

    def close(self):
        pass


class _FakePage:
    def __init__(self, url):
        self.url = url

    def goto(self, url):
        self.url = url


class _FakeBrowser:
    def __init__(self, context):
        self._context = context

    def new_context(self):
        return self._context

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, headless=False):  # noqa: ARG002
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_interrupted_export_leaves_no_partial_state(tmp_path, monkeypatch):
    """Edge: interrupted mid-export -> dest is either whole or absent.

    Drives the real ``auth.capture_login`` atomic-write path with a fake
    Playwright whose ``storage_state`` raises before writing. The tempfile must
    be cleaned up and the destination must never appear truncated/half-written.
    """
    out = tmp_path / "auth" / "storage-state.json"
    marker = "/admin"

    def crash():
        raise KeyboardInterrupt("user aborted mid-login")

    page = _FakePage(url=f"https://admin.example{marker}")
    context = _FakeContext(page, on_storage_state=crash)
    browser = _FakeBrowser(context)

    def fake_import_playwright():
        return (lambda: _FakePlaywright(browser), Exception)

    monkeypatch.setattr(auth, "_import_playwright", fake_import_playwright)

    with pytest.raises(KeyboardInterrupt):
        auth.capture_login(
            login_url=f"https://admin.example{marker}",
            storage_state=str(out),
            until_contains=marker,
            headless=True,
            timeout_sec=10,
        )

    # Atomic guarantee: full file or none — never a truncated dest.
    assert not out.exists()
    # And the temp sibling used for the staged write must be cleaned up.
    leftovers = list(out.parent.glob(out.name + ".*"))
    assert leftovers == []
