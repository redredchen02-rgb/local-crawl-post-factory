import io
import json

import pytest

from core import cli
from core.errors import ExternalError
from src import select_cover
from src.select_cover import _run_factory, _select


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

    def fake_fetch(url, dest, timeout):
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
    def fake_fetch(url, dest, timeout):
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
