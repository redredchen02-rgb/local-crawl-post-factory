"""Playwright backend driver (Phase 4-5 behavior).

All selectors come from ``backend.yaml`` via :mod:`browser.selector_recipe` —
this module never hardcodes a selector string (R7). Login is carried by a
Playwright ``storage_state`` file; this driver never handles passwords and never
bypasses access controls (origin §15).
"""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.errors import DependencyError, ExternalError, SessionExpiredError
from browser.selector_recipe import get_selector

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

    The marker is configured in backend.yaml as
    ``verify.login_required_url_contains`` (the real signal must be verified
    against the actual admin). Distinguished from a generic timeout because the
    remedy is re-login, not retry.
    """
    marker = (cfg.get("verify") or {}).get("login_required_url_contains")
    if marker and marker in (page.url or ""):
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
        context = browser.new_context(storage_state=state)
        context.set_default_timeout(timeout_ms)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def create_draft(page, cfg, manifest, manifest_path, *,
                 retries=DEFAULT_RETRIES, backoff_sec=DEFAULT_BACKOFF_SEC, pkg_dir=None):
    """Fill the create form and save a draft. Returns {'draft_url': ...}."""
    content = manifest["content"]
    pkg_dir = pkg_dir or (Path(manifest_path).parent if manifest_path else None)

    def steps():
        page.goto(cfg["create_url"])
        _check_session(cfg, page)
        page.fill(get_selector(cfg, "title"), content["title"] or "")
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
        selector = verify["result_title"].replace("{title}", title)
        page.wait_for_selector(selector)
        return True

    return _run_with_retry("verify", steps, page,
                           retries=retries, backoff_sec=backoff_sec, pkg_dir=pkg_dir)


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
