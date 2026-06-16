"""In-memory background job registry for the local WebUI.

Single-user local tool: a thread per job and a dict of snapshots is enough — no
Celery/Redis. Jobs are lost on process restart, which is fine because the actual
post packages are persisted to ``out/`` (the job only tracks progress).
"""

import threading
import uuid

_LOCK = threading.Lock()
_JOBS = {}


class Job:
    def __init__(self, job_id):
        self.id = job_id
        self.status = "pending"   # pending | running | done | failed
        self.progress = []        # list of human-readable step messages
        self.current = ""         # live status line (overwritten, not appended)
        self.result = None
        self.error = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": list(self.progress),
            "current": self.current,
            "result": self.result,
            "error": self.error,
        }


def submit(fn) -> str:
    """Run ``fn(job)`` in a daemon thread. ``fn`` receives the Job for progress.

    Returns the job id immediately.
    """
    job_id = uuid.uuid4().hex
    job = Job(job_id)
    with _LOCK:
        _JOBS[job_id] = job

    def _runner():
        job.status = "running"
        try:
            job.result = fn(job)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            job.error = str(exc)
            job.status = "failed"

    threading.Thread(target=_runner, daemon=True).start()
    return job_id


def get(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
    return job.snapshot() if job else None


def report(job, message: str) -> None:
    """Append a progress message (safe to call from the worker thread)."""
    job.progress.append(message)


def set_current(job, message: str) -> None:
    """Set the live status line (safe to call from the worker thread).

    Unlike ``report()``, this *overwrites* rather than appends — the front-end
    renders ``current`` as a single, continuously-updating row during crawl.
    """
    job.current = message
