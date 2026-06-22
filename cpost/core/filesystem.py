"""Filesystem helpers with a no-overwrite guarantee (origin R4)."""

import os
import shutil
import tempfile
from pathlib import Path

from cpost.core.errors import ValidationError


def atomic_write_text(dest: str | Path, text: str) -> Path:
    """Write ``text`` to ``dest`` atomically; a crash never truncates ``dest``.

    The temp file is created in ``dest.parent`` (the SAME filesystem) so the
    final ``os.replace`` is atomic — a regression to the default temp dir would
    raise "Invalid cross-device link". ``flush`` + ``os.fsync`` make the bytes
    durable before the rename, so a mid-write failure leaves the original file
    intact (old-or-new, never half-written). Callers pass pre-serialized text
    and keep their own json.dumps options.
    """
    dest_p = Path(dest)
    ensure_dir(dest_p.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest_p.parent),
                                    prefix=dest_p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest_p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dest_p


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def copy_no_overwrite(src: str | Path, dst: str | Path) -> Path:
    """Copy src -> dst. If dst exists, leave it untouched and return it.

    Never overwrites an existing destination (R4). Uses an exclusive-create
    (``O_EXCL``) on dst — not ``os.replace`` — so a concurrently-created target
    is preserved rather than silently clobbered (last-writer-wins).
    """
    src_p, dst_p = Path(src), Path(dst)
    if not src_p.exists():
        raise ValidationError(f"source file not found: {src_p}")
    ensure_dir(dst_p.parent)
    try:
        fd = os.open(dst_p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError:
        return dst_p
    try:
        with os.fdopen(fd, "wb") as out_fh, open(src_p, "rb") as in_fh:
            shutil.copyfileobj(in_fh, out_fh)
    except BaseException:
        try:
            os.unlink(dst_p)
        except OSError:
            pass
        raise
    shutil.copystat(src_p, dst_p)  # preserve copy2 metadata (mode/mtime)
    return dst_p


def write_text_no_overwrite(path: str | Path, text: str) -> Path:
    """Write ``text`` to ``path``. If it exists, leave it and return it.

    Uses exclusive-create (``open(path, "x")``) so two concurrent writers to the
    same new path resolve to exactly one winner; the loser sees the existing
    file (``FileExistsError``) and neither clobbers the other.
    """
    p = Path(path)
    ensure_dir(p.parent)
    try:
        fh = open(p, "x", encoding="utf-8")
    except FileExistsError:
        return p
    try:
        with fh:
            fh.write(text)
    except BaseException:
        # A mid-write failure (e.g. disk full) must not leave a sticky,
        # truncated file behind — mirror copy_no_overwrite's cleanup.
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
        raise
    return p
