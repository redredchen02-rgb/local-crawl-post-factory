"""U8 (Q9): persistent reviewed-gate store + content-subtree binding."""

from core import reviewed


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def _manifest(title="T", body="B", url="https://x.test/a", **extra):
    m = {"content": {"title": title, "body": body},
         "source": {"canonical_url": url}}
    m.update(extra)
    return m


def test_content_id_deterministic():
    assert reviewed.content_id(_manifest()) == reviewed.content_id(_manifest())
    assert len(reviewed.content_id(_manifest())) == 64


def test_content_id_changes_with_each_content_field():
    base = reviewed.content_id(_manifest(title="T"))
    assert reviewed.content_id(_manifest(title="T2")) != base
    assert reviewed.content_id(_manifest(body="B2")) != base
    assert reviewed.content_id(_manifest(url="https://x.test/b")) != base


def test_content_id_ignores_audit_and_backend():
    """mtime-trap guard: lifecycle fields must NOT change the content-id, so a
    normal draft/verify/publish re-save does not invalidate a valid review."""
    base = reviewed.content_id(_manifest())
    noisy = reviewed.content_id(_manifest(
        backend={"status": "published", "run_id": "r1"},
        audit={"updated_at": "2026-06-15T09:00:00Z"}))
    assert noisy == base


def test_mark_get_roundtrip(tmp_path):
    db = _db(tmp_path)
    reviewed.mark(db, "p1", "cid-A")
    assert reviewed.get(db, "p1") == "cid-A"


def test_get_missing_is_none(tmp_path):
    # fail-closed: missing db and unknown post_id both return None
    assert reviewed.get(str(tmp_path / "nope.sqlite"), "p1") is None
    db = _db(tmp_path)
    reviewed.mark(db, "p1", "cid-A")
    assert reviewed.get(db, "p2") is None


def test_mark_refresh_updates_content_id(tmp_path):
    db = _db(tmp_path)
    reviewed.mark(db, "p1", "cid-A")
    reviewed.mark(db, "p1", "cid-B")
    assert reviewed.get(db, "p1") == "cid-B"
