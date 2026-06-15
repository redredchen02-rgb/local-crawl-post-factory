"""Tests for crawl-posts (Unit 7).

Serves local fixture HTML over http.server on an ephemeral 127.0.0.1 port and
points crawl-posts at it -- no external network. crawl-posts runs as a
subprocess so Scrapy's Twisted reactor stays fully isolated and captured stdout
is guaranteed free of Scrapy log noise.
"""

import http.server
import json
import socket
import subprocess
import sys
import threading
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "site"


@pytest.fixture
def server():
    """Threaded HTTP server over the fixture site; yields the base URL."""
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _run_crawl(*extra_args):
    """Invoke crawl-posts as a subprocess; return (returncode, stdout, stderr)."""
    cmd = [sys.executable, "-m", "src.crawl_posts", *extra_args]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120
    )
    return proc.returncode, proc.stdout, proc.stderr


def _parse_ndjson(stdout):
    """Parse every stdout line as JSON; fail loudly on any non-JSON line."""
    items = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        items.append(json.loads(line))  # raises if Scrapy logs leaked
    return items


def test_happy_path_crawls_news_excludes_login(server):
    rc, out, err = _run_crawl(
        f"{server}/index.html",
        "--no-robots",
        "--item-regex",
        r"/news/",
        "--deny-regex",
        r"login",
    )
    assert rc == 0, err
    items = _parse_ndjson(out)
    urls = {it["url"] for it in items}
    assert any("news/a.html" in u for u in urls)
    assert any("news/b.html" in u for u in urls)
    assert not any("login" in u for u in urls)
    # Schema fields present.
    for it in items:
        for field in (
            "source_id",
            "url",
            "canonical_url",
            "title",
            "description",
            "image_url",
            "published_at",
            "text",
            "discovered_at",
        ):
            assert field in it


def test_limit_caps_items(server):
    rc, out, err = _run_crawl(
        f"{server}/index.html",
        "--no-robots",
        "--item-regex",
        r"/news/",
        "--limit",
        "1",
    )
    assert rc == 0, err
    items = _parse_ndjson(out)
    assert len(items) <= 1


def test_stdout_is_pure_ndjson(server):
    rc, out, err = _run_crawl(
        f"{server}/index.html", "--no-robots", "--item-regex", r"/news/"
    )
    assert rc == 0, err
    # Every non-empty stdout line must parse as a JSON object.
    for line in out.splitlines():
        if not line.strip():
            continue
        assert isinstance(json.loads(line), dict)


def test_unreachable_host_exits_4():
    # Grab an ephemeral port then close it so nothing is listening.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    rc, out, err = _run_crawl(
        f"http://127.0.0.1:{dead_port}/index.html",
        "--no-robots",
        "--timeout-sec",
        "5",
    )
    assert rc == 4, f"rc={rc} out={out!r} err={err!r}"
    assert out.strip() == ""
    assert err.strip() != ""
