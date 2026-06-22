"""Regression guard: the scoop pipeline (library/cluster/score) must never touch
the publish-truth tables. library_items/clusters share the state DB with
items/runs/reviewed, so a stray write or migration could corrupt dedupe truth.
"""

from cpost.core import library, runs, state, url_utils
from cpost.cli.cluster_scoops import cluster_library
from cpost.cli.library_ingest import ingest
from cpost.cli.score_scoops import score_all

NOW = "2026-06-18T00:00:00+00:00"
_CFG = {
    "ngram": 2, "similarity_threshold": 0.3, "time_window_hours": 24 * 365,
    "confidence_source_cap": 3, "quality_full_text_chars": 1000,
    "quality_recency_window_hours": 168, "quality_material_cap": 3,
    "weight_completeness": 0.5, "weight_recency": 0.2, "weight_material": 0.3,
    "weight_confidence": 0.6, "weight_quality": 0.4,
}


def _snapshot_items(db):
    with state.connect(db) as conn:
        conn.row_factory = None
        return conn.execute(
            "SELECT canonical_url, title, status FROM items ORDER BY canonical_url"
        ).fetchall()


def test_scoop_pipeline_does_not_touch_items_or_runs(tmp_path):
    db = str(tmp_path / "state.sqlite")

    # Seed publish-truth: a published item + a run-history row.
    with state.connect(db) as conn:
        state.upsert(conn, canonical_url="https://owned.com/post-1", title="Owned Post",
                     title_hash=url_utils.title_hash("Owned Post"),
                     status="published", now=NOW)
    runs.record_run(db, stage="publish", status="ok", post_id="p1")

    items_before = _snapshot_items(db)
    runs_before = runs.list_runs(db)

    # Run the entire scoop pipeline against the SAME shared DB.
    with library.connect(db) as conn:
        list(ingest(iter([
            {"canonical_url": "https://x.com/1", "title": "某藝人爆料", "source_id": "x",
             "text": "body", "published_at": NOW},
            {"canonical_url": "https://y.com/1", "title": "某藝人爆料內幕", "source_id": "y",
             "text": "body", "published_at": NOW},
        ]), conn, NOW))
        cluster_library(conn, _CFG, NOW)
        score_all(conn, _CFG, NOW)

    # Publish-truth and run history are byte-for-byte unchanged.
    assert _snapshot_items(db) == items_before
    assert runs.list_runs(db) == runs_before
    # And the library actually did its job alongside, untouched tables aside.
    with library.connect(db) as conn:
        assert library.count(conn) == 2
        assert len(library.list_clusters(conn)) >= 1
