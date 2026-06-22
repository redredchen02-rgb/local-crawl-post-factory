import io

import pytest

from cpost.core import cli
from cpost.core.errors import ValidationError
from cpost.cli.normalize_items import _normalize, _run


def _base_item(**overrides):
    item = {
        "source_id": "site",
        "url": "https://Example.com/Post/",
        "canonical_url": "https://Example.com/Post/",
        "title": "  Hello   World  ",
        "discovered_at": "2026-06-15T00:00:00Z",
    }
    item.update(overrides)
    return item


def test_happy_path_cleans_and_normalizes():
    item = _base_item(description="  some   text  ", text="  body  ")
    out = _normalize(item)

    assert out["title"] == "Hello World"
    assert out["description"] == "some text"
    assert out["text"] == "body"
    # trailing slash stripped, host lowercased
    assert out["url"] == "https://example.com/Post"
    assert out["canonical_url"] == "https://example.com/Post"


def test_empty_optional_keys_dropped():
    item = _base_item(description="   ", image_url=None)
    out = _normalize(item)
    assert "description" not in out
    assert "image_url" not in out


def test_missing_title_raises_validation():
    item = _base_item(title="   ")
    with pytest.raises(ValidationError):
        _normalize(item)


def test_canonical_derived_from_url_when_missing():
    item = _base_item()
    del item["canonical_url"]
    out = _normalize(item)
    assert out["canonical_url"] == "https://example.com/Post"


def test_invalid_canonical_raises_validation():
    item = _base_item(url="not-a-url", canonical_url="not-a-url")
    with pytest.raises(ValidationError):
        _normalize(item)


def test_blank_source_id_derived_from_host():
    """Legacy CLI/single-site path (no --source-id) → source_id = canonical host."""
    item = _base_item(source_id="")
    out = _normalize(item)
    assert out["source_id"] == "example.com"


def test_explicit_source_id_preserved():
    """An explicit non-empty source_id is never overwritten by the host."""
    item = _base_item(source_id="my-site")
    out = _normalize(item)
    assert out["source_id"] == "my-site"


def _run_command(stdin_text, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = cli.run(_run)
    return code, out.getvalue(), err.getvalue()


def test_contract_happy(monkeypatch):
    import json

    line = json.dumps(_base_item()) + "\n"
    code, out, err = _run_command(line, monkeypatch)
    assert code == 0
    assert err == ""
    assert json.loads(out.strip())["title"] == "Hello World"


def test_contract_malformed_line_exits_2(monkeypatch):
    code, out, err = _run_command("{not json}\n", monkeypatch)
    assert code == 2
    assert out == ""
    assert err.strip() != ""
