import json

import pytest

from cpost.core import audit


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


# --- U4 (R13): atomic purge_before --------------------------------------------

def _write_log(path, entries):
    path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n",
        encoding="utf-8",
    )


def test_purge_before_removes_old_keeps_recent(tmp_path):
    log = tmp_path / "audit.jsonl"
    _write_log(log, [
        {"ts": "2026-06-01T00:00:00+00:00", "post_id": "old"},
        {"ts": "2026-06-20T00:00:00+00:00", "post_id": "recent"},
    ])
    removed = audit.purge_before(str(log), "2026-06-10T00:00:00+00:00")
    assert removed == 1
    lines = [x for x in log.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["post_id"] == "recent"


def test_purge_before_missing_file_is_noop(tmp_path):
    log = tmp_path / "missing.jsonl"
    assert audit.purge_before(str(log), "2026-06-10T00:00:00+00:00") == 0
    assert not log.exists()


def test_purge_before_keeps_unparseable_lines(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(
        json.dumps({"ts": "2026-06-01T00:00:00+00:00"}) + "\n"
        + "not-json-garbage\n",
        encoding="utf-8",
    )
    removed = audit.purge_before(str(log), "2026-06-10T00:00:00+00:00")
    assert removed == 1
    lines = [x for x in log.read_text().splitlines() if x.strip()]
    assert lines == ["not-json-garbage"]


def test_purge_before_mid_write_failure_keeps_original(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    original = (
        json.dumps({"ts": "2026-06-01T00:00:00+00:00", "post_id": "old"}) + "\n"
        + json.dumps({"ts": "2026-06-20T00:00:00+00:00", "post_id": "recent"})
        + "\n"
    )
    log.write_text(original, encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("replace failed")

    monkeypatch.setattr(audit.os, "replace", boom)

    with pytest.raises(OSError):
        audit.purge_before(str(log), "2026-06-10T00:00:00+00:00")

    # Target audit file keeps original content, no half-write.
    assert log.read_text(encoding="utf-8") == original
    # No leftover temp files beside the destination.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != log.name]
    assert leftovers == []
