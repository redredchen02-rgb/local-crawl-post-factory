"""Tests for dedupe-posts (origin §14.5, R9/R10)."""

import pytest

from cpost.core import state
from cpost.core.url_utils import title_hash
from cpost.cli.dedupe_posts import _dedupe


def _seed(db_path, *, canonical_url, title, status):
    with state.connect(str(db_path)) as conn:
        state.upsert(
            conn,
            canonical_url=canonical_url,
            title=title,
            title_hash=title_hash(title),
            status=status,
            now="2026-06-15T00:00:00Z",
        )


def _run_dedupe(db_path, records):
    with state.connect(str(db_path)) as conn:
        return list(_dedupe(records, conn))


def test_empty_state_passes_all(tmp_path):
    # First-release always-pass case: empty state -> nothing dropped.
    db = tmp_path / "state.db"
    records = [
        {"canonical_url": "https://x.test/a", "title": "A"},
        {"canonical_url": "https://x.test/b", "title": "B"},
    ]
    assert _run_dedupe(db, records) == records


def test_same_canonical_url_published_skipped(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, canonical_url="https://x.test/a", title="A", status="published")
    records = [
        {"canonical_url": "https://x.test/a", "title": "A"},
        {"canonical_url": "https://x.test/b", "title": "B"},
    ]
    out = _run_dedupe(db, records)
    assert out == [{"canonical_url": "https://x.test/b", "title": "B"}]


def test_same_title_different_url_emitted(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, canonical_url="https://x.test/old", title="Same Title", status="published")
    # Q6: dedup is URL-only. Different url + same title -> a different article -> emitted.
    records = [{"canonical_url": "https://x.test/new", "title": "Same Title"}]
    assert _run_dedupe(db, records) == records


def test_new_canonical_url_emitted(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, canonical_url="https://x.test/a", title="A", status="published")
    records = [{"canonical_url": "https://x.test/fresh", "title": "Fresh"}]
    assert _run_dedupe(db, records) == records


def test_package_built_not_treated_as_processed(tmp_path):
    # Only published counts (R9): package_built must NOT be dropped.
    db = tmp_path / "state.db"
    _seed(db, canonical_url="https://x.test/a", title="A", status="package_built")
    records = [{"canonical_url": "https://x.test/a", "title": "A"}]
    assert _run_dedupe(db, records) == records


def test_missing_canonical_url_raises(tmp_path):
    db = tmp_path / "state.db"
    with pytest.raises(Exception):
        _run_dedupe(db, [{"title": "A"}])


def test_missing_title_raises(tmp_path):
    db = tmp_path / "state.db"
    with pytest.raises(Exception):
        _run_dedupe(db, [{"canonical_url": "https://x.test/a"}])


# --- U4 (R5): skip reasons are reported, dedupe stays read-only --------------

def test_skip_reason_url_or_none(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, canonical_url="https://x.test/a", title="A", status="published")
    with state.connect(str(db)) as conn:
        assert state.skip_reason(conn, "https://x.test/a", title_hash("A")) == "url"
        # Q6: a title-only match no longer skips.
        assert state.skip_reason(conn, "https://x.test/z", title_hash("A")) is None
        assert state.skip_reason(conn, "https://x.test/z", title_hash("Z")) is None


def test_on_skip_callback_reports_reason(tmp_path):
    db = tmp_path / "state.db"
    _seed(db, canonical_url="https://x.test/a", title="A", status="published")
    _seed(db, canonical_url="https://x.test/old", title="Dup", status="published")
    records = [
        {"canonical_url": "https://x.test/a", "title": "A"},        # url match -> skip
        {"canonical_url": "https://x.test/new", "title": "Dup"},    # Q6: title-only -> emitted
        {"canonical_url": "https://x.test/fresh", "title": "New"},  # passes
    ]
    seen = []
    with state.connect(str(db)) as conn:
        out = list(_dedupe(records, conn, on_skip=lambda r, reason: seen.append(reason)))
    assert out == [
        {"canonical_url": "https://x.test/new", "title": "Dup"},
        {"canonical_url": "https://x.test/fresh", "title": "New"},
    ]
    assert seen == ["url"]
