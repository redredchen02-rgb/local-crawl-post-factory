import io
import json

import pytest

from core import cli
from core.errors import ExternalError, ValidationError
from src import select_cover
from src.select_cover import _run_factory, _select, _fetch


def _item(**overrides):
    item = {
        "source_id": "site",
        "canonical_url": "https://example.com/post-1",
        "title": "Hello",
        "image_url": "https://cdn.example.com/cover.jpg",
    }
    item.update(overrides)
    return item


def test_happy_path_downloads_and_sets_cover(tmp_path, monkeypatch):
    calls = {}

    def fake_fetch(url, dest, timeout, *a, **k):
        calls["url"] = url
        final = dest.with_suffix(".jpg")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"img")
        return str(final)

    monkeypatch.setattr(select_cover, "_fetch", fake_fetch)
    out = _select(_item(), tmp_path, 20)

    assert calls["url"] == "https://cdn.example.com/cover.jpg"
    assert out["cover_source"] == "https://cdn.example.com/cover.jpg"
    assert out["cover_path"].endswith(".jpg")


def test_existing_target_not_redownloaded(tmp_path, monkeypatch):
    # Pre-create the deterministic target file.
    item = _item()
    stem = select_cover._target_stem(item, item["image_url"])
    existing = tmp_path / f"{stem}.jpg"
    existing.write_bytes(b"old")

    def boom(*a, **k):
        raise AssertionError("_fetch must not be called when target exists")

    monkeypatch.setattr(select_cover, "_fetch", boom)
    out = _select(item, tmp_path, 20)

    assert out["cover_path"] == str(existing)
    assert existing.read_bytes() == b"old"  # not overwritten (R4)


def test_no_image_url_emits_none(tmp_path):
    item = _item()
    del item["image_url"]
    out = _select(item, tmp_path, 20)
    assert out["cover_source"] is None
    assert out["cover_path"] is None
    assert out["title"] == "Hello"


def test_fetch_timeout_exits_4(tmp_path, monkeypatch):
    def fake_fetch(url, dest, timeout, *a, **k):
        raise ExternalError("timed out")

    monkeypatch.setattr(select_cover, "_fetch", fake_fetch)

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_item()) + "\n"))
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)

    code = cli.run(_run_factory(tmp_path, 20))
    assert code == 4
    assert out.getvalue() == ""
    assert err.getvalue().strip() != ""


# --- U2 (R2): transient retry on download ------------------------------------

def test_fetch_no_retry_by_default(tmp_path, monkeypatch):
    """Default retries=3, but first attempt succeeds → _download_once called once."""
    calls = {"n": 0}

    def once(url, timeout):
        calls["n"] += 1
        return b"img", ".jpg"

    monkeypatch.setattr(select_cover, "_download_once", once)
    path = _fetch("https://x/c.jpg", tmp_path / "c", 20)
    assert calls["n"] == 1
    assert path.endswith(".jpg")


def test_fetch_retries_then_succeeds(tmp_path, monkeypatch):
    """A transient ExternalError is retried and the next attempt succeeds."""
    calls = {"n": 0}

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExternalError("transient")
        return b"img", ".jpg"

    monkeypatch.setattr(select_cover, "_download_once", flaky)
    path = _fetch("https://x/c.jpg", tmp_path / "c", 20, retries=2, backoff_sec=0)
    assert calls["n"] == 2
    assert path.endswith(".jpg")


def test_fetch_retries_exhausted_raises(tmp_path, monkeypatch):
    """ExternalError that never recovers raises after exhausting retries."""
    calls = {"n": 0}

    def always_fail(url, timeout):
        calls["n"] += 1
        raise ExternalError("down")

    monkeypatch.setattr(select_cover, "_download_once", always_fail)
    with pytest.raises(ExternalError):
        _fetch("https://x/c.jpg", tmp_path / "c", 20, retries=2, backoff_sec=0)
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_fetch_validation_error_not_retried(tmp_path, monkeypatch):
    """A non-image (ValidationError) is never retried."""
    calls = {"n": 0}

    def not_image(url, timeout):
        calls["n"] += 1
        raise ValidationError("not an image")

    monkeypatch.setattr(select_cover, "_download_once", not_image)
    with pytest.raises(ValidationError):
        _fetch("https://x/c.jpg", tmp_path / "c", 20, retries=3, backoff_sec=0)
    assert calls["n"] == 1


def test_fetch_explicit_zero_retries_no_sleep(tmp_path, monkeypatch):
    """retries=0 (explicit) stays backward-compatible: no sleep, one attempt."""
    calls = {"n": 0}
    slept = []

    def once(url, timeout):
        calls["n"] += 1
        return b"img", ".jpg"

    monkeypatch.setattr(select_cover, "_download_once", once)
    monkeypatch.setattr(select_cover.time, "sleep", lambda s: slept.append(s))
    _fetch("https://x/c.jpg", tmp_path / "c", 20, retries=0)
    assert calls["n"] == 1
    assert slept == []


def test_fetch_exponential_backoff_sleep_sequence(tmp_path, monkeypatch):
    """3 retries → 4 attempts total; sleep called 3 times with exponential gaps."""
    calls = {"n": 0}
    slept = []

    def always_fail(url, timeout):
        calls["n"] += 1
        raise ExternalError("down")

    monkeypatch.setattr(select_cover, "_download_once", always_fail)
    monkeypatch.setattr(select_cover.time, "sleep", lambda s: slept.append(s))
    with pytest.raises(ExternalError):
        _fetch("https://x/c.jpg", tmp_path / "c", 20, retries=3, backoff_sec=1.0)
    # 4 attempts: sleep after attempts 1, 2, 3 (not after final failure)
    assert calls["n"] == 4
    assert slept == [1.0, 2.0, 4.0]


def test_fetch_default_retries_exhaust_sleep_sequence(tmp_path, monkeypatch):
    """With DEFAULT_RETRIES=3 and all attempts failing, sleep sequence is [1,2,4]."""
    calls = {"n": 0}
    slept = []

    def always_fail(url, timeout):
        calls["n"] += 1
        raise ExternalError("down")

    monkeypatch.setattr(select_cover, "_download_once", always_fail)
    monkeypatch.setattr(select_cover.time, "sleep", lambda s: slept.append(s))
    with pytest.raises(ExternalError):
        _fetch("https://x/c.jpg", tmp_path / "c", 20)  # uses DEFAULT_RETRIES=3
    assert slept == [1.0, 2.0, 4.0]


# --- select_all: batch parallel cover download -------------------------------

def _select_all_item(slug_title, image_url=None):
    slug, title = slug_title
    item = {"source_id": "site", "canonical_url": f"https://x.test/{slug}",
            "title": title}
    if image_url:
        item["image_url"] = image_url
    return item


def test_select_all_happy_path(tmp_path, monkeypatch):
    """All covers downloaded, records mutated in-place, original list returned."""
    dl_dir = tmp_path / "assets"
    dl_dir.mkdir()

    def fake_select(rec, dd, to, re, ba):
        ext = ".jpg" if "a" in rec.get("image_url", "") else ".png"
        fname = f"{'a' if ext == '.jpg' else 'b'}{ext}"
        (dl_dir / fname).write_text("img")
        return {**rec, "cover_source": rec["image_url"],
                "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(select_cover, "select", fake_select)

    records = [
        _select_all_item(("a", "A"), "https://x.test/a.jpg"),
        _select_all_item(("b", "B"), "https://x.test/b.png"),
    ]
    original_id = id(records)
    result = select_cover.select_all(records, dl_dir, timeout=5)

    assert id(result) == original_id  # same list, mutated in-place
    assert result[0]["cover_path"].endswith(".jpg")
    assert result[1]["cover_path"].endswith(".png")
    assert dl_dir / "a.jpg"
    assert dl_dir / "b.png"


def test_select_all_skips_no_image_url(tmp_path, monkeypatch):
    """Records without image_url pass through untouched."""
    dl_dir = tmp_path / "assets"
    dl_dir.mkdir()

    touched = []

    def fake_select(rec, *a, **k):
        touched.append(rec.get("title"))
        return {**rec, "cover_path": "/fake"}

    monkeypatch.setattr(select_cover, "select", fake_select)

    records = [
        {"title": "no-img-1"},  # no image_url
        {"title": "has-img", "image_url": "https://x.test/c.jpg"},
        {"title": "no-img-2", "image_url": ""},  # empty string
    ]
    select_cover.select_all(records, dl_dir, timeout=5)
    assert touched == ["has-img"]
    assert records[0].get("cover_path") is None
    assert records[1].get("cover_path") == "/fake"
    assert records[2].get("cover_path") is None


def test_select_all_partial_failure(tmp_path, monkeypatch):
    """Some downloads fail → cover_error set, others still succeed."""
    dl_dir = tmp_path / "assets"
    dl_dir.mkdir()

    call = {"n": 0}

    def flaky(rec, *a, **k):
        call["n"] += 1
        if call["n"] == 2:
            raise ExternalError("broken pipe")
        fname = f"{call['n']}.jpg"
        (dl_dir / fname).write_text("img")
        return {**rec, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(select_cover, "select", flaky)

    records = [
        {"image_url": "https://x.test/1.jpg"},
        {"image_url": "https://x.test/2.jpg"},
        {"image_url": "https://x.test/3.jpg"},
    ]
    select_cover.select_all(records, dl_dir, timeout=5)

    assert records[0].get("cover_path")  # succeeded
    assert records[0].get("cover_error") is None
    assert records[1].get("cover_error") is not None  # failed
    assert "broken pipe" in records[1]["cover_error"]
    assert records[2].get("cover_path")  # succeeded


def test_select_all_progress_callback(tmp_path, monkeypatch):
    """progress_cb fires for each cover with count and status."""
    dl_dir = tmp_path / "assets"
    dl_dir.mkdir()

    def fake_select(rec, *a, **k):
        fname = "x.jpg"
        (dl_dir / fname).write_text("img")
        return {**rec, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(select_cover, "select", fake_select)

    msgs = []

    select_cover.select_all(
        [{"image_url": "https://x.test/1.jpg"}, {"image_url": "https://x.test/2.jpg"}],
        dl_dir, timeout=5, progress_cb=msgs.append)

    assert len(msgs) == 2
    assert all("cover " in m for m in msgs)
    assert all(m.endswith("(ok)") for m in msgs)


def test_select_all_clamps_workers(tmp_path, monkeypatch):
    """max_workers=0 or negative is clamped to 1."""
    dl_dir = tmp_path / "assets"
    dl_dir.mkdir()

    def fake_select(rec, *a, **k):
        fname = "x.jpg"
        (dl_dir / fname).write_text("img")
        return {**rec, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(select_cover, "select", fake_select)

    records = [{"image_url": "https://x.test/1.jpg"}]
    # Should not raise
    select_cover.select_all(records, dl_dir, timeout=5, max_workers=0)
    assert records[0].get("cover_path")
    # Negative also fine
    select_cover.select_all(
        [{"image_url": "https://x.test/2.jpg"}], dl_dir, timeout=5, max_workers=-3)


def test_select_all_empty_records(tmp_path):
    """Empty record list returns immediately."""
    result = select_cover.select_all([], tmp_path, timeout=5)
    assert result == []
