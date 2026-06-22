"""publish-post — publish a verified draft, only with explicit approval (R8).

Both gates are enforced BEFORE any browser action:
  1. --approve must be present.
  2. manifest backend.status must be 'draft_verified'.
On success: clicks publish, verifies success text, writes a publish receipt,
marks the canonical_url 'published' in SQLite state (so dedupe skips it, R9).
"""

import argparse
import json
import sys
from pathlib import Path

from cpost.core import cli, manifest as mf, audit, state as state_mod, runs, reviewed
from cpost.core.errors import ValidationError
from cpost.core.url_utils import title_hash
from cpost.browser.selector_recipe import load_backend
from cpost.browser import backend_driver

LOG_PATH = "./logs/audit.jsonl"


def _parse(argv):
    p = argparse.ArgumentParser(prog="publish-post")
    p.add_argument("--manifest", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--storage-state")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int, default=backend_driver.DEFAULT_TIMEOUT_MS)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--state", help="SQLite state path to mark published (R9)")
    p.add_argument("--retries", type=int, default=None, help="override backend.yaml retry.count")
    return p.parse_args(argv)


def _run(args) -> int:
    manifest = mf.load(args.manifest)
    cfg = load_backend(args.backend)

    # Gate 1: explicit approval (R8) — applies on first publish AND on re-entry.
    if not args.approve:
        raise ValidationError("refusing to publish without --approve")

    post_id = manifest.get("post_id")
    assert isinstance(post_id, str)

    if manifest.get("backend", {}).get("status") == "published":
        # Re-entry after a successful publish whose post-publish bookkeeping tail
        # failed (e.g. a transient SQLite lock or disk error between the browser
        # publish and _mark_published). The post is ALREADY live, so we must never
        # re-click publish: just forward-complete the missing, idempotent
        # bookkeeping and report success (R2). Without this, the per-stage _retry
        # re-runs this runner, Gate 2 below raises 'draft not verified' on the now-
        # 'published' manifest, and a live post is falsely reported as failed.
        # (U4 extends this to the state-row-only mixed-state window.)
        published_url = manifest.get("backend", {}).get("published_url")
    else:
        # Gate 2: draft must be verified (R8).
        if manifest.get("backend", {}).get("status") != "draft_verified":
            raise ValidationError("refusing: draft not verified")
        mf.require_status(manifest, "draft_verified")
        # Q9 opt-in re-verify: when the WebUI passes the reviewed content-id, refuse
        # to publish content that changed since review (defense-in-depth at execution
        # time). CLI publish passes no expected_content_id and is unaffected.
        expected_cid = getattr(args, "expected_content_id", None)
        if expected_cid is not None and reviewed.content_id(manifest) != expected_cid:
            raise ValidationError("refusing: content changed since review")

        draft_url = manifest.get("backend", {}).get("draft_url")
        with backend_driver.session(args.storage_state, args.headless, args.timeout_ms) as page:
            result = backend_driver.publish_draft(
                page, cfg, draft_url, pkg_dir=str(Path(args.manifest).parent),
                **backend_driver.retry_kwargs(cfg, args.retries))
        published_url = result["published_url"]
        mf.set_backend(manifest, status="published", published_url=published_url)
        mf.save(args.manifest, manifest)

    run_id = manifest.get("backend", {}).get("run_id")  # Q7: None for CLI-built manifests
    # Forward-complete only the bookkeeping that may be missing; every step here is
    # safe to repeat on a re-entry: the receipt is write-once, _mark_published is an
    # idempotent upsert, and the run record is guarded so a re-entry never appends a
    # second 'ok' row (runs.record_run is a bare INSERT — see _publish_run_recorded).
    _write_receipt(args.manifest, post_id, published_url)
    _mark_published(args.state, manifest, post_id, published_url)
    if args.state and not _publish_run_recorded(args.state, post_id):
        runs.record_run(args.state, stage="publish", post_id=post_id,
                        status="ok", detail=published_url,
                        run_id=run_id, severity="info")  # Q7: lifecycle correlation
    audit.record(LOG_PATH, post_id, "publish-post", "ok", mf.now_iso(),
                 published_url=published_url)
    json.dump({"status": "published", "post_id": post_id, "published_url": published_url},
              sys.stdout)
    sys.stdout.write("\n")
    return 0


def _publish_run_recorded(state_path, post_id) -> bool:
    """True if a successful publish run is already recorded for ``post_id``.

    ``runs.record_run`` is a bare INSERT with no dedup, so on a publish re-entry we
    must not append a second 'ok' row — that would resurrect the double-count U9
    removes from the auto-pipeline. Order-independent, so U4's reordering is safe.
    """
    if not state_path:
        return False
    return any(
        r.get("stage") == "publish" and r.get("status") == "ok"
        for r in runs.list_runs(state_path, post_id=post_id)
    )


def _write_receipt(manifest_path, post_id, published_url):
    path = Path(manifest_path).parent / "publish_receipt.json"
    if path.exists():
        return  # write-once: keep the original published_at (the true publish time)
    receipt = {"post_id": post_id, "published_url": published_url, "published_at": mf.now_iso()}
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_published(state_path, manifest, post_id, published_url):
    if not state_path:
        return
    source = manifest.get("source", {})
    canonical_url = source.get("canonical_url")
    title = manifest.get("content", {}).get("title") or ""
    if not canonical_url:
        return
    with state_mod.connect(state_path) as conn:
        state_mod.upsert(conn, canonical_url=canonical_url, title=title,
                         title_hash=title_hash(title), status="published",
                         now=mf.now_iso(), post_id=post_id, published_url=published_url)


# Public API (used by cpost.core.pipeline and webui).
run = _run


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
