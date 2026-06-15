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
