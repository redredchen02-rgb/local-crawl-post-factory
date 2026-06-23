"""auth-login — capture a Playwright storage_state via manual login.

Opens a headed browser at the login URL; once the URL contains the success
marker (you logged in), saves the session to --storage-state. Non-interactive
in the stdin sense, so it stays scriptable, but requires a human to log in.
"""

import argparse
import json
import sys

from cpost.core import cli
from cpost.browser import auth


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="auth-login")
    p.add_argument("--login-url", required=True)
    p.add_argument("--storage-state", required=True)
    p.add_argument("--until-url-contains", required=True,
                   help="substring that appears in the URL after a successful login")
    p.add_argument("--headless", action="store_true",
                   help="rarely useful; manual login normally needs a visible browser")
    p.add_argument("--timeout-sec", type=int, default=300)
    return p.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    path = auth.capture_login(
        login_url=args.login_url,
        storage_state=args.storage_state,
        until_contains=args.until_url_contains,
        headless=args.headless,
        timeout_sec=args.timeout_sec,
    )
    json.dump({"status": "logged_in", "storage_state": path}, sys.stdout)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
