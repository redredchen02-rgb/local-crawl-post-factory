"""normalize-items: clean/validate crawled NDJSON -> normalized NDJSON.

Reads crawled records from stdin, writes normalized records to stdout under the
shared CLI contract. Per origin §4.2/§13.1 a malformed input line or a record
missing required ``title``/``canonical_url`` (after derivation) fails the whole
command with exit 2.
"""

import argparse

from core import cli, io_ndjson, url_utils, validators
from core.errors import ValidationError
from core.schema import CRAWLED_REQUIRED

# Text fields that get whitespace-collapsed/trimmed.
_TEXT_FIELDS = ("title", "description", "text")
# URL fields that get canonicalized.
_URL_FIELDS = ("url", "canonical_url")


def _normalize(obj: dict) -> dict:
    """Pure normalization of one crawled record.

    Normalizes url/canonical_url, derives canonical_url from url when missing,
    cleans text fields, drops empty optional keys, and validates that
    canonical_url is a valid URL and title is non-empty.
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
    validators.require_nonempty(out.get("title"), field="title")

    return out


def _run():
    for obj in io_ndjson.read_lines():
        io_ndjson.write_line(_normalize(obj))


def main():
    argparse.ArgumentParser(
        prog="normalize-items",
        description="Clean/validate crawled NDJSON into normalized NDJSON (stdin->stdout).",
    ).parse_args()
    cli.main_wrapper(_run)


if __name__ == "__main__":
    main()
