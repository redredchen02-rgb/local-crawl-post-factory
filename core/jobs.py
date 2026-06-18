"""In-memory background job registry for the local WebUI.

Single-user local tool: a thread per job and a dict of snapshots is enough — no
Celery/Redis. Jobs are lost on process restart, which is fine because the actual
post packages are persisted to ``out/`` (the job only tracks progress).
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
import uuid
from typing import Any

_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}
_MAX_JOBS = 100
_JOB_TTL_SEC = 24 * 60 * 60


class Job:
    def __init__(self, job_id: str) -> None:
        now = time.time()
        self.id = job_id
        self.status = "pending"   # pending | running | done | failed
        self.progress: list[str] = []        # list of human-readable step messages
        self.current = ""         # live status line (overwritten, not appended)
        self.result: Any = None
        self.error: str | None = None
        self.created_at = now
        self.updated_at = now
        self.finished_at: float | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": list(self.progress),
            "current": self.current,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }

    def touch(self) -> None:
        self.updated_at = time.time()


def _prune_locked(now: float | None = None) -> None:
    """Drop old finished jobs while preserving active ones."""
    now = time.time() if now is None else now
    expired = [
        job_id for job_id, job in _JOBS.items()
        if job.finished_at is not None and now - job.finished_at > _JOB_TTL_SEC
    ]
    for job_id in expired:
        _JOBS.pop(job_id, None)

    overflow = len(_JOBS) - _MAX_JOBS
    if overflow <= 0:
        return
    finished = sorted(
        (job for job in _JOBS.values() if job.finished_at is not None),
        key=lambda job: job.finished_at or job.updated_at,
    )
    for job in finished[:overflow]:
        _JOBS.pop(job.id, None)


def submit(fn: Callable[[Job], object]) -> str:
    """Run ``fn(job)`` in a daemon thread. ``fn`` receives the Job for progress.

    Returns the job id immediately.
    """
    job_id = uuid.uuid4().hex
    job = Job(job_id)
    with _LOCK:
        _prune_locked()
        _JOBS[job_id] = job

    def _runner() -> None:
        with _LOCK:
            job.status = "running"
            job.touch()
        try:
            result = fn(job)
            with _LOCK:
                job.result = result
                job.status = "done"
                job.finished_at = time.time()
                job.touch()
                _prune_locked()
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            with _LOCK:
                job.error = str(exc)
                job.status = "failed"
                job.finished_at = time.time()
                job.touch()
                _prune_locked()

    threading.Thread(target=_runner, daemon=True).start()
    return job_id


def get(job_id: str) -> dict | None:
    with _LOCK:
        _prune_locked()
        job = _JOBS.get(job_id)
        return job.snapshot() if job else None


def report(job: Job, message: str) -> None:
    """Append a progress message (safe to call from the worker thread)."""
    with _LOCK:
        job.progress.append(message)
        job.touch()


def set_current(job: Job, message: str) -> None:
    """Set the live status line (safe to call from the worker thread).

    Unlike ``report()``, this *overwrites* rather than appends — the front-end
    renders ``current`` as a single, continuously-updating row during crawl.
    """
    with _LOCK:
        job.current = message
        job.touch()
