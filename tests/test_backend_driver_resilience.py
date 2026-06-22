"""U1: retry, failure capture, and session-expiry detection in backend_driver."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser  # Playwright; excluded from fast-run

from cpost.core.errors import SessionExpiredError, ExternalError, ValidationError  # noqa: E402
from cpost.browser import backend_driver  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")
PlaywrightTimeout = playwright.TimeoutError
PlaywrightError = playwright.Error


class FakePage:
    def __init__(self, url="https://example.com/admin/posts/create"):
        self.url = url
        self.shots = []

    def screenshot(self, path):
        Path(path).write_bytes(b"\x89PNG\r\n")  # minimal placeholder
        self.shots.append(path)


def test_success_no_retry_no_screenshot(tmp_path):
    page = FakePage()
    result = backend_driver._run_with_retry(
        "draft", lambda: {"ok": True}, page, retries=2, backoff_sec=0, pkg_dir=str(tmp_path))
    assert result == {"ok": True}
    assert page.shots == []
    assert not (tmp_path / "failure.json").exists()


def test_transient_then_success(tmp_path):
    page = FakePage()
    calls = {"n": 0}

    def steps():
        calls["n"] += 1
        if calls["n"] == 1:
            raise PlaywrightTimeout("transient")
        return {"draft_url": page.url}

    result = backend_driver._run_with_retry(
        "draft", steps, page, retries=2, backoff_sec=0, pkg_dir=str(tmp_path))
    assert result["draft_url"]
    assert len(page.shots) == 1  # one failure captured before the retry succeeded


def test_retries_exhausted_raises_external(tmp_path):
    page = FakePage()

    def steps():
        raise PlaywrightTimeout("always down")

    with pytest.raises(ExternalError):
        backend_driver._run_with_retry(
            "verify", steps, page, retries=2, backoff_sec=0, pkg_dir=str(tmp_path))
    assert len(page.shots) == 2  # one per attempt


def test_general_playwright_error_retried_with_capture(tmp_path):
    """U3 (R3): a non-timeout PlaywrightError is transient -> capture + retry."""
    page = FakePage()
    calls = {"n": 0}

    def steps():
        calls["n"] += 1
        if calls["n"] == 1:
            raise PlaywrightError("navigation failed")
        return {"draft_url": page.url}

    result = backend_driver._run_with_retry(
        "draft", steps, page, retries=2, backoff_sec=0, pkg_dir=str(tmp_path))
    assert result["draft_url"]
    assert len(page.shots) == 1


def test_general_playwright_error_exhausted_raises_external(tmp_path):
    """U3 (R3): unrecovered PlaywrightError raises ExternalError, no bare propagate."""
    page = FakePage()

    def steps():
        raise PlaywrightError("dom detached")

    with pytest.raises(ExternalError):
        backend_driver._run_with_retry(
            "verify", steps, page, retries=2, backoff_sec=0, pkg_dir=str(tmp_path))
    assert len(page.shots) == 2  # evidence captured each attempt


def test_permanent_error_not_retried(tmp_path):
    page = FakePage()
    calls = {"n": 0}

    def steps():
        calls["n"] += 1
        raise ValidationError("missing selector")

    with pytest.raises(ValidationError):
        backend_driver._run_with_retry(
            "draft", steps, page, retries=3, backoff_sec=0, pkg_dir=str(tmp_path))
    assert calls["n"] == 1
    assert page.shots == []


def test_session_marker_raises_and_not_retried(tmp_path):
    cfg = {"verify": {"login_required_url_contains": "/admin/login"}}
    page = FakePage(url="https://example.com/admin/login?next=/admin/posts/create")

    def steps():
        backend_driver._check_session(cfg, page)
        return {"never": "reached"}

    with pytest.raises(SessionExpiredError):
        backend_driver._run_with_retry(
            "draft", steps, page, retries=3, backoff_sec=0, pkg_dir=str(tmp_path))
    assert page.shots == []  # session expiry is not a "capture+retry" failure


def test_check_session_no_marker_ok():
    cfg = {"verify": {"login_required_url_contains": "/admin/login"}}
    page = FakePage(url="https://example.com/admin/posts/create")
    backend_driver._check_session(cfg, page)  # no raise


def test_failure_json_written(tmp_path):
    page = FakePage()

    def steps():
        raise PlaywrightTimeout("down")

    with pytest.raises(ExternalError):
        backend_driver._run_with_retry(
            "publish", steps, page, retries=1, backoff_sec=0, pkg_dir=str(tmp_path))
    data = json.loads((tmp_path / "failure.json").read_text(encoding="utf-8"))
    assert data["stage"] == "publish"
    assert "down" in data["error"]


# --- U15(1): missing login marker is a config error, not a silent no-op ---

def test_check_session_missing_marker_is_config_error():
    """An expired session must not silently drive the login page: a cfg without
    login_required_url_contains is a ValidationError (config error), not a no-op."""
    page = FakePage(url="https://example.com/admin/login")
    with pytest.raises(ValidationError):
        backend_driver._check_session({"verify": {}}, page)


def test_expired_session_with_marker_raises_session_expired():
    """error: expired session + marker present -> SessionExpiredError (re-login),
    not a Playwright timeout misclassified as a transient failure."""
    cfg = {"verify": {"login_required_url_contains": "/admin/login"}}
    page = FakePage(url="https://example.com/admin/login?next=/x")
    with pytest.raises(SessionExpiredError):
        backend_driver._check_session(cfg, page)


# --- U15(2): missing content.title -> clean ValidationError (exit 2), no browser ---

class _NeverGotoPage(FakePage):
    def goto(self, *_a, **_kw):  # pragma: no cover - must never be reached
        raise AssertionError("browser was driven before manifest validation")


def test_create_draft_missing_title_is_validation_error_no_browser():
    cfg = {"create_url": "https://x/create", "selectors": {}, "verify": {}}
    page = _NeverGotoPage()
    manifest = {"content": {"body": "b"}}  # no title
    with pytest.raises(ValidationError):
        backend_driver.create_draft(page, cfg, manifest, None, pkg_dir=None)


def test_create_draft_missing_content_is_validation_error():
    cfg = {"create_url": "https://x/create", "selectors": {}, "verify": {}}
    page = _NeverGotoPage()
    with pytest.raises(ValidationError):
        backend_driver.create_draft(page, cfg, {}, None, pkg_dir=None)


# --- U15(3): a title containing ">>" must not re-parse the verify selector ---

class _FakeLocator:
    def __init__(self, recorder, kind, value, parent=None):
        self.recorder = recorder
        self.kind = kind
        self.value = value
        self.parent = parent

    def get_by_text(self, text, exact=False):
        self.recorder["get_by_text"] = (text, exact)
        return _FakeLocator(self.recorder, "text", text, parent=self)

    @property
    def first(self):
        return self

    def wait_for(self):
        self.recorder["waited"] = True


class _VerifyPage:
    """Records structured-locator calls; raises if the raw title leaks into a
    selector string (the injection bug)."""
    def __init__(self, recorder):
        self.recorder = recorder
        self.url = "https://example.com/admin/posts"

    def locator(self, selector):
        if ">>" in selector and "{title}" not in selector:
            # container prefix only; the title must NOT appear here
            if self.recorder.get("title") and self.recorder["title"] in selector:
                raise AssertionError("title leaked into selector string")
        self.recorder.setdefault("locator", selector)
        return _FakeLocator(self.recorder, "css", selector)

    def get_by_text(self, text, exact=False):
        self.recorder["get_by_text"] = (text, exact)
        return _FakeLocator(self.recorder, "text", text)


def test_verify_title_with_chaining_chars_uses_structured_locator():
    title = "Breaking >> News text=injected"
    rec = {"title": title}
    page = _VerifyPage(rec)
    backend_driver._wait_for_result_title(page, "table >> text={title}", title)
    # title passed to get_by_text as DATA (exact match), never spliced into a selector
    assert rec["get_by_text"] == (title, True)
    assert rec.get("locator") == "table"
    assert rec.get("waited") is True


# --- U15(4): init failure closes the browser (no leaked process) ---

class _FakeBrowser:
    def __init__(self, fail_context):
        self.fail_context = fail_context
        self.closed = False

    def new_context(self, storage_state=None):
        if self.fail_context:
            raise PlaywrightError("corrupt storage_state")
        return _FakeContext()

    def close(self):
        self.closed = True


class _FakeContext:
    def set_default_timeout(self, *_a):
        pass

    def new_page(self):
        return FakePage()

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, headless=True):
        return self._browser


class _FakePW:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_session_init_failure_closes_browser(monkeypatch):
    """resource: new_context raising (corrupt storage_state) closes the browser."""
    browser = _FakeBrowser(fail_context=True)
    monkeypatch.setattr(
        backend_driver, "_import_playwright",
        lambda: (lambda: _FakePW(browser), PlaywrightError, PlaywrightTimeout))
    with pytest.raises(PlaywrightError):
        with backend_driver.session(storage_state=None, headless=True):
            pass  # pragma: no cover
    assert browser.closed is True  # browser explicitly closed despite init failure
