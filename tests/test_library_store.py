from cpost.core import library


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def _item(**overrides):
    item = {
        "canonical_url": "https://a.com/1",
        "title": "Scoop One",
        "now": "2026-06-18T00:00:00+00:00",
        "source_id": "site-a",
        "url": "https://a.com/1",
        "source_text": "full body text",
        "description": "desc",
        "published_at": "2026-06-17T00:00:00+00:00",
        "discovered_at": "2026-06-18T00:00:00+00:00",
    }
    item.update(overrides)
    return item


def test_upsert_then_get_roundtrip(tmp_path):
    db = _db(tmp_path)
    with library.connect(db) as conn:
        library.upsert(conn, **_item())
    with library.connect(db) as conn:
        row = library.get(conn, "https://a.com/1")
    assert row is not None
    assert row["title"] == "Scoop One"
    assert row["source_text"] == "full body text"
    assert row["source_id"] == "site-a"
    assert row["cluster_id"] is None
    assert row["ingested_at"] == "2026-06-18T00:00:00+00:00"


def test_reingest_same_url_updates_not_appends_and_preserves_ingested_at(tmp_path):
    db = _db(tmp_path)
    with library.connect(db) as conn:
        library.upsert(conn, **_item(now="2026-06-18T00:00:00+00:00", title="Old"))
    with library.connect(db) as conn:
        library.upsert(conn, **_item(now="2026-06-19T09:09:09+00:00", title="New"))
    with library.connect(db) as conn:
        assert library.count(conn) == 1  # updated, not appended
        row = library.get(conn, "https://a.com/1")
        assert row["title"] == "New"  # content refreshed
        assert row["ingested_at"] == "2026-06-18T00:00:00+00:00"  # first-seen preserved


def test_reingest_does_not_clobber_cluster_id(tmp_path):
    """cluster_id (assigned later by clustering, plan U3) survives a content re-ingest."""
    db = _db(tmp_path)
    with library.connect(db) as conn:
        library.upsert(conn, **_item())
        conn.execute("UPDATE library_items SET cluster_id = ? WHERE canonical_url = ?",
                     ("c1", "https://a.com/1"))
    with library.connect(db) as conn:
        library.upsert(conn, **_item(title="Edited"))
    with library.connect(db) as conn:
        assert library.get(conn, "https://a.com/1")["cluster_id"] == "c1"


def test_empty_optional_fields_ok(tmp_path):
    db = _db(tmp_path)
    with library.connect(db) as conn:
        library.upsert(conn, canonical_url="https://a.com/2", title="T",
                       now="2026-06-18T00:00:00+00:00")
    with library.connect(db) as conn:
        row = library.get(conn, "https://a.com/2")
        assert row["source_text"] is None
        assert row["source_id"] is None


def test_long_source_text_roundtrip(tmp_path):
    db = _db(tmp_path)
    big = "x" * 200_000
    with library.connect(db) as conn:
        library.upsert(conn, **_item(canonical_url="https://a.com/3", source_text=big))
    with library.connect(db) as conn:
        assert library.get(conn, "https://a.com/3")["source_text"] == big


def test_get_missing_returns_none(tmp_path):
    with library.connect(_db(tmp_path)) as conn:
        assert library.get(conn, "https://nope.com/x") is None


def test_list_and_count_multisource(tmp_path):
    db = _db(tmp_path)
    with library.connect(db) as conn:
        library.upsert(conn, **_item(canonical_url="https://a.com/1", source_id="site-a",
                                     now="2026-06-18T00:00:01+00:00"))
        library.upsert(conn, **_item(canonical_url="https://b.com/1", source_id="site-b",
                                     now="2026-06-18T00:00:02+00:00"))
    with library.connect(db) as conn:
        assert library.count(conn) == 2
        rows = library.list_items(conn)
        assert {r["source_id"] for r in rows} == {"site-a", "site-b"}
        assert rows[0]["canonical_url"] == "https://b.com/1"  # newest ingested first


def test_schema_has_expected_columns_and_reopen_idempotent(tmp_path):
    db = _db(tmp_path)
    expected = {"canonical_url", "source_id", "url", "title", "source_text",
                "description", "published_at", "discovered_at", "ingested_at",
                "cluster_id"}
    with library.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(library_items)").fetchall()}
        assert expected <= cols
    with library.connect(db) as conn:
        library.upsert(conn, **_item())
    with library.connect(db) as conn:
        cols2 = {r[1] for r in conn.execute("PRAGMA table_info(library_items)").fetchall()}
        assert cols2 == cols  # reopen does not alter schema
        assert library.count(conn) == 1  # data preserved


def test_list_items_limit(tmp_path):
    """list_items with a limit returns at most N rows (library.py:134-135)."""
    db = _db(tmp_path)
    with library.connect(db) as conn:
        for i in range(5):
            library.upsert(conn, **_item(canonical_url=f"https://a.com/{i}",
                                          now=f"2026-06-18T00:00:0{i}+00:00"))
    with library.connect(db) as conn:
        result = library.list_items(conn, limit=2)
        assert len(result) == 2


def test_list_clusters_limit(tmp_path):
    """list_clusters with by_score and limit returns at most N rows (library.py:190-191)."""
    db = _db(tmp_path)
    with library.connect(db) as conn:
        library.assign_clusters(conn, [
            {"cluster_id": "c1", "members": ["https://a.com/1"], "member_count": 1,
             "source_count": 1},
            {"cluster_id": "c2", "members": ["https://a.com/2"], "member_count": 1,
             "source_count": 1},
            {"cluster_id": "c3", "members": ["https://a.com/3"], "member_count": 1,
             "source_count": 1},
        ], now="2026-06-18T00:00:00")
    with library.connect(db) as conn:
        result = library.list_clusters(conn, by_score=True, limit=2)
        assert len(result) == 2
