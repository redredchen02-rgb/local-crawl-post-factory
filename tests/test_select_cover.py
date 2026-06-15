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
    """retries=0 (default): _download_once is called exactly once."""
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
