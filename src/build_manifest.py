"""build-manifest: assemble a per-post package folder + manifest.json.

Reads processed NDJSON from stdin (records carry title, canonical_url, caption,
source fields). For each record it builds a stable folder ``<out>/<post_id>/``
containing caption.txt, manifest.json and preview.html, appends an audit line,
and emits the record with ``manifest_path`` added to stdout under the shared
CLI contract (origin §4.7/§11.7, R5, R10).

Determinism (R5): same record -> same post_id and folder layout. The only
non-deterministic field is the timestamp stamped into audit/manifest.
"""

import argparse
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from core import audit, cli, io_ndjson
from core.errors import ValidationError
from core.filesystem import ensure_dir, write_text_no_overwrite
from core.schema import PackageInput, empty_manifest
from core.url_utils import slug

_REQUIRED = ("title", "canonical_url", "caption")


def _now_iso() -> str:
    """The ONE non-deterministic field (audit/manifest timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def _date_prefix(record: Mapping[str, Any]) -> str:
    """Derive a deterministic YYYYMMDD from the record, else current UTC date."""
    for key in ("published_at", "discovered_at"):
        value = record.get(key)
        if isinstance(value, str) and len(value) >= 10:
            head = value[:10].replace("-", "")
            if len(head) == 8 and head.isdigit():
                return head
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _preview_html(title: str, caption: str) -> str:
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title></head><body>\n"
        f"<h1>{escape(title)}</h1>\n"
        f"<pre>{escape(caption)}</pre>\n</body></html>\n"
    )


def build(record: dict | PackageInput, out_dir: str, log_path: str) -> str:
    """Build the package folder for one record; return its manifest path.

    ``record`` is the R8 normalized item -- a plain NDJSON ``dict`` from the CLI
    path or a ``PackageInput`` TypedDict from the generation track; both are
    accepted (read-only ``.get``/``[]`` access).

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

    write_text_no_overwrite(folder / "caption.txt", caption)
    write_text_no_overwrite(
        folder / "preview.html", _preview_html(title, caption)
    )

    # Persist the full crawled body (内文) for later cleaning/summarizing. This is
    # NOT the published caption: it lands in its own file + manifest pointer and
    # never touches content.body (which feeds publishing and the reviewed
    # content_id fingerprint). Records without text leave source_text_path None.
    source_text = str(record.get("text") or "")
    has_source_text = bool(source_text.strip())
    if has_source_text:
        write_text_no_overwrite(folder / "source_text.txt", source_text)

    manifest = empty_manifest(post_id, record)
    manifest["content"]["body"] = caption
    manifest["content"]["source_text_path"] = (
        "./source_text.txt" if has_source_text else None
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
