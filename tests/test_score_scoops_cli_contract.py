"""CLI error-contract tests for ``score-scoops`` (lane L8).

Pins the central CLI I/O contract (cpost.core.cli) for the score-scoops
read-only query command: success writes structured JSON to stdout with empty
stderr and exit 0; failures write a single stderr line, leave stdout empty, and
carry the mapped exit code.

In-process style mirrors tests/test_score_scoops.py: monkeypatch stdout/stderr,
drive ``_run`` through ``cli.run`` with a ``SimpleNamespace`` of parsed args.
The missing-required-arg case goes through ``main``/argparse, which exits 2
before the handler runs. score-scoops reads clusters produced by cluster-scoops,
so the happy path seeds the library and clusters it first.
"""

import io
import json
from types import SimpleNamespace

import pytest

from cpost.core import cli, library
from cpost.cli import score_scoops
from cpost.cli.cluster_scoops import cluster_library
from cpost.cli.score_scoops import _run

NOW = "2026-06-18T00:00:00+00:00"

_CFG = {
    "ngram": 2, "similarity_threshold": 0.3, "time_window_hours": 24 * 365,
    "confidence_source_cap": 3, "quality_full_text_chars": 1000,
    "quality_recency_window_hours": 168, "quality_material_cap": 3,
    "weight_completeness": 0.5, "weight_recency": 0.2, "weight_material": 0.3,
    "weight_confidence": 0.6, "weight_quality": 0.4,
}


def _db(tmp_path):
    return str(tmp_path / "state.sqlite")


def _seed(db, items):
    with library.connect(db) as conn:
        for it in items:
            library.upsert(conn, now=NOW, **it)


def _setup_two_clusters(db):
    """A corroborated 3-source scoop plus one lone item, already clustered."""
    _seed(db, [
        {"canonical_url": "https://a.com/1", "title": "藝人A被爆隱婚生子", "source_id": "site-a",
         "source_text": "長" * 1000, "published_at": NOW},
        {"canonical_url": "https://b.com/9", "title": "藝人A被爆隱婚生子內幕", "source_id": "site-b",
         "source_text": "長" * 1000, "published_at": NOW},
        {"canonical_url": "https://c.com/7", "title": "獨家藝人A隱婚生子", "source_id": "site-c",
         "source_text": "長" * 1000, "published_at": NOW},
        {"canonical_url": "https://d.com/3", "title": "完全不相干的天氣新聞", "source_id": "site-d",
         "source_text": "短", "published_at": "2026-01-01T00:00:00+00:00"},
    ])
    with library.connect(db) as conn:
        cluster_library(conn, _CFG, NOW)


def _run_command(db, monkeypatch, config=None):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = cli.run(lambda: _run(SimpleNamespace(state=db, config=config)))
    return code, out.getvalue(), err.getvalue()


# --- happy path: exit 0 + valid JSON on stdout, empty stderr ----------------

def test_happy_clustered_library_emits_sorted_summary(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _setup_two_clusters(db)
    code, out, err = _run_command(db, monkeypatch)
    assert code == 0
    assert err == ""
    payload = json.loads(out.strip())
    assert payload["scored"] == 2
    scores = [c["score"] for c in payload["by_cluster"]]
    assert scores == sorted(scores, reverse=True)   # summary is score-desc


def test_happy_empty_library_is_valid_zero_summary(tmp_path, monkeypatch):
    db = _db(tmp_path)
    with library.connect(db):
        pass  # create an empty but valid state file
    code, out, err = _run_command(db, monkeypatch)
    assert code == 0
    assert err == ""
    assert json.loads(out.strip()) == {"scored": 0, "by_cluster": []}


# --- usage error: missing required --state -> exit 2, stderr, empty stdout ---

def test_missing_state_arg_exits_2_via_argparse(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["score-scoops"])
    with pytest.raises(SystemExit) as exc:
        score_scoops.main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""            # nothing on stdout
    assert "usage:" in captured.err      # argparse diagnostic on stderr


# --- bad config -> ValidationError mapped to exit 2 -------------------------

def test_bad_config_exits_2_validation(tmp_path, monkeypatch):
    db = _db(tmp_path)
    with library.connect(db):
        pass
    cfgp = tmp_path / "scoring.yaml"
    cfgp.write_text("ngram: not_an_int\n", encoding="utf-8")
    code, out, err = _run_command(db, monkeypatch, config=str(cfgp))
    assert code == 2
    assert out == ""
    assert err.strip()                   # exactly one diagnostic line
    assert "ngram" in err


# --- corrupt SQLite state file ---------------------------------------------
# CONTRACT NOTE (real bug surfaced): a corrupt/unreadable state file does NOT
# map to a dependency/external error (exit 3/4). cpost.core.db.connect only
# wraps the sqlite3.connect() call in its `except sqlite3.Error`; the corruption
# instead surfaces from executescript(schema), escapes as a raw
# sqlite3.DatabaseError, and cli.run's catch-all maps it to exit 5. We pin the
# observed behavior here rather than the spec-ideal exit 4 (do not fix source).

def test_corrupt_sqlite_state_exits_5(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite"
    db.write_bytes(b"this is not a sqlite database " * 8)
    code, out, err = _run_command(str(db), monkeypatch)
    assert code == 5
    assert out == ""
    assert err.strip()                   # exactly one diagnostic line
    assert "\n" not in err.strip()       # single line, no trailing internal lines
