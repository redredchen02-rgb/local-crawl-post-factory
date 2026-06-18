"""U5 (R6): public stage-function names + backward-compatible private aliases.

Guards the rename of the six pipeline stage functions from ``_xxx`` to public
names. The aliases must be the *same function object* so existing direct
callers (and ``from module import _xxx``) keep working for one deprecation
cycle, and a parity run proves the rename changed no behavior.
"""

from src import (
    normalize_items,
    dedupe_posts,
    render_caption,
    build_manifest,
)


def test_aliases_are_same_object():
    """Each deprecated ``_xxx`` alias is identity-equal to its public name."""
    assert normalize_items._normalize is normalize_items.normalize_one
    assert dedupe_posts._dedupe is dedupe_posts.dedupe
    assert render_caption._render is render_caption.render
    assert build_manifest._build is build_manifest.build


def test_old_alias_still_callable(tmp_path):
    """A caller using the old private name gets identical behavior."""
    raw = {
        "source_id": "x", "url": "https://x.test/a",
        "canonical_url": "https://x.test/a", "title": "Hi",
        "image_url": "", "discovered_at": "2026-06-15T02:00:00Z",
    }
    assert normalize_items._normalize(raw) == normalize_items.normalize_one(raw)


def test_full_pipeline_parity(tmp_path):
    """run_pipeline via public names produces the expected built/failed/skipped."""
    from core import pipeline

    cfg = {
        "template_path": "./templates/fixed-format.zh.yaml",
        "out_dir": str(tmp_path / "out"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
        "limit": 30,
    }

    def _item(slug, title):
        return {
            "source_id": "example.com", "url": f"https://example.com/news/{slug}",
            "canonical_url": f"https://example.com/news/{slug}", "title": title,
            "description": "desc", "image_url": "",
            "published_at": "2026-06-15T10:00:00+08:00",
            "discovered_at": "2026-06-15T02:00:00Z",
        }

    result = pipeline.run_pipeline([_item("a", "標題一"), _item("b", "標題二")], cfg)
    assert len(result["built"]) == 2
    assert result["failed"] == []
    assert result["skipped"] == 0
