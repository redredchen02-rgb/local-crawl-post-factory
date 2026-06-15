"""Playwright backend driver (Phase 4-5 behavior).

All selectors come from ``backend.yaml`` via :mod:`browser.selector_recipe` —
this module never hardcodes a selector string (R7). Login is carried by a
Playwright ``storage_state`` file; this driver never handles passwords and never
bypasses access controls (origin §15).
"""

from contextlib import contextmanager
from pathlib import Path

from core.errors import DependencyError, ExternalError
from browser.selector_recipe import get_selector

DEFAULT_TIMEOUT_MS = 30000


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


def _resolve_media(manifest_path, rel_path):
    if not rel_path:
        return None
    p = Path(rel_path)
    if not p.is_absolute():
        p = Path(manifest_path).parent / rel_path
    return str(p) if p.exists() else None


def create_draft(page, cfg, manifest, manifest_path):
    """Fill the create form and save a draft. Returns {'draft_url': ...}."""
    _, _, PlaywrightTimeout = _import_playwright()
    content = manifest["content"]
    try:
        page.goto(cfg["create_url"])
        page.fill(get_selector(cfg, "title"), content["title"] or "")
        page.fill(get_selector(cfg, "body"), content.get("body") or "")

        cover = _resolve_media(manifest_path, manifest["media"].get("watermarked_cover_path")
                               or manifest["media"].get("cover_path"))
        if cover:
            page.set_input_files(get_selector(cfg, "cover"), cover)

        if content.get("category") and "category" in cfg["selectors"]:
            page.select_option(get_selector(cfg, "category"), content["category"])
        if content.get("tags") and "tags" in cfg["selectors"]:
            page.fill(get_selector(cfg, "tags"), ",".join(content["tags"]))

        page.click(get_selector(cfg, "save_draft"))
        page.wait_for_selector(f"text={cfg['verify']['draft_success_text']}")
    except PlaywrightTimeout as exc:
        raise ExternalError(f"draft save did not confirm: {exc}")
    return {"draft_url": page.url}


def verify_draft(page, cfg, title):
    """Search the admin for the draft title. Returns True if found."""
    _, _, PlaywrightTimeout = _import_playwright()
    verify = cfg["verify"]
    try:
        page.goto(verify["search_url"])
        page.fill(verify["search_input"], title)
        page.click(verify["search_button"])
        selector = verify["result_title"].replace("{title}", title)
        page.wait_for_selector(selector)
    except PlaywrightTimeout as exc:
        raise ExternalError(f"draft not found in backend: {exc}")
    return True


def publish_draft(page, cfg, draft_url):
    """Open the draft and publish it. Returns {'published_url': ...}."""
    _, _, PlaywrightTimeout = _import_playwright()
    try:
        if draft_url:
            page.goto(draft_url)
        page.click(get_selector(cfg, "publish"))
        page.wait_for_selector(f"text={cfg['verify']['publish_success_text']}")
    except PlaywrightTimeout as exc:
        raise ExternalError(f"publish did not confirm: {exc}")
    return {"published_url": page.url}
