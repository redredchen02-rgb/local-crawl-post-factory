"""Unit tests for _retry(), _run_auto_pipeline(), and auto-pipeline wiring."""

import json
from unittest.mock import MagicMock, patch


from webui.app import _retry, _run_auto_pipeline


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
# _run_auto_pipeline()
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


class _FakeJob:
    """Minimal job stub that captures report/set_current calls."""
    def __init__(self):
        self.progress: list[str] = []
        self.current: str = ""

    @property
    def report_msgs(self):
        return self.progress


def _make_job():
    return _FakeJob()


@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_happy_path_all_succeed(mock_draft, mock_verify, mock_publish,
                                mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]
    job = _make_job()

    _run_auto_pipeline(job, cfg, built)

    assert mock_draft.call_count == 2
    assert mock_verify.call_count == 2
    assert mock_publish.call_count == 2
    assert mock_mark.call_count == 2
    # Summary message should mention 成功 2
    summary = job.progress[-1]
    assert "成功 2" in summary
    assert "失敗 0" in summary


@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_empty_built_early_return(mock_draft, mock_verify, mock_publish,
                                  mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    job = _make_job()

    _run_auto_pipeline(job, cfg, [])

    mock_draft.assert_not_called()
    mock_verify.assert_not_called()
    mock_publish.assert_not_called()
    # Should report the early-return message
    assert any("無新稿件" in m for m in job.progress)


@patch("webui.app.time.sleep")
@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_draft_fail_all_retries_skips_verify_and_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    job = _make_job()
    mock_draft.side_effect = RuntimeError("draft error")

    _run_auto_pipeline(job, cfg, built)

    assert mock_draft.call_count == 3  # 3 retry attempts
    mock_verify.assert_not_called()
    mock_publish.assert_not_called()
    summary = job.progress[-1]
    assert "失敗 1" in summary


@patch("webui.app.time.sleep")
@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_verify_fail_skips_publish_counted_as_verify_fail(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    job = _make_job()
    mock_verify.side_effect = RuntimeError("verify error")

    _run_auto_pipeline(job, cfg, built)

    mock_draft.assert_called_once()
    assert mock_verify.call_count == 3
    mock_publish.assert_not_called()
    summary = job.progress[-1]
    assert "驗證失敗 1" in summary


@patch("webui.app.time.sleep")
@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_one_draft_fails_others_continue(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, mock_sleep, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path), _make_built("p2", tmp_path)]
    job = _make_job()
    # p1 fails draft, p2 succeeds
    mock_draft.side_effect = [RuntimeError("p1 fail"), RuntimeError("p1 fail"),
                              RuntimeError("p1 fail"), None]

    _run_auto_pipeline(job, cfg, built)

    # p2 should still be verified and published
    assert mock_verify.call_count == 1
    assert mock_publish.call_count == 1
    summary = job.progress[-1]
    assert "成功 1" in summary
    assert "失敗 1" in summary


@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_reviewed_mark_called_before_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    job = _make_job()
    call_order = []
    mock_mark.side_effect = lambda *a, **kw: call_order.append("mark")
    mock_publish.side_effect = lambda ns: call_order.append("publish")

    _run_auto_pipeline(job, cfg, built)

    assert call_order == ["mark", "publish"]
    mock_mark.assert_called_once()


@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_reviewed_mark_failure_skips_publish(
        mock_draft, mock_verify, mock_publish, mock_mark, mock_record, tmp_path):
    cfg = _make_cfg(tmp_path)
    built = [_make_built("p1", tmp_path)]
    job = _make_job()
    mock_mark.side_effect = RuntimeError("db error")

    _run_auto_pipeline(job, cfg, built)

    mock_publish.assert_not_called()
    summary = job.progress[-1]
    assert "失敗 1" in summary


# ---------------------------------------------------------------------------
# Integration: _run_auto_pipeline is called from /crawl when auto_pipeline=True
# ---------------------------------------------------------------------------

def test_auto_pipeline_wired_into_crawl(tmp_path):
    """When auto_pipeline=True, _run_auto_pipeline is called after run_pipeline."""
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    from webui.app import create_app

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
    with (_patch("webui.app.pipeline.crawl_items", return_value=[]),
          _patch("webui.app.pipeline.run_pipeline",
                 return_value={"built": built, "failed": [], "skipped": 0}),
          _patch("webui.app._run_auto_pipeline") as mock_auto):
        response = client.post("/crawl")
        assert response.status_code == 200
        # Give the background thread time to run
        import time
        time.sleep(0.3)
        mock_auto.assert_called_once()
        _, call_cfg, call_built = mock_auto.call_args[0]
        assert call_built == built


def test_auto_pipeline_not_called_when_disabled(tmp_path):
    """When auto_pipeline=False (default), _run_auto_pipeline is NOT called."""
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    from webui.app import create_app
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

    with (_patch("webui.app.pipeline.crawl_items", return_value=[]),
          _patch("webui.app.pipeline.run_pipeline",
                 return_value={"built": [], "failed": [], "skipped": 0}),
          _patch("webui.app._run_auto_pipeline") as mock_auto):
        client.post("/crawl")
        import time
        time.sleep(0.3)
        mock_auto.assert_not_called()


# ---------------------------------------------------------------------------
# Gap-fill: defensive branches
# ---------------------------------------------------------------------------

@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_action_ns_none_skips_item(mock_draft, mock_verify, mock_publish,
                                   mock_mark, mock_record, tmp_path):
    """If _action_ns returns None (missing pkg dir), item is counted as failed."""
    cfg = _make_cfg(tmp_path)
    # built entry points to a non-existent package directory
    built = [{"post_id": "ghost", "title": "X",
              "manifest_path": str(tmp_path / "out" / "ghost" / "manifest.json")}]
    job = _make_job()

    _run_auto_pipeline(job, cfg, built)

    mock_draft.assert_not_called()
    summary = job.progress[-1]
    assert "失敗 1" in summary


@patch("webui.app.runs.record_run")
@patch("webui.app.reviewed.mark")
@patch("webui.app.publish_post._run")
@patch("webui.app.verify_draft._run")
@patch("webui.app.draft_post._run")
def test_manifest_missing_at_publish_skips(mock_draft, mock_verify, mock_publish,
                                           mock_mark, mock_record, tmp_path):
    """If manifest.json disappears after verify, publish is skipped as failure."""
    cfg = _make_cfg(tmp_path)
    pkg = tmp_path / "out" / "p1"
    pkg.mkdir(parents=True, exist_ok=True)
    import json
    manifest_path = pkg / "manifest.json"
    manifest_path.write_text(json.dumps({
        "content": {"title": "T", "body": "b"},
        "source": {"canonical_url": "https://x.com/p1"},
        "backend": {"status": "draft_verified"},
        "audit": {},
    }), encoding="utf-8")
    built = [{"post_id": "p1", "title": "T", "manifest_path": str(manifest_path)}]
    job = _make_job()

    def remove_manifest(ns):
        manifest_path.unlink(missing_ok=True)

    mock_verify.side_effect = remove_manifest

    _run_auto_pipeline(job, cfg, built)

    mock_publish.assert_not_called()
    summary = job.progress[-1]
    assert "失敗 1" in summary


def test_auto_pipeline_wired_empty_built(tmp_path):
    """When run_pipeline returns built=[], _run_auto_pipeline is called with []."""
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    from webui.app import create_app
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

    with (_patch("webui.app.pipeline.crawl_items", return_value=[]),
          _patch("webui.app.pipeline.run_pipeline",
                 return_value={"built": [], "failed": [], "skipped": 0}),
          _patch("webui.app._run_auto_pipeline") as mock_auto):
        client.post("/crawl")
        import time
        time.sleep(0.3)
        mock_auto.assert_called_once()
        _, _, call_built = mock_auto.call_args[0]
        assert call_built == []
