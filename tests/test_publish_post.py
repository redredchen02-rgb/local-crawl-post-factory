"""Error-contract suite for the highest-stakes, irreversible stage: publish-post.

publish-post is the only command that performs an irreversible external action (a
live publish click) AND writes durable dedupe state. This suite locks down the
*refusal* and *recoverability* contract around that action:

  - both publish gates (--approve, draft_verified) refuse BEFORE any browser call;
  - invalid / wrong-status manifests fail validation (exit 2) with an empty stdout
    and a single stderr diagnostic line — never a partial "published" payload;
  - a post-publish bookkeeping failure (e.g. SQLite lock / OSError) surfaces as a
    recoverable ExternalError (exit 4), never a silent skip;
  - re-entry after a successful publish is idempotent — no second live publish and
    no duplicate 'ok' run row;
  - U4 mixed-state recovery (durable state row 'published' but manifest still
    'draft_verified') forward-completes without re-clicking publish.

Exit-code contract (cpost.core.errors): 0 ok / 2 validation / 4 external.
Mocking mirrors tests/test_publish_gating.py: the REAL collaborator symbols on
``publish_post`` (backend_driver.session / publish_draft, audit.record,
_mark_published) are patched; nothing in cpost/cli/publish_post.py is modified.
"""

import contextlib
import json
from unittest.mock import patch

import pytest

from cpost.core import cli, runs
from cpost.core.errors import ExternalError, ValidationError
from cpost.cli import publish_post

BACKEND = "configs/backend.yaml"
POST_ID = "20260615_demo"
CANONICAL = "https://x.com/p1"
PUB_URL = "https://pub/p1"


def _publishable_manifest(tmp_path, status="draft_verified", **backend):
    """A manifest with everything publish needs: post_id, content, source url."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({
        "post_id": POST_ID,
        "content": {"title": "T", "body": "b"},
        "source": {"canonical_url": CANONICAL},
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
        retries=None,
        state=None,
    )
    base.update(kw)
    return type("NS", (), base)()


@contextlib.contextmanager
def _fake_session(*_a, **_kw):
    yield object()  # dummy page; publish_draft is mocked separately


def _publish_ok_rows(state_path):
    return [r for r in runs.list_runs(state_path, post_id=POST_ID)
            if r.get("stage") == "publish" and r.get("status") == "ok"]


def _no_browser():
    """Patch the real browser collaborators so a gate breach would be *visible*
    as a call. publish_draft must NEVER fire on any refusal path."""
    return (
        patch.object(publish_post.backend_driver, "session", _fake_session),
        patch.object(publish_post.backend_driver, "publish_draft",
                     return_value={"published_url": PUB_URL}),
        patch.object(publish_post.audit, "record"),
    )


# --------------------------------------------------------------------------- #
# Gate 1: explicit approval                                                    #
# --------------------------------------------------------------------------- #

def test_missing_approve_refuses_without_browser_action(tmp_path, capsys):
    """Gate 1 (R8): no --approve -> ValidationError(exit 2) and, critically, NO
    irreversible browser action. stdout stays empty; one stderr diagnostic line."""
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=False)
    s, pub, rec = _no_browser()
    with s, pub as mpub, rec:
        code = cli.run(lambda: publish_post._run(args))
    captured = capsys.readouterr()
    assert code == 2
    assert mpub.call_count == 0                  # no live publish on a refusal
    assert captured.out == ""                    # no partial "published" payload
    assert captured.err.strip() != ""            # exactly one diag line
    assert "\n" not in captured.err.strip()


def test_missing_approve_raises_validation_error_directly(tmp_path):
    """The gate raises a typed ValidationError (exit_code 2), independent of the
    cli.run wrapper — locks the exception kind, not just the mapped code."""
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=False)
    s, pub, rec = _no_browser()
    with s, pub as mpub, rec:
        with pytest.raises(ValidationError) as ei:
            publish_post._run(args)
    assert ei.value.exit_code == 2
    assert mpub.call_count == 0


# --------------------------------------------------------------------------- #
# Gate 2 / manifest validation                                                #
# --------------------------------------------------------------------------- #

def test_wrong_status_refuses_exit_2(tmp_path, capsys):
    """--approve present but manifest is not draft_verified (still package_built):
    Gate 2 refuses with exit 2, empty stdout, no publish."""
    args = _ns(manifest=_publishable_manifest(tmp_path, status="package_built"),
               approve=True, state=str(tmp_path / "state.sqlite"))
    s, pub, rec = _no_browser()
    with s, pub as mpub, rec:
        code = cli.run(lambda: publish_post._run(args))
    captured = capsys.readouterr()
    assert code == 2
    assert mpub.call_count == 0
    assert captured.out == ""
    assert captured.err.strip() != ""


def test_failed_status_refuses_exit_2(tmp_path):
    """A 'failed' manifest is a valid state but not publishable -> exit 2."""
    args = _ns(manifest=_publishable_manifest(tmp_path, status="failed"), approve=True)
    s, pub, rec = _no_browser()
    with s, pub as mpub, rec:
        assert cli.run(lambda: publish_post._run(args)) == 2
        assert mpub.call_count == 0


def test_invalid_manifest_json_exit_2(tmp_path, capsys):
    """Corrupt manifest JSON -> mf.load raises ValidationError(exit 2). stdout
    empty, one stderr diagnostic line, and no browser action."""
    bad = tmp_path / "manifest.json"
    bad.write_text("{not valid json", encoding="utf-8")
    args = _ns(manifest=str(bad), approve=True)
    s, pub, rec = _no_browser()
    with s, pub as mpub, rec:
        code = cli.run(lambda: publish_post._run(args))
    captured = capsys.readouterr()
    assert code == 2
    assert mpub.call_count == 0
    assert captured.out == ""
    assert captured.err.strip() != ""


def test_manifest_not_found_exit_2(tmp_path):
    """A missing manifest path -> ValidationError(exit 2) before any publish."""
    args = _ns(manifest=str(tmp_path / "does_not_exist.json"), approve=True)
    s, pub, rec = _no_browser()
    with s, pub as mpub, rec:
        assert cli.run(lambda: publish_post._run(args)) == 2
        assert mpub.call_count == 0


# --------------------------------------------------------------------------- #
# State-write failure -> recoverable ExternalError (exit 4), not a silent skip #
# --------------------------------------------------------------------------- #

def test_state_write_oserror_is_external_error_exit_4(tmp_path):
    """The post is ALREADY live; a failure writing durable dedupe state (SQLite
    lock / OSError) must NOT be swallowed — it surfaces as ExternalError(exit 4)
    so retry / the next run forward-completes the missing marker. Silent-skipping
    it would let the next run re-publish a duplicate."""
    state = str(tmp_path / "state.sqlite")
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=True, state=state,
               headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": PUB_URL}) as mpub, \
         patch.object(publish_post, "_mark_published",
                      side_effect=OSError("database is locked")), \
         patch.object(publish_post.audit, "record"):
        # mapped exit code
        assert cli.run(lambda: publish_post._run(args)) == 4
        # the live publish still happened exactly once (the tail failure is post-publish)
        assert mpub.call_count == 1


def test_state_write_oserror_raises_external_error_directly(tmp_path):
    """Same failure, asserted as the typed ExternalError(exit_code 4) and with an
    operator-actionable 'recoverable' message — locks the recovery contract."""
    state = str(tmp_path / "state.sqlite")
    args = _ns(manifest=_publishable_manifest(tmp_path), approve=True, state=state,
               headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": PUB_URL}), \
         patch.object(publish_post, "_mark_published",
                      side_effect=OSError("disk I/O error")), \
         patch.object(publish_post.audit, "record"):
        with pytest.raises(ExternalError) as ei:
            publish_post._run(args)
    assert ei.value.exit_code == 4
    assert "recoverable" in ei.value.message


# --------------------------------------------------------------------------- #
# Integration: re-entry idempotency                                            #
# --------------------------------------------------------------------------- #

def test_reentry_does_not_double_publish_or_double_record(tmp_path):
    """Running publish again on an already-'published' package (e.g. _retry fired
    because a post-publish step failed) must NOT re-click publish and must NOT add
    a second 'ok' run record. Converges to exit 0 both times."""
    state = str(tmp_path / "state.sqlite")
    mpath = _publishable_manifest(tmp_path)
    args = _ns(manifest=mpath, approve=True, state=state, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": PUB_URL}) as mpub, \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0   # first publish
        assert mpub.call_count == 1
        # manifest is now 'published'; re-invoke (simulating a tail-failure retry)
        assert cli.run(lambda: publish_post._run(args)) == 0   # re-entry
        assert mpub.call_count == 1                            # no second publish

    # exactly one published-ok row across both runs (no double-count)
    assert len(_publish_ok_rows(state)) == 1


def test_reentry_published_manifest_no_state_still_succeeds(tmp_path, capsys):
    """Re-entry on an already-'published' manifest WITHOUT --state (CLI publish has
    no dedup DB) still short-circuits to success and emits the published payload —
    never re-clicking publish nor falling through to Gate 2."""
    mpath = _publishable_manifest(tmp_path, status="published", published_url=PUB_URL)
    args = _ns(manifest=mpath, approve=True, state=None, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/SHOULD_NOT"}) as mpub, \
         patch.object(publish_post.audit, "record"):
        code = cli.run(lambda: publish_post._run(args))
    out = capsys.readouterr().out
    assert code == 0
    assert mpub.call_count == 0
    payload = json.loads(out)
    assert payload["status"] == "published"
    assert payload["published_url"] == PUB_URL


# --------------------------------------------------------------------------- #
# Integration: U4 mixed-state recovery (publish-tail ordering)                 #
# --------------------------------------------------------------------------- #

def test_mixed_state_recovery_forward_completes_without_republish(tmp_path):
    """U4 mixed state: a successful publish whose durable state row is written, but
    the process died before the manifest flip — state says 'published', manifest
    still 'draft_verified'. The authoritative pre-publish state check must
    forward-complete the manifest (no re-click) and report success. Behaves
    idempotently when re-run."""
    state = str(tmp_path / "state.sqlite")
    mpath = _publishable_manifest(tmp_path)
    # Seed the durable dedup row directly (no manifest flip) via the REAL helper,
    # so we exercise the actual _state_published_url query path.
    import cpost.core.state as state_mod
    from cpost.core.url_utils import title_hash
    with state_mod.connect(state) as conn:
        state_mod.upsert(conn, canonical_url=CANONICAL, title="T",
                         title_hash=title_hash("T"), status="published",
                         now="2026-06-15T00:00:00Z", post_id=POST_ID,
                         published_url=PUB_URL)
    args = _ns(manifest=mpath, approve=True, state=state, headless=True)
    with patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/SHOULD_NOT"}) as mpub, \
         patch.object(publish_post.audit, "record"):
        assert cli.run(lambda: publish_post._run(args)) == 0
        assert mpub.call_count == 0    # authoritative state check -> no re-publish
        # idempotent on a second pass
        assert cli.run(lambda: publish_post._run(args)) == 0
        assert mpub.call_count == 0

    # manifest forward-completed; no orphaned mixed state
    m = json.loads(open(mpath, encoding="utf-8").read())
    assert m["backend"]["status"] == "published"
    assert m["backend"]["published_url"] == PUB_URL
    # exactly one ok run row (U9 coupling preserved)
    assert len(_publish_ok_rows(state)) == 1


# --------------------------------------------------------------------------- #
# Helper early-return guards (state_path=None / missing canonical_url)         #
# --------------------------------------------------------------------------- #

def test_reserve_publishing_no_state_path():
    """_reserve_publishing returns immediately when state_path is None."""
    assert publish_post._reserve_publishing(None, {}) is None


def test_reserve_publishing_no_canonical_url():
    """_reserve_publishing returns immediately when canonical_url is missing."""
    assert publish_post._reserve_publishing("/tmp/dummy", {}) is None


def test_state_is_publishing_no_canonical_url():
    """_state_is_publishing returns False when canonical_url is missing."""
    assert publish_post._state_is_publishing("/tmp/dummy", {}) is False


def test_state_published_url_no_state_path():
    """_state_published_url returns None when state_path is None."""
    assert publish_post._state_published_url(None, {}) is None


def test_state_published_url_no_canonical_url():
    """_state_published_url returns None when canonical_url is missing."""
    assert publish_post._state_published_url("/tmp/dummy", {}) is None


def test_publish_run_recorded_no_state_path():
    """_publish_run_recorded returns False when state_path is None."""
    assert publish_post._publish_run_recorded(None, "p1") is False


def test_mark_published_no_canonical_url():
    """_mark_published returns immediately when canonical_url is missing."""
    assert publish_post._mark_published("/tmp/dummy", {}, "p1", "https://x") is None
