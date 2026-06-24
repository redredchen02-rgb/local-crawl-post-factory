"""Tests for render-caption (Unit 5, origin §4.4/§11.4, R5)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cpost.core import url_utils
from cpost.core.errors import ValidationError
from cpost.cli.render_caption import (
    _enforce_max_chars,
    _render,
    _run,
    load_template,
    make_content_hash,
    render_record,
)

ROOT = Path(__file__).resolve().parent.parent
REAL_TEMPLATE = ROOT / "templates" / "fixed-format.zh.yaml"


@pytest.fixture
def template_cfg():
    return load_template(str(REAL_TEMPLATE))


def _full_record():
    return {
        "title": "標題 Title",
        "description": "這是一段描述 description text",
        "canonical_url": "https://example.com/posts/123",
        "hashtags": "#tag1 #tag2",
    }


def test_happy_path_contains_all_parts(template_cfg):
    caption = _render(_full_record(), template_cfg)
    assert "標題 Title" in caption
    assert "這是一段描述 description text" in caption
    assert "https://example.com/posts/123" in caption
    assert "查看完整內容" in caption  # CTA text


def test_render_record_sets_caption_and_content_hash(template_cfg):
    """U5b: shared helper sets both fields; content_hash matches the formula."""
    rec = render_record(_full_record(), template_cfg)
    assert rec["caption"]
    expected = url_utils.content_hash(
        rec["canonical_url"], rec["title"], rec["caption"])
    assert rec["content_hash"] == expected


# --- make_content_hash (L9 extraction) -------------------------------------

# Pre-refactor expectation for the fixed fixture below, computed against the
# real zh template before extracting make_content_hash. If this changes, the
# publish-gate dedup hash changed — that is a behavior change, not a test bug.
_EXPECTED_FIXTURE_HASH = (
    "571ebbe861b6fe333406aa96fdc1a6ac3df6d970d7b2e58728a42c595a2c469a"
)


def test_make_content_hash_stable_and_agrees_with_render_record(template_cfg):
    """Happy: stable expected hash; extracted fn and render_record agree."""
    rec = render_record(_full_record(), template_cfg)
    direct = make_content_hash(rec)
    # Extracted function reproduces the exact inline inputs/order.
    assert direct == url_utils.content_hash(
        rec["canonical_url"], rec["title"], rec["caption"])
    # render_record stored exactly what make_content_hash computes.
    assert rec["content_hash"] == direct
    # Stable: same item -> same hash.
    assert make_content_hash(dict(rec)) == direct


def test_make_content_hash_missing_optional_fields_no_crash():
    """Edge: item missing every optional field hashes the empty-field formula."""
    assert make_content_hash({}) == url_utils.content_hash("", "", "")
    # Partial item: only canonical_url present; title/caption default to "".
    item = {"canonical_url": "https://example.com/x"}
    assert make_content_hash(item) == url_utils.content_hash(
        "https://example.com/x", "", "")


def test_render_record_content_hash_matches_pre_refactor_fixture(template_cfg):
    """Regression: byte-identical hash for a fixed fixture vs pre-refactor."""
    rec = render_record(_full_record(), template_cfg)
    assert rec["content_hash"] == _EXPECTED_FIXTURE_HASH


def test_missing_description_blank_rest_intact(template_cfg):
    record = _full_record()
    del record["description"]
    caption = _render(record, template_cfg)
    assert "標題 Title" in caption
    assert "https://example.com/posts/123" in caption
    assert "查看完整內容" in caption
    # No KeyError, no literal "{description}" leaked.
    assert "{description}" not in caption


def test_truncation_keeps_canonical_url():
    cfg = {
        "name": "tiny",
        "max_chars": 40,
        "format": "{title}\n{description}\n查看完整內容：\n{canonical_url}\n{hashtags}",
    }
    record = {
        "title": "T",
        "description": "x" * 500,
        "canonical_url": "https://example.com/keep-me",
        "hashtags": "#a",
    }
    caption = _render(record, cfg)
    assert len(caption) <= 40
    assert "https://example.com/keep-me" in caption


def test_over_budget_url_appears_exactly_once_intact():
    """U17 edge: url mid-caption + long trailing hashtags over max_chars=800.

    The url must survive exactly once and intact — never duplicated, never as a
    fragment left in the body.
    """
    url = "https://example.com/posts/this-is-a-canonical-url"
    cfg = {
        "name": "real-shape",
        "max_chars": 800,
        "format": "{title}\n\n{description}\n\n查看完整內容：\n{canonical_url}\n\n{hashtags}",
    }
    record = {
        "title": "T",
        "description": "d" * 700,
        "canonical_url": url,
        "hashtags": " ".join(f"#tag{i}" for i in range(40)),
    }
    caption = _render(record, cfg)
    assert len(caption) <= 800
    assert caption.count(url) == 1
    # No stray url scheme beyond the single intact occurrence (no fragment).
    assert caption.count("https") == 1
    assert caption.endswith(url)


def test_over_budget_empty_hashtags_single_url_at_tail():
    """U17 happy: empty hashtags (current in-repo flow) -> single url at tail."""
    url = "https://example.com/posts/keep"
    cfg = {
        "name": "real-shape",
        "max_chars": 60,
        "format": "{title}\n\n{description}\n\n查看完整內容：\n{canonical_url}\n\n{hashtags}",
    }
    record = {
        "title": "標題",
        "description": "x" * 500,
        "canonical_url": url,
        "hashtags": "",
    }
    caption = _render(record, cfg)
    assert len(caption) <= 60
    assert caption.count(url) == 1
    assert caption.count("https") == 1
    assert caption.endswith(url)


def test_budget_cut_mid_url_leaves_no_fragment():
    """U17 edge: budget boundary landing mid-url -> no url fragment in body."""
    url = "https://example.com/posts/abcdefghijklmnop"
    cfg = {
        "name": "real-shape",
        "max_chars": 70,
        "format": "{title}\n{canonical_url}\n{hashtags}",
    }
    record = {
        "title": "T",
        "description": "",
        "canonical_url": url,
        "hashtags": "#" + "z" * 200,
    }
    caption = _render(record, cfg)
    assert len(caption) <= 70
    assert caption.count(url) == 1
    # Exactly one scheme token: the intact tail url, no severed prefix fragment.
    assert caption.count("https") == 1
    assert caption.endswith(url)
    # The portion before the tail url must not contain any prefix of the url.
    body = caption[: -len(url)]
    assert "http" not in body


def test_over_budget_preserves_word_ending_in_url_prefix_char():
    """U17 regression (a): the body word right before the url ends in a char that
    is a prefix of the url scheme (e.g. 'launch' -> ...'h'). The old prefix-eating
    loop would silently drop that trailing 'h' ('launc'). The word must survive
    intact and the url must appear exactly once at the tail.
    """
    url = "https://news.site/article/12345"
    cfg = {
        "name": "real-shape",
        "max_chars": 53,
        "format": "{description} {canonical_url} {hashtags}",
    }
    record = {
        "title": "",
        "description": "The mayor will launch",
        "canonical_url": url,
        "hashtags": " ".join("#x" for _ in range(100)),
    }
    caption = _render(record, cfg)
    assert len(caption) <= 53
    assert "The mayor will launch" in caption
    assert "launc " not in caption  # the trailing 'h' was NOT eaten
    assert caption.count(url) == 1
    assert caption.endswith(url)


def test_over_budget_body_quoting_url_early_keeps_trailing_text():
    """U17 regression (b): the article body legitimately quotes the canonical url
    early, then has in-budget trailing text. The old code cut at the FIRST url
    occurrence and discarded everything after it. The trailing text must be kept.
    """
    url = "https://news.site/article/12345"
    # description quotes the url early, then continues with text that fits budget.
    cfg = {
        "name": "real-shape",
        "max_chars": 120,
        "format": "{description}\n{canonical_url}\n{hashtags}",
    }
    record = {
        "title": "",
        "description": f"See {url} for the original, then read THIS_TRAILING_TEXT.",
        "canonical_url": url,
        "hashtags": " ".join("#x" for _ in range(100)),
    }
    caption = _render(record, cfg)
    assert len(caption) <= 120
    assert "THIS_TRAILING_TEXT" in caption  # trailing body text NOT discarded
    # url still present (it appears in the quoted body and/or the tail); the
    # appended tail guarantees the canonical link survives.
    assert url in caption
    assert caption.endswith(url)


def test_determinism(template_cfg):
    record = _full_record()
    assert _render(record, template_cfg) == _render(dict(record), template_cfg)


def test_missing_template_file_exits_2():
    proc = subprocess.run(
        [sys.executable, "-m", "cpost.cli.render_caption", "--template", "/no/such/file.yaml"],
        input=json.dumps(_full_record()) + "\n",
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip()


def test_command_adds_caption_and_content_hash():
    record = _full_record()
    proc = subprocess.run(
        [sys.executable, "-m", "cpost.cli.render_caption", "--template", str(REAL_TEMPLATE)],
        input=json.dumps(record) + "\n",
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0
    assert proc.stderr == ""
    out = json.loads(proc.stdout.strip())
    assert "caption" in out
    assert len(out["content_hash"]) == 64


def test_load_template_nonexistent_file_raises_validation_error():
    """L38-39: OSError on read -> ValidationError with diagnostics."""
    with pytest.raises(ValidationError, match="cannot read template"):
        load_template("/no/such/template.yaml")


def test_load_template_invalid_yaml_raises_validation_error(tmp_path):
    """L42-43: YAML parse error -> ValidationError."""
    f = tmp_path / "bad.yaml"
    f.write_text(": [invalid yaml: unmatched", encoding="utf-8")
    with pytest.raises(ValidationError, match="invalid template YAML"):
        load_template(str(f))


def test_load_template_missing_format_key_raises_validation_error(tmp_path):
    """L45: valid YAML but no 'format' key -> ValidationError."""
    f = tmp_path / "no_format.yaml"
    f.write_text("name: test", encoding="utf-8")
    with pytest.raises(ValidationError, match="missing 'format' key"):
        load_template(str(f))


def test_enforce_max_chars_no_url():
    """L63: canonical_url is empty -> caption[:max_chars] truncation."""
    result = _enforce_max_chars("hello world body", "body", "", 5)
    assert result == "hello"


def test_enforce_max_chars_url_exceeds_budget():
    """L69: url alone exceeds max_chars -> url[:max_chars]."""
    result = _enforce_max_chars("prefix\nhttps://long.url", "prefix", "https://long.url", 5)
    assert result == "https"[:5]


def test_enforce_max_chars_non_positive():
    """L60: max_chars <= 0 returns caption verbatim."""
    result = _enforce_max_chars("any caption", "body", "https://url", 0)
    assert result == "any caption"
    result_neg = _enforce_max_chars("any caption", "body", "https://url", -1)
    assert result_neg == "any caption"


def test_load_template_returns_dict(tmp_path):
    """Happy-path: valid yaml with format key returns the parsed dict."""
    f = tmp_path / "good.yaml"
    f.write_text("format: '{title}'", encoding="utf-8")
    assert load_template(str(f)) == {"format": "{title}"}


def test_run_processes_stdin_to_stdout(tmp_path):
    """_run() reads NDJSON from stdin, renders each record, writes to stdout."""
    import io
    template = tmp_path / "t.yaml"
    template.write_text("format: '{title}'", encoding="utf-8")
    record = {"title": "Hello", "canonical_url": "https://x.com/1"}
    stdin = io.StringIO(json.dumps(record) + "\n")
    stdout = io.StringIO()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)
    try:
        _run(str(template))
    finally:
        monkeypatch.undo()
    out = json.loads(stdout.getvalue().strip())
    assert out["title"] == "Hello"
    assert "caption" in out
