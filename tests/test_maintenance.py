"""L1: cli.maintenance.run_retention — cutoff computation + purge delegation."""

import json

from cpost.cli import maintenance
from cpost.core import runs

NOW = "2026-06-22T00:00:00+00:00"


def _write_audit(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n",
        encoding="utf-8",
    )


def _seed_run(db, ts, post_id):
    """Insert one run row with a controlled ts (record_run stamps its own)."""
    with runs.open_run_conn(db) as conn:
        conn.execute(
            "INSERT INTO runs (ts, stage, post_id, status) VALUES (?, 'build', ?, 'ok')",
            (ts, post_id),
        )
        conn.commit()


# --- disabled (days = 0 -> keep all) -----------------------------------------

def test_disabled_deletes_nothing(tmp_path):
    log = tmp_path / "audit.jsonl"
    _write_audit(log, [
        {"ts": "2020-01-01T00:00:00+00:00", "post_id": "ancient"},
        {"ts": "2026-06-21T00:00:00+00:00", "post_id": "recent"},
    ])
    db = str(tmp_path / "state.sqlite")
    _seed_run(db, "2020-01-01T00:00:00+00:00", "ancient")
    _seed_run(db, "2026-06-21T00:00:00+00:00", "recent")

    result = maintenance.run_retention(
        {"audit_retention_days": 0, "runs_retention_days": 0},
        audit_log_path=str(log), runs_db_path=db, now_iso=NOW)

    assert result == {"audit_removed": 0, "runs_removed": 0}
    # nothing touched
    assert len([x for x in log.read_text().splitlines() if x.strip()]) == 2
    assert len(runs.list_runs(db)) == 2


def test_missing_keys_default_to_disabled(tmp_path):
    log = tmp_path / "audit.jsonl"
    _write_audit(log, [{"ts": "2020-01-01T00:00:00+00:00", "post_id": "ancient"}])
    db = str(tmp_path / "state.sqlite")
    _seed_run(db, "2020-01-01T00:00:00+00:00", "ancient")

    result = maintenance.run_retention(
        {}, audit_log_path=str(log), runs_db_path=db, now_iso=NOW)

    assert result == {"audit_removed": 0, "runs_removed": 0}


# --- enabled (delete older than cutoff, keep newer) --------------------------

def test_enabled_purges_audit_older_than_cutoff(tmp_path):
    log = tmp_path / "audit.jsonl"
    # cutoff = NOW - 10 days = 2026-06-12; older purged, newer kept.
    _write_audit(log, [
        {"ts": "2026-06-01T00:00:00+00:00", "post_id": "old"},
        {"ts": "2026-06-20T00:00:00+00:00", "post_id": "fresh"},
    ])
    db = str(tmp_path / "state.sqlite")  # runs disabled

    result = maintenance.run_retention(
        {"audit_retention_days": 10, "runs_retention_days": 0},
        audit_log_path=str(log), runs_db_path=db, now_iso=NOW)

    assert result["audit_removed"] == 1
    assert result["runs_removed"] == 0
    kept = [json.loads(x)["post_id"] for x in log.read_text().splitlines() if x.strip()]
    assert kept == ["fresh"]


def test_enabled_purges_runs_older_than_cutoff(tmp_path):
    db = str(tmp_path / "state.sqlite")
    _seed_run(db, "2026-06-01T00:00:00+00:00", "old")
    _seed_run(db, "2026-06-20T00:00:00+00:00", "fresh")
    log = tmp_path / "audit.jsonl"  # audit disabled (missing file is fine)

    result = maintenance.run_retention(
        {"audit_retention_days": 0, "runs_retention_days": 7},
        audit_log_path=str(log), runs_db_path=db, now_iso=NOW)

    assert result["runs_removed"] == 1
    assert result["audit_removed"] == 0
    remaining = [r["post_id"] for r in runs.list_runs(db)]
    assert remaining == ["fresh"]


def test_both_enabled_returns_counts(tmp_path):
    log = tmp_path / "audit.jsonl"
    _write_audit(log, [
        {"ts": "2026-05-01T00:00:00+00:00", "post_id": "a-old"},
        {"ts": "2026-06-21T00:00:00+00:00", "post_id": "a-fresh"},
    ])
    db = str(tmp_path / "state.sqlite")
    _seed_run(db, "2026-05-01T00:00:00+00:00", "r-old1")
    _seed_run(db, "2026-05-02T00:00:00+00:00", "r-old2")
    _seed_run(db, "2026-06-21T00:00:00+00:00", "r-fresh")

    result = maintenance.run_retention(
        {"audit_retention_days": 14, "runs_retention_days": 14},
        audit_log_path=str(log), runs_db_path=db, now_iso=NOW)

    assert result == {"audit_removed": 1, "runs_removed": 2}


def test_cutoff_boundary_keeps_equal_timestamp(tmp_path):
    """purge_before deletes ts < cutoff; an entry exactly at cutoff is kept."""
    db = str(tmp_path / "state.sqlite")
    # cutoff for 5 days before NOW = 2026-06-17T00:00:00+00:00 (exact match kept)
    _seed_run(db, "2026-06-17T00:00:00+00:00", "boundary")
    _seed_run(db, "2026-06-16T23:59:59+00:00", "just-older")
    log = tmp_path / "audit.jsonl"

    result = maintenance.run_retention(
        {"runs_retention_days": 5}, audit_log_path=str(log),
        runs_db_path=db, now_iso=NOW)

    assert result["runs_removed"] == 1
    assert [r["post_id"] for r in runs.list_runs(db)] == ["boundary"]


def test_naive_now_iso_treated_as_utc(tmp_path):
    """A naive now_iso (no tz) is treated as UTC so the cutoff still matches the
    UTC ISO timestamps the loggers write."""
    db = str(tmp_path / "state.sqlite")
    _seed_run(db, "2026-06-01T00:00:00+00:00", "old")
    _seed_run(db, "2026-06-20T00:00:00+00:00", "fresh")
    log = tmp_path / "audit.jsonl"

    result = maintenance.run_retention(
        {"runs_retention_days": 10}, audit_log_path=str(log),
        runs_db_path=db, now_iso="2026-06-22T00:00:00")

    assert result["runs_removed"] == 1
    assert [r["post_id"] for r in runs.list_runs(db)] == ["fresh"]
