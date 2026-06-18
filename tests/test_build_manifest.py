import io
import json

import pytest

from core import cli
from core.errors import ValidationError
from src.build_manifest import _build, _run


def _cover(tmp_path, name="src_cover.jpg"):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff fake jpeg")
    return p


def _record(tmp_path, **overrides):
    rec = {
        "source_id": "site",
        "url": "https://example.com/post/1",
        "canonical_url": "https://example.com/post/1",
        "title": "Hello World",
        "caption": "a caption line\nhttps://example.com/post/1",
        "content_hash": "deadbeef",
        "cover_path": str(_cover(tmp_path)),
        "discovered_at": "2026-06-15T00:00:00Z",
    }
    rec.update(overrides)
    return rec


def test_happy_path_builds_folder(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec = _record(tmp_path)

    manifest_path = _build(rec, str(out), str(log))

    folder = out / "20260615_https_example_com_post_1"
    assert folder.is_dir()
    assert (folder / "manifest.json").exists()
    assert (folder / "caption.txt").exists()
    assert (folder / "preview.html").exists()
    assert (folder / "cover.jpg").exists()
    assert manifest_path == str(folder / "manifest.json")

    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["backend"]["status"] == "package_built"
    assert manifest["post_id"] == "20260615_https_example_com_post_1"
    assert manifest["content"]["title"] == "Hello World"
    assert manifest["content"]["body"] == rec["caption"]
    assert manifest["media"]["cover_path"] == "./cover.jpg"
    assert manifest["audit"]["created_at"]

    assert "Hello World" in (folder / "preview.html").read_text()


# --- Unit 3 (R2/R4): full body text persisted to source_text.txt + pointer ---

def test_source_text_persisted_and_body_untouched(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    full_body = "完整内文第一段。\n第二段更多内容。" * 3
    rec = _record(tmp_path, text=full_body)

    _build(rec, str(out), str(log))
    folder = out / "20260615_https_example_com_post_1"

    # Raw body lands in its own file + pointer.
    assert (folder / "source_text.txt").read_text(encoding="utf-8") == full_body
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["content"]["source_text_path"] == "./source_text.txt"
    # R4: the published body (caption) is NOT touched by the new field.
    assert manifest["content"]["body"] == rec["caption"]


def test_no_source_text_when_text_absent(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec = _record(tmp_path)  # no "text"

    _build(rec, str(out), str(log))
    folder = out / "20260615_https_example_com_post_1"

    assert not (folder / "source_text.txt").exists()
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["content"]["source_text_path"] is None


def test_empty_text_treated_as_absent(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec = _record(tmp_path, text="   ")  # whitespace-only -> no file

    _build(rec, str(out), str(log))
    folder = out / "20260615_https_example_com_post_1"
    assert not (folder / "source_text.txt").exists()
    assert json.loads((folder / "manifest.json").read_text())["content"]["source_text_path"] is None


def test_rerun_idempotent_stable(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec = _record(tmp_path)

    p1 = _build(rec, str(out), str(log))
    content1 = (out / "20260615_https_example_com_post_1" / "manifest.json").read_text()
    p2 = _build(rec, str(out), str(log))
    content2 = (out / "20260615_https_example_com_post_1" / "manifest.json").read_text()

    assert p1 == p2
    assert content1 == content2  # no-overwrite keeps content stable (R5)
    folders = list(out.iterdir())
    assert len(folders) == 1


def test_missing_title_raises(tmp_path):
    rec = _record(tmp_path, title="   ")
    with pytest.raises(ValidationError):
        _build(rec, str(tmp_path / "out"), str(tmp_path / "log.jsonl"))


def _run_command(stdin_text, out, log, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    so, se = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", so)
    monkeypatch.setattr("sys.stderr", se)
    code = cli.run(lambda: _run(str(out), str(log)))
    return code, so.getvalue(), se.getvalue()


def test_command_missing_title_exits_2(tmp_path, monkeypatch):
    rec = _record(tmp_path, title="")
    code, out, err = _run_command(
        json.dumps(rec) + "\n", tmp_path / "out", tmp_path / "log.jsonl", monkeypatch
    )
    assert code == 2
    assert out == ""
    assert err.strip() != ""


def test_command_writes_audit_line(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "audit.jsonl"
    rec = _record(tmp_path)
    code, out, err = _run_command(
        json.dumps(rec) + "\n", tmp_path / "out", log, monkeypatch
    )
    assert code == 0
    assert err == ""
    emitted = json.loads(out.strip())
    assert emitted["manifest_path"].endswith("manifest.json")

    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert any(
        ln["stage"] == "package_built" and ln["status"] == "ok" for ln in lines
    )
