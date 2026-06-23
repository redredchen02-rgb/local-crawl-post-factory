"""draft-post — open the private admin, fill the create form, save a draft.

Selectors come from backend.yaml (R7); login is carried by a Playwright
storage_state file (never passwords). With --dry-run it only validates the
manifest + backend config and exits 0 without touching a browser.
"""

import argparse
import json
import sys

from cpost.core import cli, manifest as mf, audit
from cpost.core.backend_args import BackendInvocation
from cpost.browser.selector_recipe import load_backend
from cpost.browser import backend_driver

# Backward-compatible re-exports (used by publish_post and tests).
load_manifest = mf.load
require_status = mf.require_status
ALLOWED_STATES = mf.ALLOWED_STATES

LOG_PATH = "./logs/audit.jsonl"


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="draft-post")
    p.add_argument("--manifest", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--storage-state")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int, default=backend_driver.DEFAULT_TIMEOUT_MS)
    p.add_argument("--retries", type=int, default=None, help="override backend.yaml retry.count")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _run(args: argparse.Namespace | BackendInvocation) -> int:
    manifest = mf.load(args.manifest)
    cfg = load_backend(args.backend)
    mf.require_status(manifest, "package_built")
    post_id = manifest.get("post_id")
    assert isinstance(post_id, str)

    if args.dry_run:
        # origin §17.8: dry-run validates without submitting.
        json.dump({"status": "validated", "post_id": post_id}, sys.stdout)
        sys.stdout.write("\n")
        return 0

    with backend_driver.session(args.storage_state, args.headless, args.timeout_ms) as page:
        result = backend_driver.create_draft(
            page, cfg, manifest, args.manifest,
            **backend_driver.retry_kwargs(cfg, args.retries))

    mf.set_backend(manifest, status="drafted", draft_url=result["draft_url"])
    mf.save(args.manifest, manifest)
    audit.record(LOG_PATH, post_id, "draft-post", "ok", mf.now_iso(),
                 draft_url=result["draft_url"])
    json.dump({"status": "drafted", "post_id": post_id, "draft_url": result["draft_url"]},
              sys.stdout)
    sys.stdout.write("\n")
    return 0


# Public API (used by cpost.core.pipeline and webui).
run = _run


def main(argv: list[str] | None = None) -> None:
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
