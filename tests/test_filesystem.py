import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from cpost.core import filesystem
from cpost.core import manifest as mf
from cpost.core.errors import ValidationError


# --- write_text_no_overwrite --------------------------------------------------

def test_write_text_no_overwrite_new_path(tmp_path):
    p = tmp_path / "nested" / "out.txt"
    result = filesystem.write_text_no_overwrite(p, "hello")
    assert result == p
    assert p.read_text(encoding="utf-8") == "hello"


def test_write_text_no_overwrite_existing_returns_original(tmp_path):
    p = tmp_path / "out.txt"
    p.write_text("original", encoding="utf-8")
    result = filesystem.write_text_no_overwrite(p, "new content")
    assert result == p
    assert p.read_text(encoding="utf-8") == "original"  # unchanged


def test_write_text_no_overwrite_partial_write_cleans_up(tmp_path, monkeypatch):
    """A mid-write failure must remove the just-created (truncated) file so a
    later retry isn't blocked by a sticky 0-byte file (no-overwrite contract)."""
    p = tmp_path / "out.txt"

    real_open = open

    class _BoomFile:
        def __init__(self, fh):
            self._fh = fh

        def write(self, _text):
            raise OSError("disk full")

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    def fake_open(path, mode="r", *args, **kwargs):
        fh = real_open(path, mode, *args, **kwargs)
        if "x" in mode:
            return _BoomFile(fh)
        return fh

    monkeypatch.setattr("cpost.core.filesystem.open", fake_open, raising=False)
    with pytest.raises(OSError, match="disk full"):
        filesystem.write_text_no_overwrite(p, "hello")
    assert not p.exists()  # no sticky truncated file left behind

    # A subsequent successful call (real open) writes correctly.
    monkeypatch.undo()
    result = filesystem.write_text_no_overwrite(p, "recovered")
    assert result == p
    assert p.read_text(encoding="utf-8") == "recovered"


def test_write_text_no_overwrite_concurrent_single_winner(tmp_path):
    p = tmp_path / "race.txt"
    payloads = [f"writer-{i}" for i in range(16)]

    def attempt(text):
        filesystem.write_text_no_overwrite(p, text)

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(attempt, payloads))

    # Exactly one winner; content is intact (one full payload), never merged.
    assert p.read_text(encoding="utf-8") in payloads


# --- copy_no_overwrite --------------------------------------------------------

def test_copy_no_overwrite_new_dst(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "sub" / "dst.txt"
    result = filesystem.copy_no_overwrite(src, dst)
    assert result == dst
    assert dst.read_text(encoding="utf-8") == "payload"


def test_copy_no_overwrite_existing_dst_not_overwritten(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "dst.txt"
    dst.write_text("existing", encoding="utf-8")
    result = filesystem.copy_no_overwrite(src, dst)
    assert result == dst
    assert dst.read_text(encoding="utf-8") == "existing"  # untouched


def test_copy_no_overwrite_preserves_metadata(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    os.utime(src, (1_600_000_000, 1_600_000_000))
    dst = tmp_path / "dst.txt"
    filesystem.copy_no_overwrite(src, dst)
    # copystat preserves mtime (copy2 parity).
    assert dst.stat().st_mtime == src.stat().st_mtime


def test_copy_no_overwrite_missing_src_raises(tmp_path):
    src = tmp_path / "nope.txt"
    dst = tmp_path / "dst.txt"
    with pytest.raises(ValidationError):
        filesystem.copy_no_overwrite(src, dst)
    assert not dst.exists()


# --- U13: atomic_write_text + manifest.save durability ------------------------

def test_atomic_write_text_writes_content(tmp_path):
    p = tmp_path / "sub" / "out.txt"
    result = filesystem.atomic_write_text(p, "payload")
    assert result == p
    assert p.read_text(encoding="utf-8") == "payload"


def test_atomic_write_text_temp_in_dest_parent(tmp_path, monkeypatch):
    """HEADLINE INVARIANT: the temp file must be created in dest.parent (same
    filesystem) — the single property that makes os.replace atomic."""
    p = tmp_path / "out.txt"
    real_mkstemp = filesystem.tempfile.mkstemp
    seen = {}

    def spy_mkstemp(*args, **kwargs):
        seen["dir"] = kwargs.get("dir")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(filesystem.tempfile, "mkstemp", spy_mkstemp)
    filesystem.atomic_write_text(p, "x")
    assert seen["dir"] == str(p.parent)


def test_manifest_save_round_trips(tmp_path):
    p = tmp_path / "manifest.json"
    m = {"post_id": "p1", "backend": {"status": "drafted"}}
    mf.save(p, m)
    loaded = mf.load(p)
    assert loaded["post_id"] == "p1"
    assert loaded["backend"]["status"] == "drafted"
    assert "updated_at" in loaded["audit"]


def test_manifest_save_replace_failure_keeps_original_intact(tmp_path, monkeypatch):
    """A simulated os.replace failure mid-save must leave the existing manifest
    untouched (old-or-new, never truncated)."""
    p = tmp_path / "manifest.json"
    mf.save(p, {"post_id": "p1", "backend": {"status": "drafted"}})
    original = p.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("replace failed")

    monkeypatch.setattr(filesystem.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        mf.save(p, {"post_id": "p1", "backend": {"status": "published"}})
    # original content intact, no leftover temp file
    assert p.read_text(encoding="utf-8") == original
    leftovers = [x for x in p.parent.iterdir() if x.name != "manifest.json"]
    assert leftovers == []


def test_manifest_save_load_across_lifecycle(tmp_path):
    p = tmp_path / "manifest.json"
    m = {"post_id": "p1", "backend": {"status": "drafted"}}
    mf.save(p, m)
    m = mf.load(p)
    mf.set_backend(m, status="draft_verified")
    mf.save(p, m)
    m = mf.load(p)
    mf.set_backend(m, status="published", published_url="https://x.com/p/1")
    mf.save(p, m)
    final = mf.load(p)
    assert final["backend"]["status"] == "published"
    assert final["backend"]["published_url"] == "https://x.com/p/1"
    # confirm it is real JSON on disk
    assert json.loads(p.read_text(encoding="utf-8"))["post_id"] == "p1"


# --- webui_config.save atomic write invariants (B2) --------------------------

def test_webui_config_save_atomic_temp_same_parent(tmp_path, monkeypatch):
    """temp file is created in dest.parent (same filesystem, no cross-device)."""
    from cpost.core import webui_config, filesystem as fs_mod

    seen_temps: list[str] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen_temps.append(src)
        real_replace(src, dst)

    # patch inside the filesystem module (where atomic_write_text lives)
    monkeypatch.setattr(fs_mod.os, "replace", spy_replace)
    p = str(tmp_path / "webui.yaml")
    webui_config.save(p, {"start_url": "https://example.com/news"})

    assert seen_temps, "os.replace was not called"
    for tmp in seen_temps:
        assert os.path.dirname(os.path.abspath(tmp)) == str(tmp_path), (
            f"temp {tmp!r} not in dest.parent {tmp_path}"
        )


def test_webui_config_save_atomic_old_intact_on_replace_failure(tmp_path, monkeypatch):
    """If os.replace raises on the config write, the original file is intact."""
    from cpost.core import webui_config, filesystem as fs_mod

    p = tmp_path / "webui.yaml"
    webui_config.save(str(p), {"start_url": "https://example.com/news", "limit": 5})
    old_text = p.read_text(encoding="utf-8")

    def always_fail(src, dst):
        raise OSError("simulated ENOSPC")

    # patch inside the filesystem module
    monkeypatch.setattr(fs_mod.os, "replace", always_fail)
    with pytest.raises(OSError, match="simulated"):
        webui_config.save(str(p), {"start_url": "https://example.com/news", "limit": 99})

    # original content must be intact (old-or-new, never truncated)
    assert p.read_text(encoding="utf-8") == old_text
