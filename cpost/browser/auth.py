"""Interactive-login → storage_state capture (no stdin prompts).

Opens a headed browser at the admin login page so a human can authenticate
manually (never automated, never password-handling). Once the page URL contains
``until_contains`` (a sign of a successful login, e.g. ``/admin``), the session
cookies are exported to ``storage_state`` for the non-interactive pipeline
commands to reuse. The system never bypasses login or CAPTCHA (origin §15).
"""

import os
import tempfile
import time
from pathlib import Path

from cpost.core.errors import DependencyError, ExternalError


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DependencyError(f"playwright not installed: {exc}")
    return sync_playwright, PlaywrightError


def capture_login(login_url, storage_state, until_contains,
                  headless=False, timeout_sec=300, poll_sec=1.0):
    """Drive a manual login and write storage_state once logged in.

    Returns the path written. Raises ExternalError on timeout.
    """
    sync_playwright, PlaywrightError = _import_playwright()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless)
        except PlaywrightError as exc:
            raise DependencyError(f"browser not installed: {exc}")
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(login_url)
            deadline = timeout_sec / poll_sec
            waited = 0
            while until_contains not in page.url:
                if waited >= deadline:
                    raise ExternalError(
                        f"login not detected (url never contained {until_contains!r})"
                    )
                time.sleep(poll_sec)
                waited += 1
            dest = Path(storage_state)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp sibling (same dir/filesystem) then os.replace, so a
            # crash mid-export can't leave a truncated storage_state behind.
            fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent),
                                            prefix=dest.name + ".", suffix=".tmp")
            os.close(fd)
            try:
                context.storage_state(path=tmp_name)
                os.replace(tmp_name, dest)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        finally:
            context.close()
            browser.close()
    return storage_state
