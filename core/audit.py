"""Append-only JSONL audit log (origin spec §8)."""

import json
from pathlib import Path
from typing import Any


def purge_before(log_path: str, cutoff_iso: str) -> int:
    """Remove audit entries older than ``cutoff_iso`` (UTC ISO 8601).

    Rewrites the JSONL file in place. Returns the number of removed lines.
    No-op if the file doesn't exist.
    """
    p = Path(log_path)
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("ts", "") < cutoff_iso:
                removed += 1
                continue
        except json.JSONDecodeError:
            pass  # keep unparseable lines
        kept.append(line)
    p.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def record(log_path: str, post_id: str, stage: str, status: str, ts: str,
           *, severity: str = "info", run_id: str | None = None,
           **extra: Any) -> None:
    """Append one audit line to ``log_path``.

    ``severity`` defaults to "info" (backward compatible); ``run_id`` correlates
    entries across one pipeline/batch run for life-cycle lookups.
    """
    entry = {"ts": ts, "post_id": post_id, "stage": stage, "status": status,
             "severity": severity}
    if run_id is not None:
        entry["run_id"] = run_id
    entry.update(extra)
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
