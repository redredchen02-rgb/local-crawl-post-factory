"""build-manifest: assemble a per-post package folder + manifest.json.

Reads processed NDJSON from stdin (records carry title, canonical_url, caption,
content_hash, cover_path, watermarked_cover_path, source fields). For each
record it builds a stable folder ``<out>/<post_id>/`` containing cover.jpg,
watermarked_cover.jpg, caption.txt, manifest.json and preview.html, appends an
audit line, and emits the record with ``manifest_path`` added to stdout under
the shared CLI contract (origin §4.7/§11.7, R5, R10).

Determinism (R5): same record -> same post_id and folder layout. The only
non-deterministic field is the timestamp stamped into audit/manifest.
"""

import argparse
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from core import audit, cli, io_ndjson
from core.errors import ValidationError
from core.filesystem import copy_no_overwrite, ensure_dir, write_text_no_overwrite
from core.schema import empty_manifest
from core.url_utils import slug

_REQUIRED = ("title", "canonical_url", "caption")


def _now_iso() -> str:
    """The ONE non-deterministic field (audit/manifest timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def _date_prefix(record: dict) -> str:
    """Derive a deterministic YYYYMMDD from the record, else current UTC date."""
    for key in ("published_at", "discovered_at"):
        value = record.get(key)
        if isinstance(value, str) and len(value) >= 10:
            head = value[:10].replace("-", "")
            if len(head) == 8 and head.isdigit():
                return head
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _preview_html(title: str, caption: str, has_cover: bool) -> str:
    img = '<img src="./cover.jpg" alt="cover">\n' if has_cover else ""
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title></head><body>\n"
        f"<h1>{escape(title)}</h1>\n{img}"
        f"<pre>{escape(caption)}</pre>\n</body></html>\n"
    )


def build(record: dict, out_dir: str, log_path: str) -> str:
    """Build the package folder for one record; return its manifest path.

    Idempotent (R5): re-running with the same record reuses the same folder and
    leaves existing files untouched (no-overwrite helpers).
    """
    for field in _REQUIRED:
        value = record.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValidationError(f"missing required field: {field}")

    canonical_url = str(record["canonical_url"])
    caption = str(record["caption"])
    title = str(record["title"])

    post_id = f"{_date_prefix(record)}_{slug(canonical_url)}"
    folder = ensure_dir(Path(out_dir) / post_id)

    has_cover = False
    cover_path = record.get("cover_path")
    if cover_path:
        copy_no_overwrite(cover_path, folder / "cover.jpg")
        has_cover = True
    watermarked = record.get("watermarked_cover_path")
    has_watermarked = False
    if watermarked:
        copy_no_overwrite(watermarked, folder / "watermarked_cover.jpg")
        has_watermarked = True

    write_text_no_overwrite(folder / "caption.txt", caption)
    write_text_no_overwrite(
        folder / "preview.html", _preview_html(title, caption, has_cover)
    )

    manifest = empty_manifest(post_id, record)
    manifest["content"]["body"] = caption
    manifest["media"]["cover_path"] = "./cover.jpg" if has_cover else None
    manifest["media"]["watermarked_cover_path"] = (
        "./watermarked_cover.jpg" if has_watermarked else None
    )
    ts = _now_iso()
    manifest["audit"]["created_at"] = ts
    manifest["audit"]["updated_at"] = ts

    manifest_file = folder / "manifest.json"
    write_text_no_overwrite(
        manifest_file, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )

    audit.record(log_path, post_id, "package_built", "ok", ts)
    return str(manifest_file)


_build = build  # deprecated: remove in vNEXT (use build)


def _run(out_dir: str, log_path: str):
    for record in io_ndjson.read_lines():
        record["manifest_path"] = build(record, out_dir, log_path)
        io_ndjson.write_line(record)


def main():
    parser = argparse.ArgumentParser(
        prog="build-manifest",
        description="Build per-post package folders + manifests (stdin->stdout).",
    )
    parser.add_argument("--out", default="./out", help="Output directory.")
    parser.add_argument(
        "--log", default="./logs/audit.jsonl", help="Audit log path."
    )
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args.out, args.log))


if __name__ == "__main__":
    main()
