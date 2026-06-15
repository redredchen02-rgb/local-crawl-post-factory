"""Append-only JSONL audit log (origin spec §8)."""

import json
from pathlib import Path


def record(log_path: str, post_id: str, stage: str, status: str, ts: str, **extra) -> None:
    """Append one audit line to ``log_path``."""
    entry = {"ts": ts, "post_id": post_id, "stage": stage, "status": status}
    entry.update(extra)
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
