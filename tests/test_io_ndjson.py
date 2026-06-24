"""NDJSON read/write tests (io_ndjson.py)."""

import json
import io

import pytest

from cpost.core.io_ndjson import read_lines, write_line
from cpost.core.errors import ValidationError


def test_read_lines_yields_parsed_objects():
    stream = io.StringIO('{"a": 1}\n{"b": 2}\n')
    result = list(read_lines(stream))
    assert result == [{"a": 1}, {"b": 2}]


def test_read_lines_skips_blank():
    stream = io.StringIO('{"a": 1}\n\n  \n{"b": 2}\n')
    result = list(read_lines(stream))
    assert result == [{"a": 1}, {"b": 2}]


def test_read_lines_non_dict_raises():
    """A valid JSON value that is not a dict (e.g. a list) raises
    ValidationError (io_ndjson.py:32)."""
    stream = io.StringIO('["not", "a", "dict"]\n')
    with pytest.raises(ValidationError, match="not a JSON object"):
        list(read_lines(stream))


def test_read_lines_invalid_json_raises():
    stream = io.StringIO("not-json\n")
    with pytest.raises(ValidationError, match="invalid JSON"):
        list(read_lines(stream))


def test_write_line_writes_compact_json():
    stream = io.StringIO()
    write_line({"z": 1, "a": 2}, stream=stream)
    raw = stream.getvalue()
    parsed = json.loads(raw)
    assert parsed == {"z": 1, "a": 2}
    assert raw.endswith("\n")
