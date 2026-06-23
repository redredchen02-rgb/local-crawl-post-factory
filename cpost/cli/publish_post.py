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
from cpost.core.backend_args import BackendInvocation
from cpost.core.errors import ExternalError, ValidationError
from cpost.core.url_utils import title_hash
from cpost.browser.selector_recipe import load_backend
from cpost.browser import backend_driver

LOG_PATH = "./logs/audit.jsonl"


def _parse(argv: list[str]) -> argparse.Namespace:
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


def _run(args: argparse.Namespace | BackendInvocation) -> int:
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
        # publish and the state/receipt/run writes). The post is ALREADY live, so we
        # must never re-click publish: just forward-complete the missing, idempotent
        # bookkeeping below and report success (R2). Without this, the per-stage
        # _retry re-runs this runner, Gate 2 below raises 'draft not verified' on the
        # now-'published' manifest, and a live post is falsely reported as failed.
        published_url = manifest.get("backend", {}).get("published_url")
    elif (state_url := _state_published_url(args.state, manifest)) is not None:
        # U4 mixed-state re-entry: the durable state row already says 'published' for
        # this canonical_url but the manifest is still 'draft_verified' — the window
        # left if a crash lands AFTER the state marker but BEFORE the manifest was
        # flipped. The post is ALREADY live, so this pre-publish state check is
        # AUTHORITATIVE over the manifest draft_verified gate (which would otherwise
        # wave a duplicate publish through): forward-complete the manifest instead of
        # re-clicking. If the state row carries no published_url, keep the manifest's
        # existing value rather than overwriting it with '' (avoid persisting an empty
        # url). Identity caveat: inherits U1's URL-is-identity assumption — two
        # distinct packages sharing a canonical_url (mirror) would skip the second
        # publish. Accepted residual.
        published_url = state_url or manifest.get("backend", {}).get("published_url")
        mf.set_backend(manifest, status="published", published_url=published_url)
        mf.save(args.manifest, manifest)
    elif _state_is_publishing(args.state, manifest):
        # B1 crash-window detection: a prior run wrote status='publishing' (the
        # pre-reservation) but crashed before the browser publish completed or before
        # the manifest was flipped to 'published'. We cannot know whether the post is
        # now live without checking the backend. Stopping here prevents a silent
        # duplicate — worst case is operator confirmation, not a second live post.
        # Recovery:
        #   - Post IS live: set manifest backend.status=published + published_url=<URL>
        #     and re-run to forward-complete bookkeeping.
        #   - Post NOT live: sqlite3 <state.db>
        #     "UPDATE items SET status='draft_verified' WHERE canonical_url=<url>"
        #     then re-run.
        canonical_url = (manifest.get("source") or {}).get("canonical_url", "?")
        raise ExternalError(
            f"publish state is 'publishing' for {canonical_url}: a prior run crashed "
            f"mid-publish. Check the backend manually before re-running."
        )
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
        _reserve_publishing(args.state, manifest)  # B1: write 'publishing' before browser click
        with backend_driver.session(args.storage_state, args.headless, args.timeout_ms) as page:
            result = backend_driver.publish_draft(
                page, cfg, draft_url, pkg_dir=str(Path(args.manifest).parent),
                **backend_driver.retry_kwargs(cfg, args.retries))
        published_url = result["published_url"]
        # U4 (durable-first, MANIFEST before state): flip the manifest to 'published'
        # as the FIRST durable step after the live publish. manifest.save is an atomic
        # single-process write (U13), far more robust than the cross-process SQLite
        # dedup marker, which can hit a transient 'database is locked'. Establishing
        # 'published' in the manifest first is what makes a later marker-write failure
        # SAFE: if _mark_published (in the bookkeeping tail below) raises on a
        # transient lock and the per-stage _retry re-invokes this runner, re-entry
        # short-circuits on the manifest 'published' gate above and forward-completes
        # the marker — instead of re-clicking publish and creating a DUPLICATE LIVE
        # POST. (Writing the SQLite marker first, as the original U4 draft did,
        # reintroduced exactly that duplicate-publish on a transient marker lock.) The
        # remaining duplicate window is a hard crash strictly between publish_draft and
        # this save — the documented residual that needs backend-side dedup.
        mf.set_backend(manifest, status="published", published_url=published_url)
        mf.save(args.manifest, manifest)

    run_id = manifest.get("backend", {}).get("run_id")  # Q7: None for CLI-built manifests
    # Forward-complete only the bookkeeping that may be missing; every step here is
    # safe to repeat on a re-entry: the receipt is write-once, _mark_published is an
    # idempotent upsert, and the run record is guarded so a re-entry never appends a
    # second 'ok' row (runs.record_run is a bare INSERT — see _publish_run_recorded).
    # U4: the post is ALREADY live AND (on the fresh-publish path) the manifest is
    # already flipped to 'published', so a failure in this tail can never cause a
    # re-publish — the per-stage _retry / the next run re-enters via the manifest
    # 'published' short-circuit above. It is a recoverable published-but-unmarked
    # state: surface it as ExternalError(exit 4) so retry / the next run
    # forward-completes the missing state marker / receipt / run record, and never
    # silently swallow it.
    try:
        _mark_published(args.state, manifest, post_id, published_url)
        _write_receipt(args.manifest, post_id, published_url)
        if args.state and not _publish_run_recorded(args.state, post_id):
            runs.record_run(args.state, stage="publish", post_id=post_id,
                            status="ok", detail=published_url,
                            run_id=run_id, severity="info")  # Q7: lifecycle correlation
    except Exception as exc:  # noqa: BLE001 - re-classify as recoverable, see above
        raise ExternalError(
            f"published but post-publish bookkeeping failed (recoverable, re-run "
            f"to forward-complete): {exc}")
    audit.record(LOG_PATH, post_id, "publish-post", "ok", mf.now_iso(),
                 published_url=published_url)
    json.dump({"status": "published", "post_id": post_id, "published_url": published_url},
              sys.stdout)
    sys.stdout.write("\n")
    return 0


def _reserve_publishing(state_path: str | None, manifest: dict) -> None:
    """Write status='publishing' to the state row before the browser click (B1).

    On crash strictly between here and manifest.save('published'), the next re-entry
    sees 'publishing' and raises ExternalError — worst-case is 'operator confirm'
    rather than 'silent duplicate live post'. No-op when state_path is absent.
    """
    if not state_path:
        return
    canonical_url = (manifest.get("source") or {}).get("canonical_url")
    if not canonical_url:
        return
    title = manifest.get("content", {}).get("title") or ""
    with state_mod.connect(state_path) as conn:
        state_mod.upsert(conn, canonical_url=canonical_url, title=title,
                         title_hash=title_hash(title), status=state_mod.PUBLISHING,
                         now=mf.now_iso())


def _state_is_publishing(state_path: str | None, manifest: dict) -> bool:
    """True if the state row for this canonical_url is 'publishing' (B1 crash detection)."""
    if not state_path:
        return False
    canonical_url = (manifest.get("source") or {}).get("canonical_url")
    if not canonical_url:
        return False
    with state_mod.connect(state_path) as conn:
        return state_mod.is_publishing(conn, canonical_url)


def _state_published_url(state_path: str | None, manifest: dict) -> str | None:
    """Return the published_url from the durable state row iff this manifest's
    canonical_url is already marked 'published' in SQLite, else None.

    Authoritative over the manifest draft_verified gate for the already-published
    case: it closes the U4 mixed-state window (state 'published' + manifest
    'draft_verified') without re-clicking publish. Pure query, no writes. URL-only
    identity (inherits U1's mirror caveat). Returns None when no state path is
    configured (CLI publish without --state cannot consult dedup state).
    """
    if not state_path:
        return None
    canonical_url = (manifest.get("source") or {}).get("canonical_url")
    if not canonical_url:
        return None
    with state_mod.connect(state_path) as conn:
        if not state_mod.is_processed(conn, canonical_url):
            return None
        cur = conn.execute(
            "SELECT published_url FROM items WHERE canonical_url = ? LIMIT 1",
            (canonical_url,),
        )
        row = cur.fetchone()
    return row[0] if (row and row[0]) else None


def _publish_run_recorded(state_path: str | None, post_id: str) -> bool:
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


def _write_receipt(manifest_path: str, post_id: str, published_url: str) -> None:
    path = Path(manifest_path).parent / "publish_receipt.json"
    if path.exists():
        return  # write-once: keep the original published_at (the true publish time)
    receipt = {"post_id": post_id, "published_url": published_url, "published_at": mf.now_iso()}
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_published(state_path: str | None, manifest: dict, post_id: str, published_url: str) -> None:
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


def main(argv: list[str] | None = None) -> None:
    args = _parse(sys.argv[1:] if argv is None else argv)
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
