import json

from core import audit


def test_record_appends_well_formed_line(tmp_path):
    log = tmp_path / "logs" / "audit.jsonl"
    audit.record(
        str(log),
        post_id="20260615_post",
        stage="package_built",
        status="ok",
        ts="2026-06-15T00:00:00+00:00",
        extra_field="value",
        count=3,
    )

    lines = [x for x in log.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["ts"] == "2026-06-15T00:00:00+00:00"
    assert entry["post_id"] == "20260615_post"
    assert entry["stage"] == "package_built"
    assert entry["status"] == "ok"
    assert entry["extra_field"] == "value"
    assert entry["count"] == 3


def test_record_appends_multiple(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.record(str(log), "p1", "package_built", "ok", "t1")
    audit.record(str(log), "p2", "package_built", "ok", "t2")
    lines = [x for x in log.read_text().splitlines() if x.strip()]
    assert len(lines) == 2
    assert json.loads(lines[1])["post_id"] == "p2"


# --- U7 (R9): severity defaults to info; run_id correlates ------------------

def test_severity_defaults_to_info(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.record(str(log), "p1", "package_built", "ok", "t1")
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["severity"] == "info"
    assert "run_id" not in entry  # omitted when not provided


def test_severity_and_run_id_recorded(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.record(str(log), "p1", "publish", "failed", "t1",
                 severity="error", run_id="r-7")
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["severity"] == "error"
    assert entry["run_id"] == "r-7"
