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


# -- U7: per-record resilience ---------------------------------------------- #

def test_bad_record_quarantined_good_records_survive(monkeypatch):
    """[good, empty-title, good] → both good emitted, bad skipped, exit 0."""
    import json

    good_a = _base_item(url="https://example.com/a/", canonical_url="https://example.com/a/")
    bad = _base_item(title="   ", url="https://example.com/b/",
                     canonical_url="https://example.com/b/")
    good_c = _base_item(url="https://example.com/c/", canonical_url="https://example.com/c/")
    stdin_text = "".join(json.dumps(x) + "\n" for x in (good_a, bad, good_c))

    code, out, err = _run_command(stdin_text, monkeypatch)

    assert code == 0
    emitted = [json.loads(line) for line in out.strip().splitlines()]
    urls = {it["canonical_url"] for it in emitted}
    # Both good records survive; the bad one in the MIDDLE did not abort the stream.
    assert urls == {"https://example.com/a", "https://example.com/c"}
    # The bad record produced a stderr warning.
    assert "warning" in err.lower()
    assert "skip" in err.lower()


def test_all_invalid_stream_nonzero_no_silent_empty_success(monkeypatch):
    """Every record invalid → non-zero exit, nothing emitted (no silent success)."""
    import json

    bad1 = _base_item(title="   ")
    bad2 = _base_item(title="")
    stdin_text = json.dumps(bad1) + "\n" + json.dumps(bad2) + "\n"

    code, out, err = _run_command(stdin_text, monkeypatch)

    assert code == 2
    assert out == ""
    assert err.strip() != ""


def test_all_valid_stream_unchanged(monkeypatch):
    """Happy path: an all-valid stream emits every record at exit 0, no warnings."""
    import json

    a = _base_item(url="https://example.com/a/", canonical_url="https://example.com/a/")
    b = _base_item(url="https://example.com/b/", canonical_url="https://example.com/b/")
    stdin_text = json.dumps(a) + "\n" + json.dumps(b) + "\n"

    code, out, err = _run_command(stdin_text, monkeypatch)

    assert code == 0
    assert err == ""
    emitted = [json.loads(line) for line in out.strip().splitlines()]
    assert len(emitted) == 2


def test_first_record_bad_does_not_abort_before_good(monkeypatch):
    """A bad FIRST record must not exit 2 before the following good record runs."""
    import json

    bad = _base_item(title="   ")
    good = _base_item(url="https://example.com/ok/",
                      canonical_url="https://example.com/ok/")
    stdin_text = json.dumps(bad) + "\n" + json.dumps(good) + "\n"

    code, out, err = _run_command(stdin_text, monkeypatch)

    assert code == 0
    emitted = [json.loads(line) for line in out.strip().splitlines()]
    assert len(emitted) == 1
    assert emitted[0]["canonical_url"] == "https://example.com/ok"
    assert "warning" in err.lower()
