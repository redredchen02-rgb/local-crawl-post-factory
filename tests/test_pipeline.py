"""core/pipeline orchestrator: in-process build without shell/network."""

from core import pipeline, state, url_utils


def _cfg(tmp_path):
    return {
        "template_path": "./templates/fixed-format.zh.yaml",
        "watermark_config": "./configs/watermark.yaml",
        "download_dir": str(tmp_path / "assets"),
        "out_dir": str(tmp_path / "out"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
        "limit": 30,
    }


def _item(slug, title):
    return {
        "source_id": "example.com",
        "url": f"https://example.com/news/{slug}",
        "canonical_url": f"https://example.com/news/{slug}",
        "title": title,
        "description": "desc",
        "image_url": "",  # text-only path, no network
        "published_at": "2026-06-15T10:00:00+08:00",
        "discovered_at": "2026-06-15T02:00:00Z",
    }


def test_builds_packages_in_process(tmp_path):
    cfg = _cfg(tmp_path)
    result = pipeline.run_pipeline([_item("a", "標題一"), _item("b", "標題二")], cfg)
    assert len(result["built"]) == 2
    assert result["failed"] == []
    for b in result["built"]:
        assert (tmp_path / "out" / b["post_id"] / "manifest.json").exists()


def test_dedupe_skips_published(tmp_path):
    cfg = _cfg(tmp_path)
    # pre-publish item 'a'
    with state.connect(cfg["state_path"]) as conn:
        state.upsert(conn, canonical_url="https://example.com/news/a", title="標題一",
                     title_hash=url_utils.title_hash("標題一"), status="published",
                     now="2026-06-15T00:00:00Z")
    result = pipeline.run_pipeline([_item("a", "標題一"), _item("b", "標題二")], cfg)
    assert result["skipped"] == 1
    assert len(result["built"]) == 1
    assert result["built"][0]["post_id"].endswith("news_b")


def test_bad_item_fails_without_aborting_batch(tmp_path):
    cfg = _cfg(tmp_path)
    bad = _item("c", "")  # empty title -> normalize fails
    result = pipeline.run_pipeline([bad, _item("d", "好標題")], cfg)
    assert len(result["built"]) == 1
    assert len(result["failed"]) == 1


def test_empty_items(tmp_path):
    result = pipeline.run_pipeline([], _cfg(tmp_path))
    assert result["built"] == [] and result["failed"] == []
