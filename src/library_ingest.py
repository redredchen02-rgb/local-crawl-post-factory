"""library-ingest: persist normalized items into the crawl library (plan U2).

A transparent pipeline stage: reads normalized NDJSON from stdin, upserts each
record into the crawl library (``core.library``), and writes the same record
back to stdout unchanged so downstream stages can still consume the stream.

The crawled/normalized full body lives under ``text``; the library stores it as
``source_text``. Ingest is READ/WRITE on the library but never drops data
(upsert keyed on ``canonical_url``). Requires ``--state`` (the shared SQLite
file). A malformed input line, or a record missing ``canonical_url``/``title``,
fails the whole command with exit 2 (CLI contract).
"""

import argparse
import sqlite3
from collections.abc import Generator, Iterable
from datetime import datetime, timezone

from core import cli, library
from core.errors import ValidationError
from core.io_ndjson import read_lines, write_line


def to_library_fields(record: dict) -> dict:
    """Map a normalized record to ``library.upsert`` kwargs (pure).

    Maps the full-body ``text`` field to ``source_text``. Raises ValidationError
    when the required ``canonical_url``/``title``/``source_id`` is missing.

    ``source_id`` is provenance for display/filter only (NOT corroboration), but
    it must be non-empty so the library never stores a blank origin. This is a
    persistence-boundary guard: normalize-items already rejects blanks, but a
    record reaching the library by any other path is caught here too (U9 R4).
    """
    canonical_url = record.get("canonical_url")
    title = record.get("title")
    source_id = record.get("source_id")
    if not canonical_url:
        raise ValidationError("record missing required field 'canonical_url'")
    if not title:
        raise ValidationError("record missing required field 'title'")
    if not source_id or not str(source_id).strip():
        raise ValidationError("record missing required field 'source_id'")
    return {
        "canonical_url": canonical_url,
        "title": title,
        "source_id": source_id,
        "url": record.get("url"),
        "source_text": record.get("text"),
        "description": record.get("description"),
        "published_at": record.get("published_at"),
        "discovered_at": record.get("discovered_at"),
    }


def ingest(records: Iterable[dict], conn: sqlite3.Connection,
           now: str) -> Generator[dict, None, None]:
    """Upsert each record into ``conn`` and yield it unchanged (transparent stage).

    A single shared connection is used for the whole stream (no per-record
    connect), so a batch ingest stays a single transaction.
    """
    for record in records:
        library.upsert(conn, now=now, **to_library_fields(record))
        yield record


def _run(args) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with library.connect(args.state) as conn:
        for record in ingest(read_lines(), conn, now):
            write_line(record)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="library-ingest",
        description="Persist normalized items into the crawl library; pass NDJSON through unchanged.",
    )
    parser.add_argument("--state", required=True, help="path to the SQLite state file")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
