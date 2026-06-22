import contextlib
import json
from unittest.mock import patch

import pytest

from cpost.core import cli, runs, state as state_mod, url_utils
from cpost.core.errors import ExternalError
from cpost.cli import draft_post, publish_post

BACKEND = "configs/backend.yaml"


def _manifest(tmp_path, status):
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({"post_id": "20260615_demo", "backend": {"status": status}}),
        encoding="utf-8",
    )
    return str(p)


def _publishable_manifest(tmp_path, status="draft_verified", **backend):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({
        "post_id": "20260615_demo",
        "content": {"title": "T", "body": "b"},
        "source": {"canonical_url": "https://x.com/p1"},
        "backend": {"status": status, **backend},
    }), encoding="utf-8")
    return str(p)


def _ns(**kw):
    base = dict(
        manifest=None,
        backend=BACKEND,
        storage_state=None,
        headless=False,
        timeout_ms=5000,
        approve=False,
        dry_run=False,
        retries=None,
        state=None,
    )
    base.update(kw)
    return type("NS", (), base)()


@contextlib.contextmanager
def _fake_session(*_a, **_kw):
    yield object()  # dummy page; publish_draft is mocked separately


def _publish_ok_rows(state_path):
    return [r for r in runs.list_runs(state_path, post_id="20260615_demo")
            if r.get("stage") == "publish" and r.get("status") == "ok"]


def test_publish_without_approve_exits_2(tmp_path):
    args = _ns(manifest=_manifest(tmp_path, "draft_verified"), approve=False)
    assert cli.run(lambda: publish_post._run(args)) == 2


def test_publish_approve_but_wrong_status_exits_2(tmp_path):
    args = _ns(manifest=_manifest(tmp_path, "package_built"), approve=True)
    assert cli.run(lambda: publish_post._run(args)) == 2


def test_draft_dry_run_validated_exits_0(tmp_path, capsys):
    args = _ns(manifest=_manifest(tmp_path, "package_built"), dry_run=True)
    code = cli.run(lambda: draft_post._run(args))
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out)["status"] == "validated"
    assert json.loads(out)["post_id"] == "20260615_demo"


# --- U3 (R2): publish is idempotent on re-entry, recorded exactly once ---

def test_publish_idempotent_on_reentry(tmp_path):
    """A re-entry after a successful publish (e.g. _retry firing because a
    post-publish bookkeeping step failed) must NOT re-click publish, must report
    success, and must leave exactly ONE 'ok' publish run row.

    Pre-fix this fails: the second call hits Gate 2 ('draft not verified') because
    the manifest is already 'published', so the live post is reported as failed.
    """
    state = str(tmp_path / "state.sqlite")
    mpath = _publishable_manifest(tmp_path)
    args = _ns(manifest=mpath, approve=True, state=state, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}) as mpub, \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0       # first publish
        assert mpub.call_count == 1
        # Manifest is now 'published'; re-invoking simulates _retry after a tail
        # failure. It must converge to success WITHOUT re-publishing.
        assert cli.run(lambda: publish_post._run(args)) == 0       # re-entry
        assert mpub.call_count == 1                                # no second publish

    assert len(_publish_ok_rows(state)) == 1                       # no double-count


def test_publish_records_single_ok_with_published_url(tmp_path):
    state = str(tmp_path / "state.sqlite")
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=True, state=state, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}), \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0
    rows = _publish_ok_rows(state)
    assert len(rows) == 1
    assert rows[0]["detail"] == "https://pub/p1"


# --- U4: cross-run duplicate-publish prevention (reordered/idempotent tail) ---

def _seed_published_state(state_path, canonical_url, *, published_url, title="T"):
    """Write the durable dedup row WITHOUT touching the manifest, simulating the
    U4 mixed-state crash: publish + _mark_published succeeded but the process died
    before mf.save flipped the manifest off 'draft_verified'."""
    with state_mod.connect(state_path) as conn:
        state_mod.upsert(conn, canonical_url=canonical_url, title=title,
                         title_hash=url_utils.title_hash(title), status="published",
                         now="2026-06-15T00:00:00Z", post_id="20260615_demo",
                         published_url=published_url)


def test_mixed_state_reentry_forward_completes_without_republishing(tmp_path):
    """edge(mixed-state): durable state row says 'published' but the manifest is
    still 'draft_verified' (crash between _mark_published and mf.save).

    The next run must NOT re-click publish (no silent orphan / duplicate live post);
    the authoritative pre-publish state check forward-completes the manifest to
    'published' and reports success.
    """
    state = str(tmp_path / "state.sqlite")
    mpath = _publishable_manifest(tmp_path)  # canonical_url https://x.com/p1
    _seed_published_state(state, "https://x.com/p1", published_url="https://pub/p1")
    args = _ns(manifest=mpath, approve=True, state=state, headless=True)

    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/SHOULD_NOT"}) as mpub, \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0
        assert mpub.call_count == 0  # authoritative state check -> no re-publish

    # manifest forward-completed; operator-visible state consistent (no orphan)
    m = json.loads(open(mpath, encoding="utf-8").read())
    assert m["backend"]["status"] == "published"
    assert m["backend"]["published_url"] == "https://pub/p1"
    # coupling with U9: still exactly one 'ok' run row
    assert len(_publish_ok_rows(state)) == 1


def test_state_dedup_row_is_first_durable_step_blocks_next_run(tmp_path):
    """error: after a successful publish the dedup row exists; a fresh run for the
    same canonical_url whose manifest is reset to 'draft_verified' (simulating a
    tail crash before the manifest flip) does NOT re-publish."""
    state = str(tmp_path / "state.sqlite")
    mpath = _publishable_manifest(tmp_path)
    args = _ns(manifest=mpath, approve=True, state=state, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}) as mpub, \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0
        assert mpub.call_count == 1
        # Simulate a tail crash that left the manifest at draft_verified despite the
        # live publish + durable state row.
        m = json.loads(open(mpath, encoding="utf-8").read())
        m["backend"]["status"] = "draft_verified"
        m["backend"].pop("published_url", None)
        open(mpath, "w", encoding="utf-8").write(json.dumps(m))

        assert cli.run(lambda: publish_post._run(args)) == 0
        assert mpub.call_count == 1  # state check authoritative -> no second publish
    assert len(_publish_ok_rows(state)) == 1


def test_mark_published_lock_signals_not_silent_skip(tmp_path):
    """error: a transient failure writing the dedup marker after a live publish must
    surface as ExternalError (recoverable signal), never silently skip the marker
    (which would let the next run re-publish a duplicate)."""
    state = str(tmp_path / "state.sqlite")
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=True, state=state,
               headless=True)
    boom = type("Locked", (Exception,), {})
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}), \
         patch.object(publish_post, "_mark_published", side_effect=boom("db locked")), \
         patch.object(publish_post.audit, "record"):
        with pytest.raises(ExternalError):
            publish_post._run(args)


def test_auto_pipeline_rerun_same_url_no_second_live_post(tmp_path):
    """integration-ish: re-running publish over the same canonical_url after a tail
    crash (durable dedup row present, manifest still draft_verified) yields no second
    live post and no double 'ok' run row within the protected window."""
    state = str(tmp_path / "state.sqlite")
    mpath = _publishable_manifest(tmp_path)
    args = _ns(manifest=mpath, approve=True, state=state, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}) as mpub, \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0
        # reset manifest to pre-flip state, keep durable dedup row -> protected window
        m = json.loads(open(mpath, encoding="utf-8").read())
        m["backend"]["status"] = "draft_verified"
        open(mpath, "w", encoding="utf-8").write(json.dumps(m))
        for _ in range(3):
            assert cli.run(lambda: publish_post._run(args)) == 0
        assert mpub.call_count == 1            # exactly one live publish
    assert len(_publish_ok_rows(state)) == 1   # exactly one ok row (U9 coupling)


def test_marker_lock_then_retry_does_not_republish(tmp_path):
    """BLOCKER regression: a transient _mark_published lock right after a live publish
    must NOT cause a duplicate live post when the per-stage _retry re-invokes the
    runner within the SAME run.

    Durable-first ordering is MANIFEST-before-state: attempt 1 publishes live, flips
    the manifest to 'published', then the state-marker write fails (lock) ->
    ExternalError. _retry's re-invocation (attempt 2) sees the manifest already
    'published', short-circuits, and forward-completes the marker — exactly one live
    publish across all attempts.

    Pre-fix (state marker written BEFORE the manifest flip) this fails: attempt 1
    raises before the manifest is flipped, so attempt 2 finds a 'draft_verified'
    manifest with no dedup row and re-clicks publish -> two live posts.
    """
    state = str(tmp_path / "state.sqlite")
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=True, state=state,
               headless=True)
    real_mark = publish_post._mark_published
    calls = {"n": 0}

    def flaky_mark(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise type("Locked", (Exception,), {})("database is locked")
        return real_mark(*a, **kw)  # lock cleared on retry

    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}) as mpub, \
         patch.object(publish_post, "_mark_published", side_effect=flaky_mark), \
         patch.object(publish_post.audit, "record"):
        with pytest.raises(ExternalError):
            publish_post._run(args)                                # attempt 1: marker lock
        assert cli.run(lambda: publish_post._run(args)) == 0       # _retry attempt 2: converges
        assert mpub.call_count == 1                                # no duplicate live publish

    m = json.loads(open(args.manifest, encoding="utf-8").read())
    assert m["backend"]["status"] == "published"
    assert len(_publish_ok_rows(state)) == 1                       # exactly one ok row (U9)
