"""Unit tests for cpost.core.pipeline._retry, run_auto_pipeline, and WebUI wiring."""

import json
from unittest.mock import MagicMock, patch

from cpost.core.pipeline import _retry, run_auto_pipeline
from cpost.webui._auto_pipeline import _run_auto_pipeline


# ---------------------------------------------------------------------------
# _retry()
# ---------------------------------------------------------------------------

def test_retry_succeeds_first_attempt():
    fn = MagicMock(return_value="ok")
    result, exc = _retry(fn, times=3, delay=0)
    assert result == "ok"
    assert exc is None
    fn.assert_called_once()


def test_retry_succeeds_on_second_attempt():
    fn = MagicMock(side_effect=[RuntimeError("boom"), "ok"])
    result, exc = _retry(fn, times=3, delay=0)
    assert result == "ok"
    assert exc is None
    assert fn.call_count == 2


def test_retry_exhausted_returns_last_exception():
    err = RuntimeError("always fails")
    fn = MagicMock(side_effect=err)
    result, exc = _retry(fn, times=3, delay=0)
    assert result is None
    assert exc is err
    assert fn.call_count == 3


def test_retry_single_attempt():
    err = ValueError("nope")
    fn = MagicMock(side_effect=err)
    result, exc = _retry(fn, times=1, delay=0)
    assert result is None
    assert exc is err
    fn.assert_called_once()


# ---------------------------------------------------------------------------
# run_auto_pipeline() — unit tests via cpost.core.pipeline directly
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path):
    return {
        "out_dir": str(tmp_path / "out"),
        "state_path": str(tmp_path / "state.sqlite"),
        "backend_config": "./configs/backend.yaml",
        "storage_state": "./auth/storage-state.json",
    }


def _make_manifest(tmp_path, post_id: str, title: str = "Test Title") -> dict:
    pkg = tmp_path / "out" / post_id
    pkg.mkdir(parents=True, exist_ok=True)
    m = {
        "content": {"title": title, "body": "body text"},
        "source": {"canonical_url": f"https://example.com/{post_id}"},
        "backend": {"status": "draft_verified"},
        "audit": {},
    }
    (pkg / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return m


def _make_built(post_id: str, tmp_path, title: str = "Test Title") -> dict:
    _make_manifest(tmp_path, post_id, title)
    return {
        "post_id": post_id,
        "title": title,
        "manifest_path": str(tmp_path / "out" / post_id / "manifest.json"),
    }


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_happy_path_all_succeed(mock_draft, mock_verify, mock_publish,
                                mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]
    progress = []

    run_auto_pipeline(built, cfg, on_progress=progress.append)

    assert mock_draft.call_count == 2
    assert mock_verify.call_count == 2
    assert mock_publish.call_count == 2
    assert mock_mark.call_count == 2
    summary = progress[-1]
    assert "成功 2" in summary
    assert "失敗 0" in summary


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_empty_built_early_return(mock_draft, mock_verify, mock_publish,
                                  mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    progress = []

    run_auto_pipeline([], cfg, on_progress=progress.append)

    mock_draft.assert_not_called()
    mock_verify.assert_not_called()
    mock_publish.assert_not_called()
    assert any("無新稿件" in m for m in progress)


@patch("cpost.core.pipeline.time.sleep")
@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_draft_fail_all_retries_skips_verify_and_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    progress = []
    mock_draft.side_effect = RuntimeError("draft error")

    run_auto_pipeline(built, cfg, on_progress=progress.append)

    assert mock_draft.call_count == 3
    mock_verify.assert_not_called()
    mock_publish.assert_not_called()
    assert "失敗 1" in progress[-1]


@patch("cpost.core.pipeline.time.sleep")
@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_verify_fail_skips_publish_counted_as_verify_fail(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    progress = []
    mock_verify.side_effect = RuntimeError("verify error")

    run_auto_pipeline(built, cfg, on_progress=progress.append)

    mock_draft.assert_called_once()
    assert mock_verify.call_count == 3
    mock_publish.assert_not_called()
    assert "驗證失敗 1" in progress[-1]


@patch("cpost.core.pipeline.time.sleep")
@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_one_draft_fails_others_continue(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]
    progress = []
    mock_draft.side_effect = [RuntimeError("p1 fail"), RuntimeError("p1 fail"),
                              RuntimeError("p1 fail"), None]

    run_auto_pipeline(built, cfg, on_progress=progress.append)

    assert mock_verify.call_count == 1
    assert mock_publish.call_count == 1
    summary = progress[-1]
    assert "成功 1" in summary
    assert "失敗 1" in summary


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_reviewed_mark_called_before_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    call_order = []
    mock_mark.side_effect = lambda *a, **kw: call_order.append("mark")
    mock_publish.side_effect = lambda ns: call_order.append("publish")

    run_auto_pipeline(built, cfg)

    assert call_order == ["mark", "publish"]
    mock_mark.assert_called_once()


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_reviewed_mark_failure_skips_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    """If reviewed.mark raises for a publish item, the publish runner is NOT
    invoked for it and the item is recorded in result['failed'] at stage
    'publish'. This locks the gate: a failed approval pre-step never reaches
    publish_post.run."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    progress = []
    mock_mark.side_effect = RuntimeError("db error")

    result = run_auto_pipeline(built, cfg, on_progress=progress.append)

    assert mock_publish.call_count == 0
    mock_publish.assert_not_called()
    assert "失敗 1" in progress[-1]
    assert result["ok"] == 0
    assert len(result["failed"]) == 1
    f = result["failed"][0]
    assert f["post_id"] == "p1" and f["stage"] == "publish"


# ---------------------------------------------------------------------------
# R14 characterization: locks the CURRENT observable behavior so the
# single-stage-runner refactor cannot change it. (Behavior-preserving.)
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_stage_sequencing_draft_then_verify_then_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    """Each item flows draft -> verify -> publish in that global per-stage order:
    ALL drafts happen before ANY verify, ALL verifies before ANY publish."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]
    order = []
    mock_draft.side_effect = lambda ns: order.append(("draft", ns.manifest))
    mock_verify.side_effect = lambda ns: order.append(("verify", ns.manifest))
    mock_publish.side_effect = lambda ns: order.append(("publish", ns.manifest))

    run_auto_pipeline(built, cfg)

    stages = [s for s, _ in order]
    # All drafts, then all verifies, then all publishes (no interleaving).
    assert stages == ["draft", "draft", "verify", "verify", "publish", "publish"]


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_returns_autopipeline_result_shape_and_counters(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    """The returned dict keeps the AutoPipelineResult contract: ok / failed /
    verify_fail_count, with the exact counters for an all-success batch."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]

    result = run_auto_pipeline(built, cfg)

    assert set(result.keys()) == {"ok", "failed", "verify_fail_count"}
    assert result == {"ok": 2, "failed": [], "verify_fail_count": 0}


@patch("cpost.core.pipeline.time.sleep")
@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_failed_list_records_failing_stage_per_item(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    """An item failing one stage is recorded in result['failed'] tagged with that
    stage; a sibling item that succeeds is NOT aborted by it."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]
    # p1 fails at verify (all retries); p2 sails through.
    mock_verify.side_effect = [RuntimeError("v fail"), RuntimeError("v fail"),
                               RuntimeError("v fail"), None]

    result = run_auto_pipeline(built, cfg)

    assert result["ok"] == 1                       # p2 published
    assert result["verify_fail_count"] == 1        # p1 failed verify
    assert len(result["failed"]) == 1
    f = result["failed"][0]
    assert f["post_id"] == "p1" and f["stage"] == "verify"
    assert mock_publish.call_count == 1            # only p2 reached publish


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_record_run_invoked_per_stage_with_status_ok(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    """_run_stage records a status=ok run for draft and verify only. The publish
    success run is owned by publish_post.run (mocked here), so _run_stage must NOT
    record it — recording in both places double-counts the publish (U9)."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]

    run_auto_pipeline(built, cfg)

    by_stage = {}
    for call in mock_record.call_args_list:
        kw = call.kwargs
        if kw.get("status") == "ok":
            by_stage[kw["stage"]] = kw
    assert set(by_stage) == {"draft", "verify"}  # U9: publish not recorded here
    assert by_stage["draft"].get("detail") is None  # draft/verify carry no detail
    assert by_stage["verify"].get("detail") is None


def test_publish_success_recorded_exactly_once_end_to_end(tmp_path):
    """U9 integration: with the REAL publish_post.run (browser mocked), one
    successful auto-pipeline publish leaves exactly ONE publish 'ok' run row —
    _run_stage no longer duplicates the record publish_post.run already writes."""
    import contextlib
    from unittest.mock import patch

    from cpost.cli import publish_post
    from cpost.core import runs

    cfg = _make_cfg(tmp_path)
    pkg = tmp_path / "out" / "p1"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "post_id": "p1",  # publish_post.run asserts a real post_id (build always sets it)
        "content": {"title": "T", "body": "b"},
        "source": {"canonical_url": "https://example.com/p1"},
        "backend": {"status": "draft_verified"},
        "audit": {},
    }), encoding="utf-8")
    built = [{"post_id": "p1", "title": "T", "manifest_path": str(pkg / "manifest.json")}]

    @contextlib.contextmanager
    def _fake_session(*_a, **_kw):
        yield object()

    with patch("cpost.core.pipeline.draft_post.run"), \
         patch("cpost.core.pipeline.verify_draft.run"), \
         patch.object(publish_post.backend_driver, "session", _fake_session), \
         patch.object(publish_post.backend_driver, "publish_draft",
                      return_value={"published_url": "https://pub/p1"}), \
         patch.object(publish_post.audit, "record"):
        result = run_auto_pipeline(built, cfg)

    assert result["ok"] == 1
    ok_pub = [r for r in runs.list_runs(cfg["state_path"], post_id="p1")
              if r["stage"] == "publish" and r["status"] == "ok"]
    assert len(ok_pub) == 1


@patch("cpost.core.pipeline.time.sleep")
@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_retry_applied_per_stage(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    """Each stage runner is wrapped by _retry (3 attempts) — verify failing all
    three attempts is retried exactly 3 times, like the pre-refactor loops."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    mock_verify.side_effect = RuntimeError("v")

    run_auto_pipeline(built, cfg)

    assert mock_draft.call_count == 1     # succeeded first attempt
    assert mock_verify.call_count == 3    # retried to exhaustion


@patch("cpost.core.pipeline.time.sleep")
@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_note_expiry_called_on_session_expiry_each_stage(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    """on_session_expired fires when a stage raises SessionExpiredError — checked
    at the draft stage (same _note_expiry path used by verify/publish)."""
    from cpost.core.errors import SessionExpiredError
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    mock_draft.side_effect = SessionExpiredError("expired")
    expired = []

    run_auto_pipeline(built, cfg, on_session_expired=lambda c: expired.append(c))

    assert expired == [cfg]               # called once with cfg
    mock_verify.assert_not_called()       # draft failure stops the item


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_publish_passes_approve_and_expected_content_id(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    """Publish-only contract: the invocation handed to publish_post.run carries
    approve=True and expected_content_id (the reviewed content-id), while
    draft/verify invocations do not approve and never leak the gate fields.

    The draft/verify assertions are Gate-2 leak guards: if a future refactor
    leaks approve/expected_content_id into the draft or verify invocation, the
    publish approval gate is no longer the only place those fields are set."""
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    seen = {}
    mock_draft.side_effect = lambda ns: seen.__setitem__("draft", ns)
    mock_verify.side_effect = lambda ns: seen.__setitem__("verify", ns)
    mock_publish.side_effect = lambda ns: seen.__setitem__("publish", ns)

    run_auto_pipeline(built, cfg)

    # DRAFT must not carry the publish gate fields.
    assert seen["draft"].approve is False
    assert seen["draft"].dry_run is False
    assert seen["draft"].expected_content_id is None
    # VERIFY must not carry the publish gate fields either.
    assert seen["verify"].approve is False
    assert seen["verify"].expected_content_id is None
    # PUBLISH alone approves and carries the reviewed content-id.
    assert seen["publish"].approve is True
    # expected_content_id is the cid passed to reviewed.mark for the same item.
    cid = mock_mark.call_args[0][2]
    assert seen["publish"].expected_content_id == cid


# ---------------------------------------------------------------------------
# Defensive branches
# ---------------------------------------------------------------------------

@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_action_ns_none_skips_item(mock_draft, mock_verify, mock_publish,
                                   mock_mark, mock_record, tmp_path):
    """If manifest path does not exist, item is counted as failed."""
    cfg = _make_cfg(tmp_path)
    built = [{"post_id": "ghost", "title": "X",
              "manifest_path": str(tmp_path / "out" / "ghost" / "manifest.json")}]
    progress = []

    run_auto_pipeline(built, cfg, on_progress=progress.append)

    mock_draft.assert_not_called()
    assert "失敗 1" in progress[-1]


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_manifest_missing_at_publish_skips(mock_draft, mock_verify, mock_publish,
                                           mock_mark, mock_record, tmp_path):
    """If manifest.json disappears after verify, publish is skipped as failure."""
    cfg = _make_cfg(tmp_path)
    pkg = tmp_path / "out" / "p1"
    pkg.mkdir(parents=True, exist_ok=True)
    manifest_path = pkg / "manifest.json"
    manifest_path.write_text(json.dumps({
        "content": {"title": "T", "body": "b"},
        "source": {"canonical_url": "https://x.com/p1"},
        "backend": {"status": "draft_verified"},
        "audit": {},
    }), encoding="utf-8")
    built = [{"post_id": "p1", "title": "T", "manifest_path": str(manifest_path)}]
    progress = []

    def remove_manifest(ns):
        manifest_path.unlink(missing_ok=True)

    mock_verify.side_effect = remove_manifest

    run_auto_pipeline(built, cfg, on_progress=progress.append)

    mock_publish.assert_not_called()
    assert "失敗 1" in progress[-1]


@patch("cpost.core.pipeline.runs.record_run")
@patch("cpost.core.pipeline.reviewed.mark")
@patch("cpost.core.pipeline.publish_post.run")
@patch("cpost.core.pipeline.verify_draft.run")
@patch("cpost.core.pipeline.draft_post.run")
def test_missing_manifest_error_strings_are_stage_distinct(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    """The per-stage missing-manifest error string differs by stage: draft/verify
    record '找不到此貼文包', publish records '找不到 manifest'. Catches a
    copy-paste swap of the two strings that every other test would miss."""
    cfg = _make_cfg(tmp_path)
    # p_draft: manifest never exists -> fails at DRAFT with '找不到此貼文包'.
    p_draft = {"post_id": "p_draft", "title": "D",
               "manifest_path": str(tmp_path / "out" / "p_draft" / "manifest.json")}
    # p_pub: manifest exists through verify, then vanishes -> fails at PUBLISH
    # with '找不到 manifest'.
    _make_built("p_pub", tmp_path)
    pub_manifest = tmp_path / "out" / "p_pub" / "manifest.json"
    p_pub = {"post_id": "p_pub", "title": "P", "manifest_path": str(pub_manifest)}

    def remove_pub_manifest(ns):
        if ns.manifest == str(pub_manifest):
            pub_manifest.unlink(missing_ok=True)

    mock_verify.side_effect = remove_pub_manifest

    result = run_auto_pipeline([p_draft, p_pub], cfg)

    by_pid = {f["post_id"]: f for f in result["failed"]}
    assert by_pid["p_draft"]["stage"] == "draft"
    assert by_pid["p_draft"]["error"] == "找不到此貼文包"
    assert by_pid["p_pub"]["stage"] == "publish"
    assert by_pid["p_pub"]["error"] == "找不到 manifest"
    # Guard against the swap specifically.
    assert by_pid["p_draft"]["error"] != by_pid["p_pub"]["error"]


# ---------------------------------------------------------------------------
# WebUI adapter: _run_auto_pipeline bridges to core
# ---------------------------------------------------------------------------

def test_webui_adapter_delegates_to_core(tmp_path):
    """_run_auto_pipeline calls pipeline.run_auto_pipeline with correct callbacks."""
    built = [{"post_id": "p1", "title": "T", "manifest_path": "/x/manifest.json"}]
    cfg = _make_cfg(tmp_path)

    class _FakeJob:
        progress: list[str] = []
        current: str = ""

    job = _FakeJob()

    with patch("cpost.webui._auto_pipeline.pipeline.run_auto_pipeline") as mock_core:
        mock_core.return_value = {"ok": 1, "failed": [], "verify_fail_count": 0}
        result = _run_auto_pipeline(job, cfg, built)
        assert result == {"ok": 1, "failed": [], "verify_fail_count": 0}
        mock_core.assert_called_once()
        call_kwargs = mock_core.call_args[1]
        assert call_kwargs["on_progress"] is not None
        assert call_kwargs["on_status"] is not None
        # callbacks should bridge to jobs
        with patch("cpost.webui._auto_pipeline.jobs.report") as mock_report:
            call_kwargs["on_progress"]("hello")
            mock_report.assert_called_once_with(job, "hello")


# ---------------------------------------------------------------------------
# Integration: _run_auto_pipeline is called from /crawl when auto_pipeline=True
# ---------------------------------------------------------------------------

def test_auto_pipeline_wired_into_crawl(tmp_path):
    """When auto_pipeline=True, _run_auto_pipeline is called after run_pipeline."""
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app

    config_path = str(tmp_path / "webui.yaml")
    import yaml
    (tmp_path / "webui.yaml").write_text(
        yaml.dump({"start_url": "https://example.com", "auto_pipeline": True,
                   "state_path": str(tmp_path / "state.sqlite"),
                   "out_dir": str(tmp_path / "out"),
                   "download_dir": str(tmp_path / "assets"),
                   "storage_state": str(tmp_path / "ss.json")}),
        encoding="utf-8")

    app = create_app(config_path)
    client = TestClient(app, raise_server_exceptions=False)

    built = [{"post_id": "p1", "title": "T", "manifest_path": "/tmp/p1/manifest.json"}]
    with (_patch("cpost.webui.routers.crawl.pipeline.crawl_items", return_value=[]),
          _patch("cpost.webui.routers.crawl.pipeline.run_pipeline",
                 return_value={"built": built, "failed": [], "skipped": 0}),
          _patch("cpost.webui.routers.crawl._run_auto_pipeline") as mock_auto):
        mock_auto.return_value = {"ok": 1, "failed": [], "verify_fail_count": 0}
        response = client.post("/crawl")
        assert response.status_code == 200
        import time
        time.sleep(0.3)
        mock_auto.assert_called_once()
        call_job, call_cfg, call_built = mock_auto.call_args[0]
        assert call_built == built
        from cpost.core import jobs as jobs_mod
        job = jobs_mod.get(call_job.id)
        assert job["result"]["auto_pipeline"]["ok"] == 1


def test_auto_pipeline_not_called_when_disabled(tmp_path):
    """When auto_pipeline=False (default), _run_auto_pipeline is NOT called."""
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    import yaml

    config_path = str(tmp_path / "webui.yaml")
    (tmp_path / "webui.yaml").write_text(
        yaml.dump({"start_url": "https://example.com", "auto_pipeline": False,
                   "state_path": str(tmp_path / "state.sqlite"),
                   "out_dir": str(tmp_path / "out"),
                   "download_dir": str(tmp_path / "assets"),
                   "storage_state": str(tmp_path / "ss.json")}),
        encoding="utf-8")

    app = create_app(config_path)
    client = TestClient(app, raise_server_exceptions=False)

    with (_patch("cpost.webui.routers.crawl.pipeline.crawl_items", return_value=[]),
          _patch("cpost.webui.routers.crawl.pipeline.run_pipeline",
                 return_value={"built": [], "failed": [], "skipped": 0}),
          _patch("cpost.webui.routers.crawl._run_auto_pipeline") as mock_auto):
        client.post("/crawl")
        import time
        time.sleep(0.3)
        mock_auto.assert_not_called()


def test_auto_pipeline_wired_empty_built(tmp_path):
    """When run_pipeline returns built=[], _run_auto_pipeline is called with []."""
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    import yaml

    config_path = str(tmp_path / "webui.yaml")
    (tmp_path / "webui.yaml").write_text(
        yaml.dump({"start_url": "https://example.com", "auto_pipeline": True,
                   "state_path": str(tmp_path / "state.sqlite"),
                   "out_dir": str(tmp_path / "out"),
                   "download_dir": str(tmp_path / "assets"),
                   "storage_state": str(tmp_path / "ss.json")}),
        encoding="utf-8")

    app = create_app(config_path)
    client = TestClient(app, raise_server_exceptions=False)

    with (_patch("cpost.webui.routers.crawl.pipeline.crawl_items", return_value=[]),
          _patch("cpost.webui.routers.crawl.pipeline.run_pipeline",
                 return_value={"built": [], "failed": [], "skipped": 0}),
          _patch("cpost.webui.routers.crawl._run_auto_pipeline") as mock_auto):
        client.post("/crawl")
        import time
        time.sleep(0.3)
        mock_auto.assert_called_once()
        call_job, call_cfg, call_built = mock_auto.call_args[0]
        assert call_built == []


# ---------------------------------------------------------------------------
# R14: the single typed backend-invocation contract (replaces SimpleNamespace)
# ---------------------------------------------------------------------------

def test_backend_invocation_timeout_default_unifies_on_constant():
    """The contract's timeout_ms default == backend_driver.DEFAULT_TIMEOUT_MS.

    This locks the audit's drift fix: core/pipeline.py used to hardcode 30_000
    while the webui used the named constant. Now there is one canonical default.
    """
    from cpost.browser import backend_driver
    from cpost.core.backend_args import BackendInvocation

    inv = BackendInvocation(manifest="m", backend="b", storage_state="s", state="st")
    assert inv.timeout_ms == backend_driver.DEFAULT_TIMEOUT_MS


def test_backend_invocation_attribute_access_for_runners():
    """Runners read args by attribute (args.manifest, args.dry_run, args.approve,
    args.expected_content_id). The dataclass must expose all of them with the
    inert defaults the draft/verify path relies on."""
    from cpost.core.backend_args import BackendInvocation

    inv = BackendInvocation(manifest="m", backend="b", storage_state="s", state="st")
    assert inv.manifest == "m"
    assert inv.backend == "b"
    assert inv.storage_state == "s"
    assert inv.state == "st"
    assert inv.headless is True
    assert inv.retries is None
    assert inv.dry_run is False
    assert inv.approve is False
    assert inv.expected_content_id is None


def test_default_timeout_ms_import_error_fallback():
    """When browser module is absent, _default_timeout_ms returns CORE_DEFAULT_TIMEOUT_MS (backend_args.py:33-34)."""
    import builtins as _b
    import importlib
    import cpost.core.backend_args as ba

    # purge browser modules so the import inside _default_timeout_ms will fail
    import sys as _sys
    for _k in list(_sys.modules):
        if 'browser' in _k or 'playwright' in _k:
            del _sys.modules[_k]

    _real_import = _b.__import__
    def _mock_import(name, *a, **kw):
        if 'cpost.browser' in name:
            raise ImportError(f'No module named {name}')
        return _real_import(name, *a, **kw)
    _b.__import__ = _mock_import
    importlib.reload(ba)

    result = ba._default_timeout_ms()
    assert result == ba.CORE_DEFAULT_TIMEOUT_MS

    _b.__import__ = _real_import
    importlib.reload(ba)

