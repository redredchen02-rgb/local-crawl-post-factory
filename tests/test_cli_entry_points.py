"""Import-level tests for CLI entry points (main() function body).

Each module's ``main()`` is imported and called directly so that coverage is
tracked (subprocess tests don't contribute to ``--cov``).  We test with
dummy args that pass argparse but fail safely inside ``_run``, covering the
full ``main()`` body including ``cli.main_wrapper``.

The ``if __name__ == "__main__"`` guard cannot be reached through import
(by design).  A functional subprocess smoke-test covers those one-liners even
though they don't appear in the coverage report.
"""

import io
import subprocess
import sys
from pathlib import Path

import pytest

from cpost.cli import (
    auth_login,
    build_manifest,
    cluster_scoops,
    dedupe_posts,
    discover_sources,
    draft_post,
    generate_article,
    health_check_sources,
    library_ingest,
    normalize_items,
    publish_post,
    render_caption,
    score_scoops,
    verify_draft,
)

ROOT = Path(__file__).resolve().parent.parent


# --- Modules with ``def main(argv=None)`` — pass args directly ---------------


@pytest.mark.parametrize("call_main", [
    lambda: auth_login.main(["--login-url", "http://x",
                             "--storage-state", "/tmp/x",
                             "--until-url-contains", "ok"]),
    lambda: draft_post.main(["--manifest", "/nope",
                             "--backend", "/nope",
                             "--storage-state", "/nope"]),
    lambda: health_check_sources.main(["--roster-path", str(ROOT / "state" / "roster.db"),
                                       "--library-db", str(ROOT / "state" / "library.sqlite")]),
    lambda: publish_post.main(["--manifest", "/nope",
                               "--backend", "/nope",
                               "--storage-state", "/nope"]),
    lambda: verify_draft.main(["--manifest", "/nope",
                               "--backend", "/nope",
                               "--storage-state", "/nope"]),
])
def test_main_with_required_args_does_not_crash(call_main):
    """Dummy required args that pass argparse but fail inside _run.

    The failure is caught by cli.main_wrapper, exercising the full main()
    body including the cli.main_wrapper call.
    """
    try:
        call_main()
    except SystemExit:
        pass


# --- Modules with ``def main()`` — must monkeypatch sys.argv ----------------


def test_build_manifest_main(monkeypatch):
    """Zero required args — runs fully via main() with empty stdin."""
    monkeypatch.setattr("sys.argv", ["build-manifest"])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    try:
        build_manifest.main()
    except SystemExit:
        pass


def test_cluster_scoops_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["cluster-scoops",
                                     "--state", "/nope",
                                     "--config", "/nope"])
    try:
        cluster_scoops.main()
    except SystemExit:
        pass


def test_dedupe_posts_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["dedupe-posts",
                                     "--state", "/nope"])
    try:
        dedupe_posts.main()
    except SystemExit:
        pass


def test_discover_sources_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["discover-sources",
                                     "--sources-yaml", "/nope",
                                     "--roster-path", "/nope"])
    try:
        discover_sources.main()
    except SystemExit:
        pass


def test_generate_article_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["generate-article",
                                     "--state", "/nope",
                                     "--cluster-id", "nonexistent"])
    try:
        generate_article.main()
    except SystemExit:
        pass


def test_library_ingest_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["library-ingest",
                                     "--state", "/nope"])
    try:
        library_ingest.main()
    except SystemExit:
        pass


def test_normalize_items_main(monkeypatch):
    """Zero required args — runs fully via main() with empty stdin."""
    monkeypatch.setattr("sys.argv", ["normalize-items"])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    try:
        normalize_items.main()
    except SystemExit:
        pass


def test_render_caption_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["render-caption",
                                     "--template", "/nope"])
    try:
        render_caption.main()
    except SystemExit:
        pass


def test_score_scoops_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["score-scoops",
                                     "--state", "/nope",
                                     "--config", "/nope"])
    try:
        score_scoops.main()
    except SystemExit:
        pass


# --- Subprocess smoke-test for the if __name__ guard -------------------------
# Does not contribute to --cov data but verifies the real entry point works.

_ENTRY_MODULES = [
    "auth_login", "build_manifest", "cluster_scoops", "crawl_posts",
    "dedupe_posts", "discover_sources", "draft_post", "generate_article",
    "health_check_sources", "library_ingest", "normalize_items",
    "publish_post", "render_caption", "score_scoops", "verify_draft",
]


@pytest.mark.parametrize("mod_name", _ENTRY_MODULES)
def test_subprocess_help_exits_zero(mod_name: str) -> None:
    """``python -m cpost.cli.<module> --help`` exercises ``__name__`` guard."""
    proc = subprocess.run(
        [sys.executable, "-m", f"cpost.cli.{mod_name}", "--help"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        f"{mod_name} --help exited {proc.returncode}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip(), f"{mod_name}: --help produced no output"
