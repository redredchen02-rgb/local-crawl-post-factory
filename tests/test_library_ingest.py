import io
import json
from types import SimpleNamespace

import pytest

from core import cli, library, pipeline
from core.errors import ValidationError
from src.library_ingest import _run, ingest, to_library_fields


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
