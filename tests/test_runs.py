"""U3: run history store."""

from core import runs, pipeline


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def test_record_and_list(tmp_path):
    db = _db(tmp_path)
    runs.record_run(db, stage="build", post_id="p1", status="ok", detail="標題")
    out = runs.list_runs(db)
    assert len(out) == 1
    assert out[0]["stage"] == "build"
    assert out[0]["post_id"] == "p1"
    assert out[0]["status"] == "ok"
    assert out[0]["ts"]


def test_failure_records_error(tmp_path):
    db = _db(tmp_path)
    runs.record_run(db, stage="publish", status="failed", error="boom")
    out = runs.list_runs(db)
    assert out[0]["status"] == "failed"
    assert "boom" in out[0]["error"]


def test_missing_db_lists_empty(tmp_path):
    assert runs.list_runs(str(tmp_path / "nope.sqlite")) == []


def test_newest_first_and_limit(tmp_path):
    db = _db(tmp_path)
    for i in range(5):
        runs.record_run(db, stage="build", post_id=f"p{i}", status="ok")
    out = runs.list_runs(db, limit=3)
    assert len(out) == 3
    assert out[0]["post_id"] == "p4"  # newest first


def test_pipeline_records_build_runs(tmp_path):
    cfg = {
        "template_path": "./templates/fixed-format.zh.yaml",
        "watermark_config": "./configs/watermark.yaml",
        "download_dir": str(tmp_path / "assets"),
        "out_dir": str(tmp_path / "out"),
        "state_path": _db(tmp_path),
        "audit_log": str(tmp_path / "audit.jsonl"),
        "limit": 30,
    }
    item = {"source_id": "ex", "url": "https://example.com/news/a",
            "canonical_url": "https://example.com/news/a", "title": "標題甲",
            "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"}
    pipeline.run_pipeline([item], cfg)
    rows = runs.list_runs(cfg["state_path"])
    assert any(r["stage"] == "build" and r["status"] == "ok" for r in rows)
