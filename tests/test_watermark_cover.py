"""Tests for watermark-cover (origin §4.6, §11.6, R4/R5)."""

import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from src import watermark_cover as wc

ROOT = Path(__file__).resolve().parent.parent


def _make_cover(path: Path, size=(800, 600), color=(40, 90, 160)) -> Path:
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _make_logo(path: Path, size=(200, 200)) -> Path:
    Image.new("RGBA", size, (255, 0, 0, 200)).save(path, format="PNG")
    return path


def _cfg(logo_path: Path, **over) -> dict:
    cfg = {
        "type": "image",
        "mode": "logo",
        "logo_path": str(logo_path),
        "position": "bottom_right",
        "opacity": 0.72,
        "margin_px": 32,
        "max_width_ratio": 0.22,
        "output_format": "jpg",
        "quality": 92,
    }
    cfg.update(over)
    return cfg


def test_happy_path_produces_valid_image(tmp_path):
    cover = _make_cover(tmp_path / "cover.png")
    logo = _make_logo(tmp_path / "logo.png")
    rec = {"cover_path": str(cover), "content_hash": "a" * 64}

    out = wc._watermark(rec, _cfg(logo))

    out_p = Path(out["watermarked_cover_path"])
    assert out_p.exists()
    assert out_p != cover
    with Image.open(out_p) as img:
        img.verify()  # valid image


def test_original_cover_unchanged(tmp_path):
    cover = _make_cover(tmp_path / "cover.png")
    logo = _make_logo(tmp_path / "logo.png")
    rec = {"cover_path": str(cover), "content_hash": "b" * 64}

    before_bytes = cover.read_bytes()
    before_mtime = cover.stat().st_mtime

    wc._watermark(rec, _cfg(logo))

    assert cover.read_bytes() == before_bytes  # R4: bytes unchanged
    assert cover.stat().st_mtime == before_mtime  # R4: mtime unchanged


def test_no_cover_path_emits_none(tmp_path):
    logo = _make_logo(tmp_path / "logo.png")
    out = wc._watermark({"content_hash": "c" * 64}, _cfg(logo))
    assert out["watermarked_cover_path"] is None


def test_deterministic_output_reused(tmp_path):
    cover = _make_cover(tmp_path / "cover.png")
    logo = _make_logo(tmp_path / "logo.png")
    rec = {"cover_path": str(cover), "content_hash": "d" * 64}

    out1 = wc._watermark(rec, _cfg(logo))
    p1 = Path(out1["watermarked_cover_path"])
    mtime1 = p1.stat().st_mtime

    out2 = wc._watermark(rec, _cfg(logo))
    assert out2["watermarked_cover_path"] == out1["watermarked_cover_path"]
    assert p1.stat().st_mtime == mtime1  # reused, not rewritten


def test_large_cover(tmp_path):
    cover = _make_cover(tmp_path / "big.png", size=(4000, 3000))
    logo = _make_logo(tmp_path / "logo.png")
    rec = {"cover_path": str(cover), "content_hash": "e" * 64}

    out = wc._watermark(rec, _cfg(logo))
    assert Path(out["watermarked_cover_path"]).exists()


def test_command_missing_logo_exits_2(tmp_path):
    cover = _make_cover(tmp_path / "cover.png")
    cfg_path = tmp_path / "watermark.yaml"
    cfg_path.write_text(
        f"mode: logo\nlogo_path: {tmp_path / 'nope.png'}\noutput_format: jpg\n",
        encoding="utf-8",
    )
    stdin = f'{{"cover_path": "{cover}", "content_hash": "f"}}\n'

    proc = subprocess.run(
        [sys.executable, "-m", "src.watermark_cover", "--config", str(cfg_path)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip()


def test_command_missing_config_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "src.watermark_cover", "--config", str(tmp_path / "missing.yaml")],
        input="",
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 2
