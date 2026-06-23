"""Retention/purge maintenance for the durable logs (audit.jsonl + runs table).

``run_retention`` reads the retention windows from the WebUI config and trims
both durable logs to that window by delegating to the existing, already-tested
``audit.purge_before`` / ``runs.purge_before`` helpers. A window of 0 days means
"keep all" (disabled), so the matching purge is skipped entirely.

Trigger wiring (calling run_retention from the auto-pipeline tail) is
intentionally deferred to a follow-up that owns pipeline.py, to keep this lane
collision-free.
"""

from datetime import datetime, timedelta, timezone

from cpost.core import audit, runs


def _cutoff_iso(now_iso: str, days: int) -> str:
    """The ISO 8601 (UTC) instant ``days`` before ``now_iso``.

    Entries with ``ts < cutoff`` are purged, so the cutoff is the oldest
    timestamp kept. ``now_iso`` is parsed leniently (naive input is treated as
    UTC) to match the UTC ISO timestamps the loggers write.
    """
    now = datetime.fromisoformat(now_iso)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - timedelta(days=days)).isoformat()


def run_retention(
    config: dict,
    *,
    audit_log_path: str,
    runs_db_path: str,
    now_iso: str,
) -> dict:
    """Purge audit + runs entries older than their configured retention window.

    Reads ``audit_retention_days`` / ``runs_retention_days`` from ``config``
    (0 = disabled, keep all). For each enabled window it computes the cutoff
    from ``now_iso`` and calls the matching ``purge_before``. Returns a dict of
    removed counts keyed by log name; a disabled window reports 0.
    """
    audit_days = int(config.get("audit_retention_days", 0))
    runs_days = int(config.get("runs_retention_days", 0))

    audit_removed = 0
    if audit_days > 0:
        audit_removed = audit.purge_before(
            audit_log_path, _cutoff_iso(now_iso, audit_days))

    runs_removed = 0
    if runs_days > 0:
        runs_removed = runs.purge_before(
            runs_db_path, _cutoff_iso(now_iso, runs_days))

    return {"audit_removed": audit_removed, "runs_removed": runs_removed}
