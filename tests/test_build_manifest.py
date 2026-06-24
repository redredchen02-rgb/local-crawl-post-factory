import io
import json

import pytest

from cpost.core import cli
from cpost.core.errors import ValidationError
from cpost.core.schema import PACKAGE_INPUT_REQUIRED, PackageInput
from cpost.cli.build_manifest import _REQUIRED, _build, _run


def _record(tmp_path, **overrides):
    rec = {
        "source_id": "site",
        "url": "https://example.com/post/1",
        "canonical_url": "https://example.com/post/1",
        "title": "Hello World",
        "caption": "a caption line\nhttps://example.com/post/1",
        "content_hash": "deadbeef",
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
    assert manifest_path == str(folder / "manifest.json")

    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["backend"]["status"] == "package_built"
    assert manifest["post_id"] == "20260615_https_example_com_post_1"
    assert manifest["content"]["title"] == "Hello World"
    assert manifest["content"]["body"] == rec["caption"]
    assert "media" not in manifest
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


# --- Unit 12 (R8): PackageInput contract + distinct-cluster post_id folders ---

def test_package_input_required_matches_build_required():
    # The documented contract constant stays in lockstep with build's _REQUIRED.
    assert PACKAGE_INPUT_REQUIRED == _REQUIRED == ("title", "canonical_url", "caption")


def test_two_cluster_ids_distinct_post_id_folders(tmp_path):
    # Two different scoop canonicals must slug to DISTINCT post_id folders, or
    # write_text_no_overwrite would silently drop the second (cross-cluster
    # pollution). Exercise realistic c_<12hex> ids under the scoop host.
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec_a = _record(
        tmp_path, canonical_url="https://scoop.cpost.local/c_aaaaaaaaaaaa",
        caption="A 正文", title="瓜 A")
    rec_b = _record(
        tmp_path, canonical_url="https://scoop.cpost.local/c_bbbbbbbbbbbb",
        caption="B 正文", title="瓜 B")

    p_a = _build(rec_a, str(out), str(log))
    p_b = _build(rec_b, str(out), str(log))

    assert p_a != p_b
    folders = sorted(f.name for f in out.iterdir())
    assert len(folders) == 2
    assert "c_aaaaaaaaaaaa" in folders[0] and "c_bbbbbbbbbbbb" in folders[1]


def test_legacy_track_required_fields_satisfied_by_package_input(tmp_path):
    # A PackageInput carrying only the three required fields (legacy repost track
    # minimum) still builds without error.
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec: PackageInput = {
        "title": "Legacy Post",
        "canonical_url": "https://news.example.com/a/1",
        "caption": "legacy caption body",
    }
    manifest_path = _build(dict(rec), str(out), str(log))
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    assert manifest["content"]["title"] == "Legacy Post"
    assert manifest["content"]["body"] == "legacy caption body"


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


# --- U1 (R1/R5): post_id collision-resistance + no silent stale reuse ---

# Two distinct same-date URLs whose slugs agree in the first 60 chars (the bug
# hunt's verified trigger): without disambiguation both collapse onto one folder
# and the second post's content is silently lost.
_LONG_A = (
    "https://example.com/news/2024/06/a-very-long-breaking-news-headline-"
    "about-something-important-happening-today/part-1"
)
_LONG_B = (
    "https://example.com/news/2024/06/a-very-long-breaking-news-headline-"
    "about-something-important-happening-today/part-2"
)


def _body_of(manifest_path):
    return json.loads(open(manifest_path, encoding="utf-8").read())["content"]["body"]


def test_long_url_slug_collision_yields_distinct_folders(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec_a = _record(tmp_path, canonical_url=_LONG_A, caption="A body", title="Part 1")
    rec_b = _record(tmp_path, canonical_url=_LONG_B, caption="B body", title="Part 2")

    p_a = _build(rec_a, str(out), str(log))
    p_b = _build(rec_b, str(out), str(log))

    assert p_a != p_b
    assert len(list(out.iterdir())) == 2
    # Both posts' content is preserved (neither silently dropped).
    assert _body_of(p_a) == "A body"
    assert _body_of(p_b) == "B body"


def test_same_url_changed_content_raises_instead_of_silent_stale(tmp_path):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    _build(_record(tmp_path, caption="version one"), str(out), str(log))

    # Same canonical_url + same date, but edited body -> must NOT silently keep
    # the stale files and report success.
    with pytest.raises(ValidationError):
        _build(_record(tmp_path, caption="version two"), str(out), str(log))

    folder = out / "20260615_https_example_com_post_1"
    assert _body_of(folder / "manifest.json") == "version one"  # original preserved


def test_distinct_urls_with_identical_slug_raise_collision(tmp_path):
    # Two short distinct URLs that slug identically (separator collapses) share a
    # post_id; the guard turns the silent loss into a loud error.
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    _build(_record(tmp_path, canonical_url="https://example.com/a-b", caption="A"),
           str(out), str(log))
    with pytest.raises(ValidationError):
        _build(_record(tmp_path, canonical_url="https://example.com/a/b", caption="B"),
               str(out), str(log))


def test_run_emits_distinct_manifest_paths_for_colliding_pair(tmp_path, monkeypatch):
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    rec_a = _record(tmp_path, canonical_url=_LONG_A, caption="A body", title="Part 1")
    rec_b = _record(tmp_path, canonical_url=_LONG_B, caption="B body", title="Part 2")
    stdin = json.dumps(rec_a) + "\n" + json.dumps(rec_b) + "\n"

    code, out_txt, err = _run_command(stdin, out, log, monkeypatch)

    assert code == 0
    paths = [json.loads(ln)["manifest_path"] for ln in out_txt.splitlines() if ln.strip()]
    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert len(list(out.iterdir())) == 2


def test_corrupt_manifest_raises_instead_of_silent_overwrite(tmp_path):
    """L76-77: unreadable JSON in existing manifest -> ValidationError."""
    out = tmp_path / "out"
    log = tmp_path / "logs" / "audit.jsonl"
    _build(_record(tmp_path), str(out), str(log))
    folder = out / "20260615_https_example_com_post_1"
    # Corrupt the manifest
    (folder / "manifest.json").write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValidationError, match="unreadable"):
        _build(_record(tmp_path), str(out), str(log))
