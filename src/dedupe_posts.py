"""dedupe-posts: drop already-published items, emit only new ones (origin §4.3, R9/R10).

Reads normalized NDJSON from stdin, writes only NEW items to stdout.

For each record we ask the SQLite state whether its ``canonical_url`` has
already been *published* (``is_processed`` matches status='published' rows only
-- R9). Dedup is URL-only (Q6): a shared title is not identity, so two different
articles with the same title are both emitted. If the URL was published the
record is dropped, otherwise it is emitted unchanged.

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


def dedupe(records, conn, on_skip=None):
    """Yield records that are not already published in ``conn``.

    Read-only: never writes to state. When a record is dropped, ``on_skip`` (if
    given) is called with ``(record, reason)`` where reason is 'url' (Q6:
    URL-only dedup) -- letting the caller record the decision for observability
    (R5) without making this stage a writer. Raises ValidationError when a record
    is missing the required ``canonical_url`` or ``title`` field.
    """
    for record in records:
        canonical_url = record.get("canonical_url")
        title = record.get("title")
        if not canonical_url:
            raise ValidationError("record missing required field 'canonical_url'")
        if not title:
            raise ValidationError("record missing required field 'title'")
        reason = state.skip_reason(conn, canonical_url)
        if reason is not None:
            if on_skip is not None:
                on_skip(record, reason)
            continue  # already published -> drop
        yield record


_dedupe = dedupe  # deprecated: remove in vNEXT (use dedupe)


def _run(args) -> int:
    with state.connect(args.state) as conn:
        for record in dedupe(read_lines(), conn):
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
