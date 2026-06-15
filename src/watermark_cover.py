"""watermark-cover: paste a logo watermark onto each cover, output a NEW file (origin §4.6, R4/R5).

Reads NDJSON from stdin (records carry ``cover_path`` from select-cover) and writes
NDJSON with ``watermarked_cover_path`` added.

Determinism (R5): the output filename is derived from the record's ``content_hash``
prefix, so the same input always lands at the same path.

No-overwrite (R4): the output path always differs from ``cover_path``; the original
cover is never modified. If the output already exists it is reused, not rewritten.

Records without a ``cover_path`` are emitted unchanged with ``watermarked_cover_path=None``.
"""

import argparse
from pathlib import Path

import yaml

from core import cli
from core.errors import DependencyError, ValidationError
from core.filesystem import ensure_dir
from core.io_ndjson import read_lines, write_line

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - exercised only without Pillow
    raise DependencyError(f"Pillow is required for watermark-cover: {exc}")


def load_config(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise ValidationError(f"config not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        raise ValidationError(f"config must be a mapping: {p}")
    return cfg


def _output_path(cover_path: Path, content_hash: str, output_format: str) -> Path:
    stem = (content_hash or "nohash")[:16]
    return cover_path.parent / f"{stem}.watermarked.{output_format}"


def _position_box(cover_size, logo_size, position: str, margin: int):
    cw, ch = cover_size
    lw, lh = logo_size
    pos = (position or "bottom_right").lower()
    if "top" in pos:
        y = margin
    else:
        y = ch - lh - margin
    if "left" in pos:
        x = margin
    elif "center" in pos and "right" not in pos and "left" not in pos:
        x = (cw - lw) // 2
    else:
        x = cw - lw - margin
    return max(0, x), max(0, y)


def watermark(record: dict, cfg: dict) -> dict:
    """Pure helper: watermark one record's cover, return record + watermarked_cover_path.

    Never mutates the original cover file (R4). Reuses an existing output (no rewrite).
    """
    out = dict(record)
    cover_path = record.get("cover_path")
    if not cover_path:
        out["watermarked_cover_path"] = None
        return out

    cover_p = Path(cover_path)
    if not cover_p.exists():
        raise ValidationError(f"cover_path not found: {cover_p}")

    logo_path = cfg.get("logo_path")
    if not logo_path:
        raise ValidationError("config missing required field 'logo_path'")
    logo_p = Path(logo_path)
    if not logo_p.exists():
        raise ValidationError(f"logo_path not found: {logo_p}")

    output_format = str(cfg.get("output_format", "jpg")).lower()
    content_hash = record.get("content_hash", "")
    out_p = _output_path(cover_p, content_hash, output_format)

    # R4: never write back onto the original cover.
    if out_p.resolve() == cover_p.resolve():
        raise ValidationError("output path collides with original cover (R4)")

    out["watermarked_cover_path"] = str(out_p)

    # Reuse an existing deterministic output -- do not rewrite (R4/R5).
    if out_p.exists():
        return out

    opacity = float(cfg.get("opacity", 0.72))
    margin = int(cfg.get("margin_px", 32))
    max_ratio = float(cfg.get("max_width_ratio", 0.22))
    position = cfg.get("position", "bottom_right")
    quality = int(cfg.get("quality", 92))

    with Image.open(cover_p) as cover_img:
        cover = cover_img.convert("RGBA")
        with Image.open(logo_p) as logo_img:
            logo = logo_img.convert("RGBA")

            target_w = max(1, int(cover.width * max_ratio))
            scale = target_w / logo.width
            target_h = max(1, int(logo.height * scale))
            logo = logo.resize((target_w, target_h), Image.LANCZOS)

            if opacity < 1.0:
                alpha = logo.split()[3].point(lambda a: int(a * opacity))
                logo.putalpha(alpha)

            x, y = _position_box(cover.size, logo.size, position, margin)
            layer = Image.new("RGBA", cover.size, (0, 0, 0, 0))
            layer.paste(logo, (x, y), logo)
            composed = Image.alpha_composite(cover, layer)

        ensure_dir(out_p.parent)
        if output_format in ("jpg", "jpeg"):
            composed.convert("RGB").save(out_p, format="JPEG", quality=quality)
        else:
            composed.save(out_p, format=output_format.upper())

    return out


_watermark = watermark  # deprecated: remove in vNEXT (use watermark)


def _run(args) -> int:
    cfg = load_config(args.config)
    for record in read_lines():
        write_line(watermark(record, cfg))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="watermark-cover",
        description="Paste a logo watermark onto each cover; output a new file (never overwrite).",
    )
    parser.add_argument("--config", required=True, help="path to watermark.yaml")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
