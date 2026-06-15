"""U3: run history store. U7: run_id/severity correlation + migration."""

import sqlite3

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


# --- U7 (R9): run_id / severity + idempotent migration -----------------------

def test_record_with_run_id_and_severity(tmp_path):
    db = _db(tmp_path)
    runs.record_run(db, stage="build", post_id="p1", status="ok",
                    run_id="r-1", severity="info")
    out = runs.list_runs(db)
    assert out[0]["run_id"] == "r-1"
    assert out[0]["severity"] == "info"


def test_filter_by_post_id_and_severity(tmp_path):
    db = _db(tmp_path)
    runs.record_run(db, stage="build", post_id="p1", status="ok", severity="info")
    runs.record_run(db, stage="build", post_id="p2", status="failed", severity="error")
    assert [r["post_id"] for r in runs.list_runs(db, post_id="p1")] == ["p1"]
    assert [r["severity"] for r in runs.list_runs(db, severity="error")] == ["error"]


def test_new_run_id_unique_and_monotonic():
    a, b = runs.new_run_id(), runs.new_run_id()
    assert a != b


def test_filter_by_run_id(tmp_path):
    """Q7: filtering by run_id pulls a whole lifecycle (build+publish) as one group."""
    db = _db(tmp_path)
    runs.record_run(db, stage="build", post_id="p1", status="ok", run_id="r-A")
    runs.record_run(db, stage="publish", post_id="p1", status="ok", run_id="r-A")
    runs.record_run(db, stage="build", post_id="p2", status="ok", run_id="r-B")
    out = runs.list_runs(db, run_id="r-A")
    assert len(out) == 2
    assert {r["stage"] for r in out} == {"build", "publish"}
    assert all(r["run_id"] == "r-A" for r in out)


def test_pipeline_runs_share_one_run_id(tmp_path):
    cfg = {
        "template_path": "./templates/fixed-format.zh.yaml",
        "watermark_config": "./configs/watermark.yaml",
        "download_dir": str(tmp_path / "assets"), "out_dir": str(tmp_path / "out"),
        "state_path": _db(tmp_path), "audit_log": str(tmp_path / "audit.jsonl"),
        "limit": 30,
    }
    items = [
        {"source_id": "ex", "url": f"https://example.com/news/{s}",
         "canonical_url": f"https://example.com/news/{s}", "title": t,
         "image_url": "", "discovered_at": "2026-06-15T02:00:00Z"}
        for s, t in [("a", "標題甲"), ("b", "標題乙")]
    ]
    pipeline.run_pipeline(items, cfg)
    run_ids = {r["run_id"] for r in runs.list_runs(cfg["state_path"])}
    assert len(run_ids) == 1 and None not in run_ids


def test_old_db_migrated_idempotently(tmp_path):
    """A pre-U7 runs table (no run_id/severity) gains the columns on open."""
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "stage TEXT NOT NULL, post_id TEXT, status TEXT NOT NULL, detail TEXT, error TEXT);"
    )
    conn.execute("INSERT INTO runs (ts, stage, status) VALUES ('t','build','ok')")
    conn.commit()
    conn.close()
    # First access migrates; old row reads back with NULL run_id/severity.
    out = runs.list_runs(db)
    assert out[0]["run_id"] is None and out[0]["severity"] is None
    runs.record_run(db, stage="x", status="ok", run_id="r9", severity="info")
    assert runs.list_runs(db, severity="info")[0]["run_id"] == "r9"


def test_fresh_and_migrated_schema_match(tmp_path):
    """Fresh DB (via _SCHEMA) and migrated DB (via ALTER) end with same columns."""
    fresh = _db(tmp_path)
    runs.record_run(fresh, stage="build", status="ok")
    old = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(old)
    conn.executescript(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "stage TEXT NOT NULL, post_id TEXT, status TEXT NOT NULL, detail TEXT, error TEXT);")
    conn.commit()
    conn.close()
    runs.record_run(old, stage="build", status="ok")  # triggers migration

    def cols(path):
        c = sqlite3.connect(path)
        names = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
        c.close()
        return names

    assert cols(fresh) == cols(old)
