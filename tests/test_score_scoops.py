import io
import json
from types import SimpleNamespace

from cpost.core import cli, library, scoop_pipeline, webui_config
from cpost.cli import generate_article
from cpost.cli.cluster_scoops import cluster_library
from cpost.cli.score_scoops import _run, score_all

NOW = "2026-06-18T00:00:00+00:00"

_CFG = {
    "ngram": 2, "similarity_threshold": 0.3, "time_window_hours": 24 * 365,
    "confidence_source_cap": 3, "quality_full_text_chars": 1000,
    "quality_recency_window_hours": 168, "quality_material_cap": 3,
    "weight_completeness": 0.5, "weight_recency": 0.2, "weight_material": 0.3,
    "weight_confidence": 0.6, "weight_quality": 0.4,
}


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def _seed(db, items):
    with library.connect(db) as conn:
        for it in items:
            library.upsert(conn, now=NOW, **it)


def _setup_two_clusters(db):
    """One scoop corroborated by 3 sources + one lone single-source item."""
    _seed(db, [
        {"canonical_url": "https://a.com/1", "title": "藝人A被爆隱婚生子", "source_id": "site-a",
         "source_text": "長" * 1000, "published_at": NOW},
        {"canonical_url": "https://b.com/9", "title": "藝人A被爆隱婚生子內幕", "source_id": "site-b",
         "source_text": "長" * 1000, "published_at": NOW},
        {"canonical_url": "https://c.com/7", "title": "獨家藝人A隱婚生子", "source_id": "site-c",
         "source_text": "長" * 1000, "published_at": NOW},
        {"canonical_url": "https://d.com/3", "title": "完全不相干的天氣新聞", "source_id": "site-d",
         "source_text": "短", "published_at": "2026-01-01T00:00:00+00:00"},
    ])
    with library.connect(db) as conn:
        cluster_library(conn, _CFG, NOW)


def test_multisource_cluster_scores_higher(tmp_path):
    db = _db(tmp_path)
    _setup_two_clusters(db)
    with library.connect(db) as conn:
        scored = score_all(conn, _CFG, NOW)
    assert scored[0]["source_count"] == 3  # the corroborated scoop ranks first
    assert scored[0]["confidence"] > scored[-1]["confidence"]
    assert scored[0]["score"] > scored[-1]["score"]


def test_scores_persisted_and_ordered(tmp_path):
    db = _db(tmp_path)
    _setup_two_clusters(db)
    with library.connect(db) as conn:
        score_all(conn, _CFG, NOW)
    with library.connect(db) as conn:
        rows = library.list_clusters(conn, by_score=True)
        assert all(r["score"] is not None for r in rows)
        assert rows[0]["source_count"] == 3  # highest score first


def test_same_source_repeats_do_not_inflate_confidence(tmp_path):
    db = _db(tmp_path)
    _seed(db, [
        {"canonical_url": "https://a.com/1", "title": "同站連發新聞", "source_id": "site-a",
         "source_text": "x" * 1000, "published_at": NOW},
        {"canonical_url": "https://a.com/2", "title": "同站連發新聞續報", "source_id": "site-a",
         "source_text": "x" * 1000, "published_at": NOW},
        {"canonical_url": "https://a.com/3", "title": "同站連發新聞三度", "source_id": "site-a",
         "source_text": "x" * 1000, "published_at": NOW},
    ])
    with library.connect(db) as conn:
        cluster_library(conn, _CFG, NOW)
        scored = score_all(conn, _CFG, NOW)
    singles = [r for r in scored if r["source_count"] == 1]
    assert singles  # repeats from one source collapse to source_count 1
    assert all(r["confidence"] <= 1 / 3 + 1e-9 for r in singles)


def test_idempotent_rescore(tmp_path):
    db = _db(tmp_path)
    _setup_two_clusters(db)
    with library.connect(db) as conn:
        first = score_all(conn, _CFG, NOW)
    with library.connect(db) as conn:
        second = score_all(conn, _CFG, NOW)
    assert ([(r["cluster_id"], r["score"]) for r in first]
            == [(r["cluster_id"], r["score"]) for r in second])


def _run_command(db, monkeypatch, config=None):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = cli.run(lambda: _run(SimpleNamespace(state=db, config=config, format="json")))
    return code, out.getvalue(), err.getvalue()


def test_cli_contract_emits_sorted_summary(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _setup_two_clusters(db)
    code, out, err = _run_command(db, monkeypatch)
    assert code == 0
    assert err == ""
    payload = json.loads(out.strip())
    assert payload["scored"] >= 2
    scores = [c["score"] for c in payload["by_cluster"]]
    assert scores == sorted(scores, reverse=True)


def test_cli_empty_library_ok(tmp_path, monkeypatch):
    db = _db(tmp_path)
    code, out, err = _run_command(db, monkeypatch)
    assert code == 0
    assert json.loads(out.strip()) == {"scored": 0, "by_cluster": []}


def test_generation_result_declared_keys(tmp_path, monkeypatch):
    """R7: run_generation_pipeline returns exactly the GenerationPipelineResult
    keys; each built entry exactly the GenerationBuilt keys."""
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com",
                                  "out_dir": str(tmp_path / "out")})
    cfg = webui_config.load(str(cfgp))
    monkeypatch.setattr(
        generate_article, "generate",
        lambda conn, cid, lc, pr, now, **kw: {
            "title": f"標題{cid}", "caption": f"正文{cid}", "text": f"正文{cid}",
            "canonical_url": f"https://scoop.cpost.local/{cid}", "source_id": "scoop",
            "url": "https://rep.example.com/x",
            "published_at": "2026-06-15T10:00:00+08:00", "discovered_at": now})
    result = scoop_pipeline.run_generation_pipeline(["c1"], cfg)
    assert set(result) == {"built", "failed", "kind"}
    assert result["built"]
    for entry in result["built"]:
        assert set(entry) == {"post_id", "title"}
