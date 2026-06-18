"""Tests for crawl-posts (Unit 7).

Serves local fixture HTML over http.server on an ephemeral 127.0.0.1 port and
points crawl-posts at it -- no external network. crawl-posts runs as a
subprocess so Scrapy's Twisted reactor stays fully isolated and captured stdout
is guaranteed free of Scrapy log noise.
"""

import pytest  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.subprocess]  # subprocess + embedded HTTP server; excluded from fast-run

import http.server  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from functools import partial  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

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


def test_extract_captures_nested_inline_text(server):
    """Unit 2: text nested in inline markup (<strong>, <a>) inside a <p> is captured.

    Article A has '<p>Nested <strong>BOLDWORD</strong> and <a ...>LINKWORD</a> inline.</p>'.
    With the old direct-text-only selector these nested words were dropped.
    """
    rc, out, err = _run_crawl(
        f"{server}/index.html", "--no-robots", "--item-regex", r"/news/a",
    )
    assert rc == 0, err
    items = _parse_ndjson(out)
    a = next(it for it in items if "news/a.html" in it["url"])
    assert "BOLDWORD" in a["text"], a["text"]
    assert "LINKWORD" in a["text"], a["text"]
    # No duplication: descendant ::text must not emit the same direct text twice.
    assert a["text"].count("BOLDWORD") == 1


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


def test_spider_base_settings_have_retry_and_autothrottle():
    """T1: Spider base settings enable retry, AutoThrottle, and safe limits."""
    from src.crawl_posts import BASE_SPIDER_SETTINGS

    s = BASE_SPIDER_SETTINGS
    assert s["RETRY_ENABLED"] is True
    assert s["RETRY_TIMES"] >= 1
    assert 502 in s["RETRY_HTTP_CODES"]
    assert 503 in s["RETRY_HTTP_CODES"]
    assert 504 in s["RETRY_HTTP_CODES"]
    assert 408 in s["RETRY_HTTP_CODES"]
    assert 429 in s["RETRY_HTTP_CODES"]

    assert s["AUTOTHROTTLE_ENABLED"] is True
    assert s["AUTOTHROTTLE_START_DELAY"] >= 0.1
    assert s["AUTOTHROTTLE_MAX_DELAY"] >= 1
    assert s["AUTOTHROTTLE_TARGET_CONCURRENCY"] >= 1

    assert s.get("DNSCACHE_ENABLED", True) is True
    assert s["DOWNLOAD_MAXSIZE"] > 0

    assert s["COOKIES_ENABLED"] is False
    assert s["TELNETCONSOLE_ENABLED"] is False


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


# -- Unit 2: live progress callback ----------------------------------------- #

def test_progress_cb_receives_increasing_snapshots(server):
    """crawl_items with progress_cb fires at least once with growing counts."""
    from src.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{server}/index.html"]
    opts["item_regex"] = r"/news/"
    opts["no_robots"] = True
    opts["limit"] = 30
    opts["max_pages"] = 200

    snaps = []

    def cb(snap):
        snaps.append(dict(snap))

    items = crawl_items(opts, progress_cb=cb)

    assert len(snaps) >= 1, "progress callback was never called"
    last = snaps[-1]
    assert last["responses"] > 0
    assert last["items"] > 0
    assert "last_url" in last
    for i in range(1, len(snaps)):
        assert snaps[i]["responses"] >= snaps[i - 1]["responses"]
        assert snaps[i]["items"] >= snaps[i - 1]["items"]

    items_no_cb = crawl_items(opts)
    assert len(items) == len(items_no_cb)
    for a, b in zip(items, items_no_cb):
        assert a["url"] == b["url"]


def test_progress_cb_none_behaves_identically(server):
    """progress_cb=None produces the same result as calling without kwarg."""
    from src.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{server}/index.html"]
    opts["item_regex"] = r"/news/"
    opts["no_robots"] = True
    opts["limit"] = 5

    items = crawl_items(opts)
    items_explicit = crawl_items(opts, progress_cb=None)
    assert len(items) == len(items_explicit)
    for a, b in zip(items, items_explicit):
        assert a["url"] == b["url"]


def test_progress_cb_does_not_pollute_stdout(server):
    """stdout (from subprocess) remains pure NDJSON when progress is enabled."""
    from src.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{server}/index.html"]
    opts["item_regex"] = r"/news/"
    opts["no_robots"] = True
    opts["limit"] = 5

    dummy_calls = []

    def dummy(snap):
        dummy_calls.append(snap)

    crawl_items(opts, progress_cb=dummy)
    assert len(dummy_calls) >= 1


def test_progress_cb_unreachable_host_still_external_error(server):
    """With progress_cb, an unreachable host still raises ExternalError."""
    from src.crawl_posts import CONFIG_DEFAULTS, crawl_items
    from core.errors import ExternalError

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"http://127.0.0.1:{dead_port}/index.html"]
    opts["timeout_sec"] = 5

    with pytest.raises(ExternalError):
        crawl_items(opts, progress_cb=lambda s: None)
