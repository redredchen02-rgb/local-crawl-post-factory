"""U1: retry, failure capture, and session-expiry detection in backend_driver."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser  # Playwright; excluded from fast-run

from core.errors import SessionExpiredError, ExternalError, ValidationError  # noqa: E402
from browser import backend_driver  # noqa: E402

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
