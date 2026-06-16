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
