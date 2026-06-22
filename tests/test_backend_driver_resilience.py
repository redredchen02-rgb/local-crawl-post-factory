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


# --- U15(3) regression: _wait_for_result_title title-matching defects ----------

def _valid_selector(sel):
    """Crude Playwright/CSS selector sanity check used by the fakes below.

    Real Playwright raises a PlaywrightError when fed a syntactically broken
    selector (e.g. an unbalanced quote). The fakes reproduce that so a test can
    detect the broken-locator path WITHOUT a real browser. ``text={title}`` data
    fragments are never passed to a selector parser in the fixed code, so they need
    not be valid selectors here.
    """
    return sel.count('"') % 2 == 0 and not sel.rstrip().endswith(">>")


class _FakeTextLocator:
    def __init__(self, leaf_texts, query, exact):
        self._leaf_texts = leaf_texts
        self._query = query
        self._exact = exact

    @property
    def first(self):
        return self

    def wait_for(self):
        if self._exact:
            matched = any(t == self._query for t in self._leaf_texts)
        else:  # substring / case-insensitive, like Playwright's text= engine
            matched = any(self._query.lower() in t.lower() for t in self._leaf_texts)
        if not matched:
            raise PlaywrightTimeout(
                f"locator.wait_for: no element matched get_by_text({self._query!r})")


class _FakeScope:
    """A located container; get_by_text searches its leaf node texts."""

    def __init__(self, leaf_texts):
        self._leaf_texts = leaf_texts
        self.text_calls = []

    def get_by_text(self, query, exact=False):
        self.text_calls.append((query, exact))
        return _FakeTextLocator(self._leaf_texts, query, exact)


class _FakeResultPage(_FakeScope):
    """Fake Playwright page recording how _wait_for_result_title builds locators.

    ``leaf_texts`` are the same-node text contents present in the (mock) result
    table. ``locator()`` and ``wait_for_selector()`` validate the selector string,
    raising PlaywrightError on a broken fragment — exactly what the buggy split path
    produced.
    """

    def __init__(self, leaf_texts):
        super().__init__(leaf_texts)
        self.locator_calls = []
        self.wait_for_selector_calls = []
        self.scopes = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        if not _valid_selector(selector):
            raise PlaywrightError(f"Unexpected token in selector: {selector!r}")
        scope = _FakeScope(self._leaf_texts)
        self.scopes.append(scope)
        return scope

    def wait_for_selector(self, selector):
        self.wait_for_selector_calls.append(selector)
        if not _valid_selector(selector):
            raise PlaywrightError(f"Unexpected token in selector: {selector!r}")
        # present post -> matches; mirror substring text semantics loosely
        return object()


def test_custom_recipe_does_not_build_broken_locator():
    """Defect A: a non-'text={title}' recipe must NOT feed a split fragment to
    page.locator(); the present post must verify without a broken-locator error."""
    page = _FakeResultPage(['整合測試貼文'])
    # Buggy code did page.locator('tr:has-text("') -> PlaywrightError -> false transient.
    backend_driver._wait_for_result_title(page, 'tr:has-text("{title}")', "整合測試貼文")
    # Fixed path substitutes into the full selector and waits on it.
    assert page.wait_for_selector_calls == ['tr:has-text("整合測試貼文")']
    assert page.locator_calls == []  # no fragment fed to locator()


def test_custom_recipe_with_chain_fragment_not_broken():
    """Defect A variant: 'td.title >> {title}' must not yield 'td.title >>' fed to
    locator() (trailing '>>' is an invalid selector)."""
    page = _FakeResultPage(['整合測試貼文'])
    backend_driver._wait_for_result_title(page, "td.title >> {title}", "整合測試貼文")
    assert page.wait_for_selector_calls == ["td.title >> 整合測試貼文"]
    assert page.locator_calls == []


def test_canonical_recipe_matches_title_with_same_node_suffix():
    """Defect B: a result cell rendered as title+suffix in the SAME node (e.g.
    '整合測試貼文 (草稿)') must still match a present post — exact=True missed it."""
    page = _FakeResultPage(['整合測試貼文 (草稿)'])
    # Should NOT raise: get_by_text must use exact=False (substring) like text=.
    backend_driver._wait_for_result_title(page, "table >> text={title}", "整合測試貼文")
    assert page.locator_calls == ["table"]  # scoped to the container as data
    # the structured text match was built with exact=False (substring), so the
    # same-node suffix did not cause a miss
    assert page.scopes[0].text_calls == [("整合測試貼文", False)]
    # the title was never substituted into a selector string
    assert page.wait_for_selector_calls == []


def test_canonical_recipe_injection_safe_title_with_chevrons():
    """Canonical recipe keeps >>-in-title injection safety: the title is matched as
    data via get_by_text(exact=False), never re-parsed as a chained selector."""
    page = _FakeResultPage(['a >> b'])
    backend_driver._wait_for_result_title(page, "table >> text={title}", "a >> b")
    assert page.locator_calls == ["table"]
    assert page.wait_for_selector_calls == []
