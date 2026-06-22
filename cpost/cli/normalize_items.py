"""normalize-items: clean/validate crawled NDJSON -> normalized NDJSON.

Reads crawled records from stdin, writes normalized records to stdout under the
shared CLI contract. Per origin §4.2/§13.1 a malformed input line or a record
missing required ``title``/``canonical_url`` (after derivation) fails the whole
command with exit 2.
"""

import argparse
from urllib.parse import urlparse

from cpost.core import cli, io_ndjson, url_utils, validators
from cpost.core.errors import ValidationError
from cpost.core.schema import CRAWLED_REQUIRED

# Text fields that get whitespace-collapsed/trimmed.
_TEXT_FIELDS = ("title", "description", "text")
# URL fields that get canonicalized.
_URL_FIELDS = ("url", "canonical_url")


def normalize_one(obj: dict) -> dict:
    """Pure normalization of one crawled record.

    Normalizes url/canonical_url, derives canonical_url from url when missing,
    cleans text fields, drops empty optional keys, and validates that
    canonical_url is a valid URL and title is non-empty.

    ``source_id`` is auto-derived from the canonical_url host (lowercased) when
    not explicitly provided, so the legacy single-site/CLI path (no --source-id)
    still yields a non-empty origin (U9 R4) instead of being rejected.
    """
    out = dict(obj)

    for field in _URL_FIELDS:
        value = out.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = url_utils.normalize_url(value)

    # Derive canonical_url from url when missing/empty.
    canonical = out.get("canonical_url")
    if (not isinstance(canonical, str) or not canonical.strip()) and out.get("url"):
        out["canonical_url"] = out["url"]

    for field in _TEXT_FIELDS:
        if field in out and isinstance(out[field], str):
            out[field] = url_utils.clean_text(out[field])

    # Drop keys whose value is empty/None after cleaning, except required keys.
    for key in list(out.keys()):
        if key in CRAWLED_REQUIRED:
            continue
        value = out[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            del out[key]

    validators.require_url(out.get("canonical_url", ""), field="canonical_url")
    validators.require_nonempty(out.get("title") or "", field="title")
    # source_id is provenance (display/filter only, NOT corroboration) but is a
    # CRAWLED_REQUIRED field, so it must be non-empty (plan U9 R4). The legacy
    # single-site/CLI path crawls without a --source-id, so rather than rejecting
    # those records, derive source_id from the canonical_url host (already
    # validated to have a hostname above). Only error if neither is available.
    source_id = out.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        host = urlparse(out["canonical_url"]).hostname
        if not host:
            raise ValidationError("missing or empty field: source_id")
        out["source_id"] = host.lower()

    return out


_normalize = normalize_one  # deprecated: remove in vNEXT (use normalize_one)


def _run():
    for obj in io_ndjson.read_lines():
        io_ndjson.write_line(normalize_one(obj))


def main():
    argparse.ArgumentParser(
        prog="normalize-items",
        description="Clean/validate crawled NDJSON into normalized NDJSON (stdin->stdout).",
    ).parse_args()
    cli.main_wrapper(_run)


if __name__ == "__main__":
    main()
