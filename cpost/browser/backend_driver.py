"""Playwright backend driver (Phase 4-5 behavior).

All selectors come from ``backend.yaml`` via :mod:`cpost.browser.selector_recipe` —
this module never hardcodes a selector string (R7). Login is carried by a
Playwright ``storage_state`` file; this driver never handles passwords and never
bypasses access controls (origin §15).
"""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cpost.core.errors import (
    DependencyError, ExternalError, SessionExpiredError, ValidationError,
)
from cpost.browser.selector_recipe import get_selector

DEFAULT_TIMEOUT_MS = 30000
DEFAULT_RETRIES = 1
DEFAULT_BACKOFF_SEC = 0.0


def retry_kwargs(cfg, retries_override=None):
    """Build {retries, backoff_sec} from backend.yaml ``retry`` + optional override."""
    retry = cfg.get("retry") or {}
    retries = retries_override if retries_override is not None else retry.get("count", DEFAULT_RETRIES)
    return {"retries": int(retries), "backoff_sec": float(retry.get("backoff_sec", DEFAULT_BACKOFF_SEC))}


def _check_session(cfg, page):
    """Raise SessionExpiredError if the page was redirected to a login page.

    U15(1): the marker ``verify.login_required_url_contains`` is REQUIRED (enforced
    by :func:`selector_recipe.load_backend`). A missing marker is therefore a config
    error caught at load time, NOT a silent no-op here that would let an expired
    session drive the login page undetected and misreport as a generic timeout. If
    a caller hands us a cfg that skipped that validation (no marker), we surface the
    config error explicitly rather than no-op. Distinguished from a generic timeout
    because the remedy is re-login, not retry.
    """
    marker = (cfg.get("verify") or {}).get("login_required_url_contains")
    if not marker:
        raise ValidationError(
            "backend config missing verify key: login_required_url_contains"
        )
    if marker in (page.url or ""):
        raise SessionExpiredError(
            "login session expired (redirected to login) — re-run auth-login"
        )


def _capture_failure(pkg_dir, stage, page, error):
    """Save a screenshot + failure.json next to the post package (R2)."""
    if not pkg_dir:
        return
    d = Path(pkg_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    shot = d / f"failure_{stage}_{ts}.png"
    try:
        page.screenshot(path=str(shot))
    except Exception:  # noqa: BLE001 - never let capture mask the real error
        shot = None
    (d / "failure.json").write_text(json.dumps({
        "stage": stage,
        "url": getattr(page, "url", None),
        "error": str(error),
        "screenshot": str(shot) if shot else None,
        "ts": ts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_with_retry(stage, steps, page, *, retries, backoff_sec, pkg_dir):
    """Run ``steps`` with retry on transient failures (R1).

    SessionExpiredError and permanent errors (e.g. ValidationError from a
    missing selector) are not retried. Transient PlaywrightTimeout is retried;
    each failure captures a screenshot.
    """
    _, PlaywrightError, PlaywrightTimeout = _import_playwright()
    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            return steps()
        except SessionExpiredError:
            raise  # re-login needed, not a retryable transient failure
        except (PlaywrightTimeout, PlaywrightError) as exc:
            # Treat generic Playwright errors (navigation, transient selector
            # failures) as transient: capture evidence and retry. ValidationError
            # (e.g. a missing configured selector) is not a PlaywrightError and
            # propagates untouched.
            _capture_failure(pkg_dir, stage, page, exc)
            if attempt >= attempts:
                raise ExternalError(
                    f"{stage} did not confirm after {attempts} attempt(s): {exc}")
            if backoff_sec:
                time.sleep(backoff_sec * attempt)


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DependencyError(f"playwright not installed: {exc}")
    return sync_playwright, PlaywrightError, PlaywrightTimeout


@contextmanager
def session(storage_state=None, headless=True, timeout_ms=DEFAULT_TIMEOUT_MS):
    """Yield a Playwright ``page`` with optional saved login state."""
    sync_playwright, PlaywrightError, _ = _import_playwright()
    state = None
    if storage_state and Path(storage_state).exists():
        state = storage_state
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless)
        except PlaywrightError as exc:
            raise DependencyError(f"browser not installed: {exc}")
        # U15(4): new_context/new_page are inside the try/finally that closes the
        # browser. Previously they ran BEFORE the try, so a failure here (e.g.
        # new_context raising on a corrupt storage_state) left the launched browser
        # process leaked. Now any init failure still closes the browser explicitly.
        context = None
        try:
            context = browser.new_context(storage_state=state)
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
            yield page
        finally:
            if context is not None:
                context.close()
            browser.close()


def create_draft(page, cfg, manifest, manifest_path, *,
                 retries=DEFAULT_RETRIES, backoff_sec=DEFAULT_BACKOFF_SEC, pkg_dir=None):
    """Fill the create form and save a draft. Returns {'draft_url': ...}."""
    # U15(2): validate required manifest fields BEFORE driving the browser. A
    # manifest missing content/content.title previously raised a bare KeyError once
    # the browser was already open — surfacing as exit 5 (internal error) and
    # leaking an open browser. Validate up front so it is a clean ValidationError
    # (exit 2) with no browser side effects, and read fields with .get() so the only
    # gate is this explicit check.
    content = manifest.get("content")
    if not isinstance(content, dict):
        raise ValidationError("manifest missing required field: content")
    if not content.get("title"):
        raise ValidationError("manifest missing required field: content.title")
    pkg_dir = pkg_dir or (Path(manifest_path).parent if manifest_path else None)

    def steps():
        page.goto(cfg["create_url"])
        _check_session(cfg, page)
        page.fill(get_selector(cfg, "title"), content.get("title") or "")
        page.fill(get_selector(cfg, "body"), content.get("body") or "")

        if content.get("category") and "category" in cfg["selectors"]:
            page.select_option(get_selector(cfg, "category"), content["category"])
        if content.get("tags") and "tags" in cfg["selectors"]:
            page.fill(get_selector(cfg, "tags"), ",".join(content["tags"]))

        page.click(get_selector(cfg, "save_draft"))
        page.wait_for_selector(f"text={cfg['verify']['draft_success_text']}")
        return {"draft_url": page.url}

    return _run_with_retry("draft", steps, page,
                           retries=retries, backoff_sec=backoff_sec, pkg_dir=pkg_dir)


def verify_draft(page, cfg, title, *,
                 retries=DEFAULT_RETRIES, backoff_sec=DEFAULT_BACKOFF_SEC, pkg_dir=None):
    """Search the admin for the draft title. Returns True if found."""
    verify = cfg["verify"]

    def steps():
        page.goto(verify["search_url"])
        _check_session(cfg, page)
        page.fill(verify["search_input"], title)
        page.click(verify["search_button"])
        _wait_for_result_title(page, verify["result_title"], title)
        return True

    return _run_with_retry("verify", steps, page,
                           retries=retries, backoff_sec=backoff_sec, pkg_dir=pkg_dir)


def _wait_for_result_title(page, result_title, title):
    """Wait for the search-result row carrying ``title`` as text.

    U15(3): selector-injection hardening for the canonical recipe only. The shipped
    recipe is ``"<container> >> text={title}"``; naively substituting the title
    re-parses Playwright selector syntax — a title containing ``>>`` (chaining) or
    ``text=``/quotes splits the selector and yields a FALSE transient failure (the
    post is fine, the locator is broken). For that recipe we build a STRUCTURED
    locator: scope to the container before ``>> text={title}`` and match the title
    via ``get_by_text(title, exact=False)``, which treats the title as data (not
    selector syntax) and preserves the original ``text=`` SUBSTRING/case-insensitive
    semantics — so a same-node suffix (e.g. ``"<title> (草稿)"``) still matches.

    Recipes WITHOUT the ``text={title}`` convention are custom selectors (e.g.
    ``'tr:has-text("{title}")'``). For those we substitute the title into the full
    selector and wait on the complete result — splitting on ``{title}`` would yield a
    broken selector fragment (``'tr:has-text("'``) that raises a PlaywrightError and
    re-introduces the false-transient-failure class this guard exists to prevent.
    """
    placeholder = "text={title}"
    if placeholder in result_title:
        container = result_title.split(">>")[0].strip() if ">>" in result_title else None
        scope = page.locator(container) if container else page
        scope.get_by_text(title, exact=False).first.wait_for()
        return
    # Custom recipe: substitute the title into the full selector. This is a valid
    # locator for custom recipes; do NOT feed a split fragment into page.locator().
    page.wait_for_selector(result_title.replace("{title}", title))


def publish_draft(page, cfg, draft_url, *,
                  retries=DEFAULT_RETRIES, backoff_sec=DEFAULT_BACKOFF_SEC, pkg_dir=None):
    """Open the draft and publish it. Returns {'published_url': ...}."""

    def steps():
        if draft_url:
            page.goto(draft_url)
            _check_session(cfg, page)
        page.click(get_selector(cfg, "publish"))
        page.wait_for_selector(f"text={cfg['verify']['publish_success_text']}")
        return {"published_url": page.url}

    return _run_with_retry("publish", steps, page,
                           retries=retries, backoff_sec=backoff_sec, pkg_dir=pkg_dir)
