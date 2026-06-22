import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from cpost.core import filesystem
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
