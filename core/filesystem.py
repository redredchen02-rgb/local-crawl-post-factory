"""Filesystem helpers with a no-overwrite guarantee (origin R4)."""

import shutil
from pathlib import Path

from core.errors import ValidationError


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def copy_no_overwrite(src: str | Path, dst: str | Path) -> Path:
    """Copy src -> dst. If dst exists, leave it untouched and return it.

    Never overwrites an existing destination (R4).
    """
    src_p, dst_p = Path(src), Path(dst)
    if not src_p.exists():
        raise ValidationError(f"source file not found: {src_p}")
    if dst_p.exists():
        return dst_p
    ensure_dir(dst_p.parent)
    shutil.copy2(src_p, dst_p)
    return dst_p


def write_text_no_overwrite(path: str | Path, text: str) -> Path:
    p = Path(path)
    if p.exists():
        return p
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")
    return p
