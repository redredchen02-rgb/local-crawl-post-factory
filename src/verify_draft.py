"""verify-draft — search the admin backend to confirm the draft exists."""

import argparse
import json
import sys
from pathlib import Path

from core import cli, manifest as mf, audit
from browser.selector_recipe import load_backend
from browser import backend_driver

LOG_PATH = "./logs/audit.jsonl"


def _parse(argv):
    p = argparse.ArgumentParser(prog="verify-draft")
    p.add_argument("--manifest", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--storage-state")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int, default=backend_driver.DEFAULT_TIMEOUT_MS)
    p.add_argument("--retries", type=int, default=None, help="override backend.yaml retry.count")
    return p.parse_args(argv)


def _run(args) -> int:
    manifest = mf.load(args.manifest)
    cfg = load_backend(args.backend)
    mf.require_status(manifest, "drafted")
    post_id = manifest.get("post_id")
    title = manifest.get("content", {}).get("title") or ""

    with backend_driver.session(args.storage_state, args.headless, args.timeout_ms) as page:
        backend_driver.verify_draft(
            page, cfg, title, pkg_dir=str(Path(args.manifest).parent),
            **backend_driver.retry_kwargs(cfg, args.retries))

    mf.set_backend(manifest, status="draft_verified")
    mf.save(args.manifest, manifest)
    audit.record(LOG_PATH, post_id, "verify-draft", "ok", mf.now_iso())
    json.dump({"status": "draft_verified", "post_id": post_id}, sys.stdout)
    sys.stdout.write("\n")
    return 0


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
