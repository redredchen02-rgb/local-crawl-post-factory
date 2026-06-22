"""Tests for render-caption (Unit 5, origin §4.4/§11.4, R5)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cpost.core import url_utils
from cpost.cli.render_caption import _render, load_template, render_record

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
