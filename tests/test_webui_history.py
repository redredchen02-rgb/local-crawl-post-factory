"""U4: WebUI run-history and audit views."""


from fastapi.testclient import TestClient

from cpost.webui.app import create_app
from cpost.core import webui_config, runs


def _client(tmp_path):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com",
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
    })
    return TestClient(create_app(str(cfgp))), tmp_path


def test_history_lists_runs(tmp_path):
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="publish", post_id="p1",
                    status="ok", detail="https://example.com/p1")
    r = client.get("/history")
    assert r.status_code == 200
    assert "publish" in r.text and "p1" in r.text


def test_history_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/history")
    assert r.status_code == 200
    assert "尚無運行紀錄" in r.text


def test_audit_shows_lines(tmp_path):
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{"ts":"2026-06-15T02:00:00Z","post_id":"p1","stage":"draft-post","status":"ok"}\n',
        encoding="utf-8")
    r = client.get("/audit")
    assert r.status_code == 200
    assert "draft-post" in r.text


def test_audit_skips_broken_lines(tmp_path):
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{ broken\n{"ts":"t","stage":"publish-post","status":"ok","post_id":"p2"}\n',
        encoding="utf-8")
    r = client.get("/audit")
    assert r.status_code == 200
    assert "publish-post" in r.text  # good line shown, broken skipped


def test_audit_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/audit")
    assert r.status_code == 200
    assert "尚無 audit" in r.text


def test_history_hx_request_returns_fragment(tmp_path):
    """Auto-refresh poll (HX-Request) returns the table fragment, not the full page."""
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="publish", post_id="p1", status="ok")
    full = client.get("/history")
    frag = client.get("/history", headers={"HX-Request": "true"})
    assert 'href="/packages"' in full.text
    assert 'href="/packages"' not in frag.text and "publish" in frag.text


def test_audit_hx_request_returns_fragment(tmp_path):
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{"ts":"t","stage":"draft-post","status":"ok","post_id":"p1"}\n', encoding="utf-8")
    frag = client.get("/audit", headers={"HX-Request": "true"})
    assert 'href="/packages"' not in frag.text and "draft-post" in frag.text


def test_history_filter_by_post_id(tmp_path):
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="draft", post_id="abc123", status="ok")
    runs.record_run(str(tp / "state.sqlite"), stage="draft", post_id="xyz999", status="ok")
    r = client.get("/history?post_id=abc123")
    assert r.status_code == 200
    assert "abc123" in r.text
    assert "xyz999" not in r.text


def test_history_filter_by_severity(tmp_path):
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="publish", post_id="p1",
                    status="failed", severity="error", error="boom")
    runs.record_run(str(tp / "state.sqlite"), stage="draft", post_id="p2",
                    status="ok", severity="info")
    r = client.get("/history?severity=error")
    assert r.status_code == 200
    assert "p1" in r.text
    assert "p2" not in r.text


def test_history_filter_combined(tmp_path):
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="verify", post_id="match",
                    status="failed", severity="error")
    runs.record_run(str(tp / "state.sqlite"), stage="verify", post_id="other",
                    status="failed", severity="error")
    r = client.get("/history?post_id=match&severity=error")
    assert "match" in r.text
    assert "other" not in r.text


def test_history_filter_no_match_shows_empty(tmp_path):
    client, tp = _client(tmp_path)
    runs.record_run(str(tp / "state.sqlite"), stage="draft", post_id="abc", status="ok")
    r = client.get("/history?post_id=nope")
    assert r.status_code == 200
    assert "尚無運行紀錄" in r.text


def test_history_filter_by_run_id(tmp_path):
    client, tp = _client(tmp_path)
    from cpost.core import runs as runs_mod
    rid = runs_mod.new_run_id()
    runs.record_run(str(tp / "state.sqlite"), stage="draft", post_id="p1",
                    status="ok", run_id=rid)
    runs.record_run(str(tp / "state.sqlite"), stage="draft", post_id="p2",
                    status="ok", run_id="other-run-id")
    r = client.get(f"/history?run_id={rid}")
    assert "p1" in r.text
    assert "p2" not in r.text


def test_audit_post_id_links_to_detail(tmp_path):
    """audit table post_id cell should render a link to /packages/{post_id}."""
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{"ts":"t","stage":"draft-post","status":"ok","post_id":"20260615_abc"}\n',
        encoding="utf-8")
    r = client.get("/audit")
    assert '/packages/20260615_abc' in r.text


def test_audit_handles_one_line_directly(tmp_path):
    """Direct coverage for _tail_audit for-loop (_helpers.py:131)."""
    from cpost.webui._helpers import _tail_audit
    log = tmp_path / "audit.jsonl"
    log.write_text('{"ts":"t","stage":"s","status":"ok","post_id":"p"}\n', encoding="utf-8")
    rows = _tail_audit(str(log), 200)
    assert len(rows) == 1


def test_audit_tail_large_file_skips_partial_first_line(tmp_path):
    """_tail_audit with >32KB file triggers lines=lines[1:] (_helpers.py:131)."""
    from cpost.webui._helpers import _tail_audit
    log = tmp_path / "audit_fullsize.jsonl"
    big = "\n".join(
        f'{{"ts":"2026-06-{d:02d}T00:00:00Z","stage":"ok","status":"ok","post_id":"p{d}"}}'
        for d in range(1, 4)
    )
    single_len = len(big) + 1
    repeat = (66000 // single_len) + 1
    content = "\n".join([big] * repeat) + "\n"
    assert len(content.encode("utf-8")) > 65536
    log.write_text(content, encoding="utf-8")
    rows = _tail_audit(str(log), limit=9999)
    assert len(rows) > 0
    for r in rows:
        assert "post_id" in r


def test_audit_skips_empty_lines_and_renders_good(tmp_path):
    """_tail_audit skips empty lines and still renders valid entries (_helpers.py:131,136)."""
    client, tp = _client(tmp_path)
    (tp / "audit.jsonl").write_text(
        '{"ts":"t1","stage":"first","status":"ok","post_id":"p1"}\n'
        '\n'  # empty line — should be skipped
        '{"ts":"t2","stage":"second","status":"ok","post_id":"p2"}\n',
        encoding="utf-8")
    r = client.get("/audit")
    assert r.status_code == 200
    assert "first" in r.text
    assert "second" in r.text
