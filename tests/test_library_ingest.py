import io
import json
from types import SimpleNamespace

import pytest

from cpost.core import cli, library, pipeline
from cpost.core.errors import ValidationError
from cpost.core.io_ndjson import read_lines
from cpost.cli.library_ingest import _run, ingest, to_library_fields


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def _rec(**overrides):
    rec = {
        "source_id": "site-a",
        "url": "https://a.com/1",
        "canonical_url": "https://a.com/1",
        "title": "Scoop One",
        "discovered_at": "2026-06-18T00:00:00Z",
        "text": "full body",
        "description": "desc",
    }
    rec.update(overrides)
    return rec


# --- pure mapping ---

def test_to_library_fields_maps_text_to_source_text():
    fields = to_library_fields(_rec(text="the body"))
    assert fields["source_text"] == "the body"
    assert fields["canonical_url"] == "https://a.com/1"
    assert fields["title"] == "Scoop One"


def test_to_library_fields_missing_canonical_raises():
    rec = _rec()
    del rec["canonical_url"]
    with pytest.raises(ValidationError):
        to_library_fields(rec)


def test_to_library_fields_missing_title_raises():
    with pytest.raises(ValidationError):
        to_library_fields(_rec(title=""))


def test_to_library_fields_always_yields_nonempty_source_id():
    # source_id is provenance (display/filter only) but must never be blank in
    # the library; a valid record maps through with its source_id intact (U9 R4).
    fields = to_library_fields(_rec(source_id="site-x"))
    assert fields["source_id"] == "site-x"


def test_to_library_fields_blank_source_id_raises():
    with pytest.raises(ValidationError):
        to_library_fields(_rec(source_id=""))
    with pytest.raises(ValidationError):
        to_library_fields(_rec(source_id="   "))


def test_to_library_fields_missing_source_id_raises():
    rec = _rec()
    del rec["source_id"]
    with pytest.raises(ValidationError):
        to_library_fields(rec)


def test_ingest_persists_nonempty_source_id(tmp_path):
    # End-to-end through the stage: every persisted row carries a non-empty
    # source_id (the provenance-completeness guarantee, R4).
    db = _db(tmp_path)
    recs = [_rec(canonical_url="https://a.com/1", source_id="site-a"),
            _rec(canonical_url="https://b.com/1", source_id="site-b")]
    with library.connect(db) as conn:
        list(ingest(iter(recs), conn, "2026-06-18T00:00:00+00:00"))
    with library.connect(db) as conn:
        for url in ("https://a.com/1", "https://b.com/1"):
            sid = library.get(conn, url)["source_id"]
            assert sid and sid.strip()


# --- stage behaviour ---

def test_ingest_persists_and_passes_through(tmp_path):
    db = _db(tmp_path)
    recs = [_rec(canonical_url="https://a.com/1"), _rec(canonical_url="https://a.com/2")]
    with library.connect(db) as conn:
        out = list(ingest(iter(recs), conn, "2026-06-18T00:00:00+00:00"))
    assert out == recs  # transparent: records yielded unchanged
    with library.connect(db) as conn:
        assert library.count(conn) == 2
        assert library.get(conn, "https://a.com/1")["source_text"] == "full body"


def test_ingest_multisource_preserves_source_id(tmp_path):
    db = _db(tmp_path)
    recs = [_rec(canonical_url="https://a.com/1", source_id="site-a"),
            _rec(canonical_url="https://b.com/1", source_id="site-b")]
    with library.connect(db) as conn:
        list(ingest(iter(recs), conn, "2026-06-18T00:00:00+00:00"))
    with library.connect(db) as conn:
        assert library.get(conn, "https://a.com/1")["source_id"] == "site-a"
        assert library.get(conn, "https://b.com/1")["source_id"] == "site-b"


# --- CLI contract ---

def _run_command(stdin_text, db, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    args = SimpleNamespace(state=db)
    code = cli.run(lambda: _run(args))
    return code, out.getvalue(), err.getvalue()


def test_contract_happy(tmp_path, monkeypatch):
    db = _db(tmp_path)
    code, out, err = _run_command(json.dumps(_rec()) + "\n", db, monkeypatch)
    assert code == 0
    assert err == ""
    assert json.loads(out.strip())["canonical_url"] == "https://a.com/1"
    with library.connect(db) as conn:
        assert library.count(conn) == 1


def test_contract_empty_stdin_is_ok(tmp_path, monkeypatch):
    db = _db(tmp_path)
    code, out, err = _run_command("", db, monkeypatch)
    assert code == 0
    assert out == ""
    with library.connect(db) as conn:
        assert library.count(conn) == 0


def test_contract_malformed_line_exits_2(tmp_path, monkeypatch):
    db = _db(tmp_path)
    code, out, err = _run_command("{not json}\n", db, monkeypatch)
    assert code == 2
    assert out == ""
    assert err.strip() != ""


def test_contract_missing_canonical_exits_2(tmp_path, monkeypatch):
    db = _db(tmp_path)
    rec = _rec()
    del rec["canonical_url"]
    code, out, err = _run_command(json.dumps(rec) + "\n", db, monkeypatch)
    assert code == 2
    assert out == ""


def test_contract_partial_failure_emits_nothing_and_commits_nothing(tmp_path, monkeypatch):
    # [valid, valid, missing-source_id]: the third record rolls the whole
    # transaction back (db.connect commits only after the loop completes), so
    # the two earlier valid records must NOT reach stdout -- otherwise stdout
    # would advertise records the library never persisted (U8).
    db = _db(tmp_path)
    bad = _rec(canonical_url="https://a.com/3")
    del bad["source_id"]
    stdin_text = (
        json.dumps(_rec(canonical_url="https://a.com/1")) + "\n"
        + json.dumps(_rec(canonical_url="https://a.com/2")) + "\n"
        + json.dumps(bad) + "\n"
    )
    code, out, err = _run_command(stdin_text, db, monkeypatch)
    assert code == 2
    assert out == ""                 # nothing emitted on failure
    assert err.strip() != ""
    with library.connect(db) as conn:
        assert library.count(conn) == 0   # transaction rolled back: DB empty


def test_contract_partial_failure_malformed_tail_emits_nothing(tmp_path, monkeypatch):
    # Same invariant when the failing line is malformed JSON rather than an
    # invalid record: already-passed valid records must not leak to stdout.
    db = _db(tmp_path)
    stdin_text = (
        json.dumps(_rec(canonical_url="https://a.com/1")) + "\n"
        + json.dumps(_rec(canonical_url="https://a.com/2")) + "\n"
        + "{not json}\n"
    )
    code, out, err = _run_command(stdin_text, db, monkeypatch)
    assert code == 2
    assert out == ""
    with library.connect(db) as conn:
        assert library.count(conn) == 0


def test_contract_all_valid_emits_all_and_commits_all(tmp_path, monkeypatch):
    # Happy path with multiple records: every record is emitted to stdout AND
    # committed to the library (emitted stream agrees with committed DB state).
    db = _db(tmp_path)
    stdin_text = (
        json.dumps(_rec(canonical_url="https://a.com/1")) + "\n"
        + json.dumps(_rec(canonical_url="https://a.com/2")) + "\n"
    )
    code, out, err = _run_command(stdin_text, db, monkeypatch)
    assert code == 0
    assert err == ""
    emitted = [json.loads(line)["canonical_url"] for line in out.splitlines() if line.strip()]
    assert emitted == ["https://a.com/1", "https://a.com/2"]
    with library.connect(db) as conn:
        assert library.count(conn) == 2
        for url in ("https://a.com/1", "https://a.com/2"):
            assert library.get(conn, url) is not None


def test_integration_downstream_never_sees_unpersisted_records(tmp_path, monkeypatch):
    # Integration: ingest's stdout is the downstream stage's stdin. On a
    # partial failure the buffered records are dropped, so a downstream
    # consumer reading ingest's stdout never receives a record the library
    # rolled back -- the emitted stream and committed DB agree.
    db = _db(tmp_path)
    bad = _rec(canonical_url="https://a.com/3")
    del bad["source_id"]
    stdin_text = (
        json.dumps(_rec(canonical_url="https://a.com/1")) + "\n"
        + json.dumps(bad) + "\n"
    )
    code, out, err = _run_command(stdin_text, db, monkeypatch)
    assert code == 2

    # downstream consumes ingest's stdout verbatim
    downstream_records = list(read_lines(io.StringIO(out)))
    assert downstream_records == []

    # and the library holds exactly what downstream was fed: nothing
    with library.connect(db) as conn:
        committed = {r["canonical_url"] for r in library.list_items(conn)}
    assert committed == {r["canonical_url"] for r in downstream_records}


# --- multi-source crawl orchestration ---

def test_crawl_all_sources_combines(monkeypatch):
    calls = []

    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        calls.append(cfg["source_id"])
        return [{"source_id": cfg["source_id"], "canonical_url": f"https://{cfg['source_id']}/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    reports = []
    cfg = {"sources": [{"source_id": "a", "start_url": "https://a"},
                       {"source_id": "b", "start_url": "https://b"}]}
    items = pipeline.crawl_all_sources(cfg, on_source=lambda sid, r: reports.append((sid, r)))
    assert calls == ["a", "b"]
    assert {i["source_id"] for i in items} == {"a", "b"}
    assert dict(reports) == {"a": 1, "b": 1}  # each source's count via on_source


def test_crawl_all_sources_one_failure_does_not_abort(monkeypatch):
    reports = []

    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        if cfg["source_id"] == "bad":
            raise RuntimeError("boom")
        return [{"source_id": cfg["source_id"], "canonical_url": "https://good/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    cfg = {"sources": [{"source_id": "bad", "start_url": "https://bad"},
                       {"source_id": "good", "start_url": "https://good"}]}
    # Per-source outcomes go to on_source now (NOT progress_cb): the dict-snapshot
    # progress_cb is reserved for realtime crawl telemetry (arch F1).
    items = pipeline.crawl_all_sources(
        cfg, on_source=lambda sid, r: reports.append((sid, r)))
    assert [i["source_id"] for i in items] == ["good"]  # good source still crawled
    by_src = dict(reports)
    assert by_src["good"] == 1                          # success count reported
    assert "boom" in by_src["bad"]                      # failure reported, not raised


def test_crawl_all_sources_falls_back_to_single(monkeypatch):
    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        assert cfg["start_url"] == "https://single"
        return [{"canonical_url": "https://single/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    items = pipeline.crawl_all_sources({"start_url": "https://single"})
    assert len(items) == 1
