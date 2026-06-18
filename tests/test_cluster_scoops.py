import io
import json
from types import SimpleNamespace

from core import cli, library
from src.cluster_scoops import _run, cluster_library


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def _seed(db, items):
    with library.connect(db) as conn:
        for it in items:
            library.upsert(conn, now="2026-06-18T00:00:00+00:00", **it)


def _three_source_event():
    return [
        {"canonical_url": "https://a.com/1", "title": "藝人A被爆隱婚生子", "source_id": "site-a"},
        {"canonical_url": "https://b.com/9", "title": "藝人A被爆隱婚生子內幕", "source_id": "site-b"},
        {"canonical_url": "https://c.com/7", "title": "獨家：藝人A隱婚生子", "source_id": "site-c"},
    ]


_CFG = {"ngram": 2, "similarity_threshold": 0.3, "time_window_hours": 72}


def test_cluster_library_assigns_and_keeps_all_rows(tmp_path):
    db = _db(tmp_path)
    _seed(db, _three_source_event())
    with library.connect(db) as conn:
        clusters = cluster_library(conn, _CFG, "2026-06-18T00:00:00+00:00")
    cid = clusters[0]["cluster_id"]
    with library.connect(db) as conn:
        assert all(r["cluster_id"] == cid for r in library.list_items(conn))
        assert library.list_clusters(conn)[0]["source_count"] == 3
        assert library.count(conn) == 3  # view layer: no library rows dropped


def test_cluster_library_idempotent_rerun(tmp_path):
    db = _db(tmp_path)
    _seed(db, _three_source_event())
    with library.connect(db) as conn:
        first = cluster_library(conn, _CFG, "2026-06-18T00:00:00+00:00")
    with library.connect(db) as conn:
        second = cluster_library(conn, _CFG, "2026-06-19T00:00:00+00:00")
        assert len(library.list_clusters(conn)) == 1  # no duplicate cluster rows
        assert library.count(conn) == 3
    assert first[0]["cluster_id"] == second[0]["cluster_id"]  # stable id


def test_get_cluster_members_returns_full_items(tmp_path):
    db = _db(tmp_path)
    _seed(db, _three_source_event())
    with library.connect(db) as conn:
        clusters = cluster_library(conn, _CFG, "2026-06-18T00:00:00+00:00")
        members = library.get_cluster_members(conn, clusters[0]["cluster_id"])
    assert {m["source_id"] for m in members} == {"site-a", "site-b", "site-c"}


def _run_command(db, monkeypatch, config=None):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = cli.run(lambda: _run(SimpleNamespace(state=db, config=config)))
    return code, out.getvalue(), err.getvalue()


def test_cli_contract_emits_summary(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _seed(db, _three_source_event())
    code, out, err = _run_command(db, monkeypatch)
    assert code == 0
    assert err == ""
    payload = json.loads(out.strip())
    assert payload["items"] == 3
    assert "clusters" in payload and "by_cluster" in payload


def test_cli_empty_library_ok(tmp_path, monkeypatch):
    db = _db(tmp_path)
    code, out, err = _run_command(db, monkeypatch)
    assert code == 0
    assert json.loads(out.strip()) == {"clusters": 0, "items": 0, "by_cluster": []}
