"""NDJSON stdin/stdout helpers.

Every pipeline stage reads NDJSON from stdin (one JSON object per line) and
writes NDJSON to stdout. Malformed input lines raise ValidationError so the
caller maps them to exit code 2.
"""

import json
import sys
from typing import Iterator

from core.errors import ValidationError


def read_lines(stream=None) -> Iterator[dict]:
    """Yield one parsed object per non-empty stdin line.

    Blank/whitespace-only lines are skipped. A line that is not valid JSON or
    that does not decode to an object raises ValidationError.
    """
    stream = stream if stream is not None else sys.stdin
    for lineno, raw in enumerate(stream, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid JSON on line {lineno}: {exc.msg}")
        if not isinstance(obj, dict):
            raise ValidationError(f"line {lineno} is not a JSON object")
        yield obj


def write_line(obj: dict, stream=None) -> None:
    """Write a single object as one compact NDJSON line."""
    stream = stream if stream is not None else sys.stdout
    stream.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
