"""dedupe-posts: drop already-published items, emit only new ones (origin §4.3, R9/R10).

Reads normalized NDJSON from stdin, writes only NEW items to stdout.

For each record we compute ``title_hash(record['title'])`` and ask the SQLite
state whether this item has already been *published* (``is_processed`` matches
status='published' rows only -- R9). If so the record is dropped, otherwise it
is emitted unchanged.

dedupe is READ-ONLY on state (R10): we never write/upsert here. State writes
happen later at build-manifest (package_built) and publish (published).

First-release note: with no publish stage yet the state has no published rows,
so ``is_processed`` is always False and every item passes through. That is the
expected "only published counts" behaviour, not a bug.
"""

import argparse

from core import cli, state
from core.errors import ValidationError
from core.io_ndjson import read_lines, write_line
from core.url_utils import title_hash


def _dedupe(records, conn):
    """Yield records that are not already published in ``conn``.

    Read-only: never writes to state. Raises ValidationError when a record is
    missing the required ``canonical_url`` or ``title`` field.
    """
    for record in records:
        canonical_url = record.get("canonical_url")
        title = record.get("title")
        if not canonical_url:
            raise ValidationError("record missing required field 'canonical_url'")
        if not title:
            raise ValidationError("record missing required field 'title'")
        th = title_hash(title)
        if state.is_processed(conn, canonical_url, th):
            continue  # already published -> drop
        yield record


def _run(args) -> int:
    with state.connect(args.state) as conn:
        for record in _dedupe(read_lines(), conn):
            write_line(record)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dedupe-posts",
        description="Drop already-published items; emit only new normalized NDJSON.",
    )
    parser.add_argument("--state", required=True, help="path to the SQLite state file")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
