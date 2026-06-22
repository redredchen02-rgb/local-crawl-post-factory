from cpost.core import state, url_utils


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def test_creates_table_and_no_match_on_empty(tmp_path):
    with state.connect(_db(tmp_path)) as conn:
        assert state.is_processed(conn, "https://x.com/a", url_utils.title_hash("A")) is False


def test_published_row_is_processed(tmp_path):
    db = _db(tmp_path)
    th = url_utils.title_hash("A")
    with state.connect(db) as conn:
        state.upsert(conn, canonical_url="https://x.com/a", title="A", title_hash=th,
                     status="published", now="2026-06-15T00:00:00Z")
    with state.connect(db) as conn:
        assert state.is_processed(conn, "https://x.com/a", th) is True


def test_package_built_is_not_processed(tmp_path):
    """R9: only 'published' counts as processed."""
    db = _db(tmp_path)
    th = url_utils.title_hash("A")
    with state.connect(db) as conn:
        state.upsert(conn, canonical_url="https://x.com/a", title="A", title_hash=th,
                     status="package_built", now="2026-06-15T00:00:00Z")
    with state.connect(db) as conn:
        assert state.is_processed(conn, "https://x.com/a", th) is False


def test_title_collision_not_processed(tmp_path):
    db = _db(tmp_path)
    th = url_utils.title_hash("Same Title")
    with state.connect(db) as conn:
        state.upsert(conn, canonical_url="https://x.com/a", title="Same Title", title_hash=th,
                     status="published", now="2026-06-15T00:00:00Z")
    with state.connect(db) as conn:
        # Q6: dedup is URL-only. Different url + same title_hash -> NOT processed.
        assert state.is_processed(conn, "https://x.com/b", th) is False
