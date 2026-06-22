"""CLI error-contract tests for ``dedupe-posts`` (LANE L4).

dedupe-posts is READ-ONLY on state (R10): it reads normalized NDJSON from stdin,
drops already-published canonical_urls, and writes the survivors to stdout. It
has no browser/external collaborator, so there is no exit-4 path here; instead we
exercise the read-only state handling (missing/empty state) plus the NDJSON I/O
contract (origin §2.3 / §10): success -> stdout NDJSON, stderr empty, exit 0;
input/usage failure -> empty stdout, stderr diagnostic, exit 2.

The happy/usage paths run the real ``main`` as a subprocess (true stdin->stdout
NDJSON + argparse exit codes). The validation paths drive ``_run`` in-process
under the contract runner with stdin monkeypatched, mirroring the existing suite.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

from cpost.core import cli, state
from cpost.core.url_utils import title_hash
from cpost.cli import dedupe_posts

ROOT = Path(__file__).resolve().parent.parent


def _seed_published(db_path, *, canonical_url, title="A"):
    with state.connect(str(db_path)) as conn:
        state.upsert(
            conn, canonical_url=canonical_url, title=title,
            title_hash=title_hash(title), status="published",
            now="2026-06-15T00:00:00Z",
        )


def _run_subprocess(state_path, stdin_text):
    """Invoke dedupe-posts as a subprocess; return (returncode, stdout, stderr)."""
    cmd = [sys.executable, "-m", "cpost.cli.dedupe_posts", "--state", str(state_path)]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), input=stdin_text,
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _ndjson(*records):
    return "".join(json.dumps(r) + "\n" for r in records)


# --- happy path: valid NDJSON -> exit 0 + NDJSON on stdout, stderr empty ------

def test_happy_empty_state_passes_all_exits_0(tmp_path):
    """An empty (auto-created) state drops nothing: every record passes through."""
    db = tmp_path / "state.db"
    records = [
        {"canonical_url": "https://x.test/a", "title": "A"},
        {"canonical_url": "https://x.test/b", "title": "B"},
    ]
    rc, out, err = _run_subprocess(db, _ndjson(*records))
    assert rc == 0, err
    assert err == ""
    emitted = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert emitted == records


def test_happy_published_url_dropped_exits_0(tmp_path):
    """A canonical_url already published is dropped; the rest pass through."""
    db = tmp_path / "state.db"
    _seed_published(db, canonical_url="https://x.test/a")
    records = [
        {"canonical_url": "https://x.test/a", "title": "A"},  # published -> dropped
        {"canonical_url": "https://x.test/b", "title": "B"},  # new -> emitted
    ]
    rc, out, err = _run_subprocess(db, _ndjson(*records))
    assert rc == 0, err
    assert err == ""
    emitted = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert emitted == [{"canonical_url": "https://x.test/b", "title": "B"}]


def test_happy_empty_stdin_emits_nothing_exits_0(tmp_path):
    """Empty stdin is a valid no-op: exit 0, empty stdout, empty stderr."""
    db = tmp_path / "state.db"
    rc, out, err = _run_subprocess(db, "")
    assert rc == 0, err
    assert out == ""
    assert err == ""


# --- error: missing required input -> exit 2, stderr, empty stdout -----------

def test_missing_state_arg_exits_2(tmp_path):
    """argparse: a missing required --state exits 2, usage on stderr, no stdout."""
    cmd = [sys.executable, "-m", "cpost.cli.dedupe_posts"]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), input="", capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() != ""


def test_invalid_json_line_exits_2(tmp_path):
    """A malformed stdin line -> ValidationError -> exit 2, stderr, no stdout."""
    db = tmp_path / "state.db"
    rc, out, err = _run_subprocess(db, "{not valid json}\n")
    assert rc == 2
    assert out == ""
    assert err.strip() != ""


def test_record_missing_canonical_url_exits_2(tmp_path):
    """A record lacking canonical_url -> ValidationError -> exit 2, stderr, no stdout."""
    db = tmp_path / "state.db"
    rc, out, err = _run_subprocess(db, _ndjson({"title": "A"}))
    assert rc == 2
    assert out == ""
    assert err.strip() != ""


def test_record_missing_title_exits_2(tmp_path):
    """A record lacking title -> ValidationError -> exit 2, stderr, no stdout."""
    db = tmp_path / "state.db"
    rc, out, err = _run_subprocess(db, _ndjson({"canonical_url": "https://x.test/a"}))
    assert rc == 2
    assert out == ""
    assert err.strip() != ""


# --- in-process: contract runner maps the same failures (no subprocess) ------

def _ns(state_path):
    return type("NS", (), {"state": str(state_path)})()


def test_in_process_invalid_json_maps_exit_2(tmp_path, monkeypatch, capsys):
    """The same malformed-line failure maps to exit 2 through cli.run in-process."""
    db = tmp_path / "state.db"
    monkeypatch.setattr(sys, "stdin", io.StringIO("{nope}\n"))
    code = cli.run(lambda: dedupe_posts._run(_ns(db)))
    cap = capsys.readouterr()
    assert code == 2
    assert cap.out == ""
    assert cap.err.strip() != ""


def test_in_process_empty_state_passes_all(tmp_path, monkeypatch, capsys):
    """Read-only empty-state happy path through cli.run: exit 0, NDJSON out."""
    db = tmp_path / "state.db"
    records = [{"canonical_url": "https://x.test/a", "title": "A"}]
    monkeypatch.setattr(sys, "stdin", io.StringIO(_ndjson(*records)))
    code = cli.run(lambda: dedupe_posts._run(_ns(db)))
    cap = capsys.readouterr()
    assert code == 0
    emitted = [json.loads(line) for line in cap.out.splitlines() if line.strip()]
    assert emitted == records
    assert cap.err == ""
