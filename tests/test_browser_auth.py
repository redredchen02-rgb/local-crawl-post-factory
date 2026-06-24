"""Mock-based tests for browser/auth.py (capture_login error paths).

Uses monkeypatching on auth._import_playwright to avoid needing a real
Playwright installation or browser binary. Only the error/edge branches
that are unreachable with a real browser are covered here — happy-path
takes a human in a headed browser by design (origin §15).
"""

import errno
import os
from unittest.mock import Mock, patch

import pytest

from cpost.browser import auth
from cpost.core.errors import DependencyError, ExternalError


# ---------------------------------------------------------------------------
# Mock helpers — use plain Mock (NOT MagicMock) for context-manager roles,
# because MagicMock.__enter__() returns a *different* mock each call, which
# breaks the "with sync_playwright() as pw:" wiring.
# ---------------------------------------------------------------------------


class _FakePlaywright:
    """Stand-in for ``playwright.sync_api.sync_playwright``.

    ``with _FakePlaywright() as pw:`` yields a ``pw`` whose ``.chromium``
    is the same object every time.
    """

    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeChromium:
    """Stand-in for ``pw.chromium``.

    ``launch(headless=)`` either raises *launch_error* or returns a context
    whose ``new_context() → context → new_page()`` returns *page*.
    """

    def __init__(self, page, launch_error=None, storage_error=None):
        self._page = page
        self._launch_error = launch_error
        self._storage_error = storage_error

    def launch(self, headless):
        if self._launch_error is not None:
            raise self._launch_error
        # launch() returns a *browser*; browser.new_context() returns a *context*.
        context = Mock(spec=["new_page", "storage_state", "close"])
        context.new_page.return_value = self._page
        if self._storage_error is not None:
            context.storage_state = Mock(side_effect=self._storage_error)
        else:
            context.storage_state = Mock()
        context.close = Mock()
        browser = Mock(spec=["new_context", "close"])
        browser.new_context.return_value = context
        browser.close = Mock()
        return browser


def _import_playwright_mocks(page, launch_error=None, storage_error=None):
    """Return a (sync_playwright, PlaywrightError) pair for patching.

    When *launch_error* is set, ``pw.chromium.launch(headless=)`` raises it.
    *page* is the return of ``context.new_page()`` and must expose ``.url``
    and ``.goto(url)``.
    """
    PlaywrightError = type("PlaywrightError", (Exception,), {})
    if launch_error is not None and not isinstance(launch_error, PlaywrightError):
        launch_error = PlaywrightError(str(launch_error))

    chromium = _FakeChromium(page, launch_error=launch_error, storage_error=storage_error)
    return lambda: _FakePlaywright(chromium), PlaywrightError


def test_capture_login_browser_launch_failure(tmp_path):
    """pw.chromium.launch raises PlaywrightError → DependencyError."""
    import_ret = _import_playwright_mocks(page=Mock(), launch_error="chromium not found")
    with patch.object(auth, "_import_playwright", return_value=import_ret):
        with pytest.raises(DependencyError, match="browser not installed"):
            auth.capture_login(
                "https://x.com/login",
                str(tmp_path / "state.json"),
                "/admin",
                headless=True,
                timeout_sec=1,
                poll_sec=0.1,
            )


def test_capture_login_timeout(tmp_path):
    """page.url never matches until_contains → ExternalError after deadline."""
    page = Mock()
    page.url = "/login"
    page.goto = Mock()
    import_ret = _import_playwright_mocks(page=page)
    with patch.object(auth, "_import_playwright", return_value=import_ret):
        with pytest.raises(ExternalError, match="login not detected"):
            auth.capture_login(
                "https://x.com/login",
                str(tmp_path / "state.json"),
                "/admin",
                headless=True,
                timeout_sec=1,
                poll_sec=0.1,
            )


def test_capture_login_successful_write(tmp_path):
    """Login detected → storage_state written to disk and path returned."""
    dest = tmp_path / "state.json"
    page = Mock()
    page.url = "https://x.com/admin/dashboard"
    page.goto = Mock()
    import_ret = _import_playwright_mocks(page=page)
    with patch.object(auth, "_import_playwright", return_value=import_ret):
        result = auth.capture_login(
            "https://x.com/login",
            str(dest),
            "/admin",
            headless=True,
            timeout_sec=10,
            poll_sec=0.1,
        )

    # dest.exists() proves context.storage_state → os.replace completed
    assert result == str(dest)
    assert dest.exists()


def test_capture_login_storage_state_raises(tmp_path):
    """context.storage_state raises → temp file cleaned up and error re-raised."""
    dest = tmp_path / "state.json"
    page = Mock()
    page.url = "https://x.com/admin/dashboard"
    page.goto = Mock()
    exc = RuntimeError("disk full")
    import_ret = _import_playwright_mocks(page=page, storage_error=exc)
    with patch.object(auth, "_import_playwright", return_value=import_ret):
        with pytest.raises(RuntimeError, match="disk full"):
            auth.capture_login(
                "https://x.com/login",
                str(dest),
                "/admin",
                headless=True,
                timeout_sec=10,
                poll_sec=0.1,
            )

    # temp sibling should not remain on disk after cleanup
    left_behind = list(dest.parent.glob(f"{dest.name}.*.tmp"))
    assert not left_behind, f"temp files not cleaned: {left_behind}"


def test_capture_login_storage_state_raises_oserror_on_cleanup(tmp_path):
    """os.unlink of temp file fails → error silently swallowed and original
    exception re-raised (defensive guard covering ``except OSError: pass``)."""
    dest = tmp_path / "state.json"
    page = Mock()
    page.url = "https://x.com/admin/dashboard"
    page.goto = Mock()

    unlink_called = False

    def _broken_unlink(path, *a, **kw):
        nonlocal unlink_called
        unlink_called = True
        raise OSError(errno.EACCES, "permission denied")

    exc = RuntimeError("disk full")
    import_ret = _import_playwright_mocks(page=page, storage_error=exc)
    with patch.object(auth, "_import_playwright", return_value=import_ret):
        with patch.object(os, "unlink", _broken_unlink):
            with pytest.raises(RuntimeError, match="disk full"):
                auth.capture_login(
                    "https://x.com/login",
                    str(dest),
                    "/admin",
                    headless=True,
                    timeout_sec=10,
                    poll_sec=0.1,
                )

    assert unlink_called, "os.unlink should have been attempted"
