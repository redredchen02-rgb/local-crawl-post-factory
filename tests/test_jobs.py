import time

from core import jobs


def _wait(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = jobs.get(job_id)
        if snap and snap["status"] in ("done", "failed"):
            return snap
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_submit_runs_and_completes():
    jid = jobs.submit(lambda job: {"answer": 42})
    snap = _wait(jid)
    assert snap["status"] == "done"
    assert snap["result"] == {"answer": 42}
    assert snap["created_at"] <= snap["updated_at"]
    assert snap["finished_at"] is not None


def test_failure_is_captured():
    def boom(job):
        raise RuntimeError("kaboom")

    jid = jobs.submit(boom)
    snap = _wait(jid)
    assert snap["status"] == "failed"
    assert "kaboom" in snap["error"]


def test_unknown_id_returns_none():
    assert jobs.get("does-not-exist") is None


def test_progress_reporting():
    def work(job):
        jobs.report(job, "step 1")
        jobs.report(job, "step 2")
        return "ok"

    jid = jobs.submit(work)
    snap = _wait(jid)
    assert snap["progress"] == ["step 1", "step 2"]


def test_two_jobs_do_not_mix():
    a = jobs.submit(lambda job: "A")
    b = jobs.submit(lambda job: "B")
    assert _wait(a)["result"] == "A"
    assert _wait(b)["result"] == "B"


# -- Unit 1: live status line ------------------------------------------------ #

def test_current_defaults_to_empty():
    """New job has an empty current string."""
    jid = jobs.submit(lambda job: None)
    snap = _wait(jid)
    assert snap["current"] == ""


def test_set_current_overwrites():
    """set_current replaces the previous value (does NOT append)."""
    calls = []

    def work(job):
        jobs.set_current(job, "first")
        jobs.set_current(job, "second")
        calls.append(jobs.get(job.id)["current"])
        return "ok"

    jid = jobs.submit(work)
    snap = _wait(jid)
    assert snap["current"] == "second"
    assert calls == ["second"]
    assert snap["updated_at"] >= snap["created_at"]


def test_current_does_not_affect_progress():
    """set_current is orthogonal to report(); progress remains append-only."""

    def work(job):
        jobs.report(job, "step 1")
        jobs.set_current(job, "crawling...")
        jobs.report(job, "step 2")
        return "ok"

    jid = jobs.submit(work)
    snap = _wait(jid)
    assert snap["progress"] == ["step 1", "step 2"]
    assert snap["current"] == "crawling..."


def test_two_jobs_current_independent():
    """Each job keeps its own current value."""

    def work_a(job):
        jobs.set_current(job, "status A")
        return "A"

    def work_b(job):
        jobs.set_current(job, "status B")
        return "B"

    a_id = jobs.submit(work_a)
    b_id = jobs.submit(work_b)
    assert _wait(a_id)["current"] == "status A"
    assert _wait(b_id)["current"] == "status B"


def test_finished_jobs_pruned_after_ttl(monkeypatch):
    """Finished jobs older than the TTL disappear from snapshots."""
    jid = jobs.submit(lambda job: "ok")
    snap = _wait(jid)
    assert snap["status"] == "done"

    with jobs._LOCK:
        jobs._JOBS[jid].finished_at = 1.0

    monkeypatch.setattr(jobs, "_JOB_TTL_SEC", 10)
    monkeypatch.setattr(jobs.time, "time", lambda: 100.0)
    assert jobs.get(jid) is None


def test_pruning_preserves_running_jobs(monkeypatch):
    """Overflow pruning removes finished jobs before active jobs."""
    with jobs._LOCK:
        jobs._JOBS.clear()

    blocked = []

    def work(job):
        blocked.append(job.id)
        while blocked:
            time.sleep(0.01)

    running_id = jobs.submit(work)
    while not blocked:
        time.sleep(0.01)
    done_id = jobs.submit(lambda job: "done")
    _wait(done_id)

    monkeypatch.setattr(jobs, "_MAX_JOBS", 1)
    jobs.get(running_id)

    assert jobs.get(running_id) is not None
    assert jobs.get(done_id) is None
    blocked.clear()
