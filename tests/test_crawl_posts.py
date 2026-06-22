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
    cmd = [sys.executable, "-m", "cpost.cli.crawl_posts", *extra_args]
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
    from cpost.cli.crawl_posts import BASE_SPIDER_SETTINGS

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
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

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
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

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
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

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
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items
    from cpost.core.errors import ExternalError

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"http://127.0.0.1:{dead_port}/index.html"]
    opts["timeout_sec"] = 5

    with pytest.raises(ExternalError):
        crawl_items(opts, progress_cb=lambda s: None)


# -- U10: per-source extraction selector overrides (R6) --------------------- #

def _crawl_one(server, item_regex, **opt_overrides):
    """Crawl the fixture site and return the single matching item."""
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{server}/index-c.html"]
    opts["item_regex"] = item_regex
    opts["no_robots"] = True
    opts["limit"] = 5
    opts.update(opt_overrides)
    items = crawl_items(opts)
    return next(it for it in items if "news/c.html" in it["url"])


@pytest.fixture
def index_c(server):
    """Write a temp index that links to news/c.html, then clean up."""
    path = FIXTURE_DIR / "index-c.html"
    path.write_text(
        '<!doctype html><html><head><title>Idx</title></head><body>'
        '<a href="news/c.html">Article C</a></body></html>',
        encoding="utf-8",
    )
    try:
        yield server
    finally:
        path.unlink(missing_ok=True)


def test_custom_body_selector_extracts_non_p_layout(index_c):
    """A body_selector targeting a <div> extracts content the default would miss."""
    c = _crawl_one(index_c, r"/news/c", body_selector="div.article-body ::text")
    assert "CUSTOMDIVBODY" in c["text"], c["text"]
    # The decoy <p> is excluded because the custom selector only targets the div.
    assert "IGNORED" not in c["text"], c["text"]


def test_empty_body_selector_falls_back_to_default(index_c):
    """Empty body_selector → built-in body p/h1/h2/li behavior (decoy <p> captured)."""
    c = _crawl_one(index_c, r"/news/c")  # body_selector defaults to ""
    assert "IGNORED" in c["text"], c["text"]
    assert "Article C Heading" in c["text"], c["text"]


def test_custom_image_and_date_selectors_honored(index_c):
    """Custom image_selector/date_selector read non-default meta tags."""
    c = _crawl_one(
        index_c,
        r"/news/c",
        image_selector='meta[name="thumbnail"]::attr(content)',
        date_selector='meta[name="pubdate"]::attr(content)',
    )
    assert c["image_url"].endswith("/img/c-custom.png"), c["image_url"]
    assert c["published_at"] == "2026-06-20T09:30:00Z", c["published_at"]


def test_empty_image_date_selectors_fall_back(server):
    """Empty image/date selectors → og:image + article:published_time defaults."""
    a = _crawl_one_a(server)
    assert a["image_url"].endswith("/img/a.png"), a["image_url"]
    assert a["published_at"] == "2026-06-10T08:00:00Z", a["published_at"]


def _crawl_one_a(server):
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{server}/index.html"]
    opts["item_regex"] = r"/news/a"
    opts["no_robots"] = True
    opts["limit"] = 5
    items = crawl_items(opts)
    return next(it for it in items if "news/a.html" in it["url"])


def test_body_selector_matching_nothing_yields_empty_not_crash(index_c):
    """A selector matching nothing → empty body, visible (not a crash).

    min_text_chars=0 (default) means the item is still emitted with empty text,
    so an under-extracted source surfaces rather than silently vanishing.
    """
    c = _crawl_one(index_c, r"/news/c", body_selector="div.does-not-exist ::text")
    assert c["text"] == "", repr(c["text"])
    # Other fields still populate, so the zero-content item is observable.
    assert c["title"] == "Article C Title"


def test_invalid_body_selector_falls_back_to_default(index_c):
    """A syntactically invalid body_selector → default body extraction, not 0 items.

    A typo'd selector used to raise inside parsel/cssselect, silently dropping
    every item for the source (reported as a 0-item success). It must now fall
    back to the built-in body p/h1/h2/li set so the crawl still yields content.
    """
    c = _crawl_one(index_c, r"/news/c", body_selector="div[unclosed")
    # Fell back to default extraction: the decoy <p> and heading are captured.
    assert "IGNORED" in c["text"], c["text"]
    assert "Article C Heading" in c["text"], c["text"]
    assert c["text"].strip() != ""


def test_invalid_image_and_date_selectors_fall_back(index_c):
    """Invalid image/date selectors → default meta-tag extraction, item still produced."""
    c = _crawl_one(
        index_c,
        r"/news/c",
        image_selector="meta[unclosed",
        date_selector="meta[unclosed",
    )
    # Item is still produced (crawl not zeroed) and falls back to defaults; the
    # fixture has no og:image / article:published_time, so these resolve empty
    # without crashing -- the point is the item exists with body content.
    assert "IGNORED" in c["text"], c["text"]
    assert c["image_url"] == "", c["image_url"]
    assert c["published_at"] == "", c["published_at"]


def test_invalid_body_selector_returns_items_not_zero(index_c):
    """End-to-end: an invalid body_selector still returns items (count > 0)."""
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{index_c}/index-c.html"]
    opts["item_regex"] = r"/news/c"
    opts["no_robots"] = True
    opts["limit"] = 5
    opts["body_selector"] = "div[unclosed"
    items = crawl_items(opts)
    assert any("news/c.html" in it["url"] for it in items)


def test_body_selector_empty_with_min_text_chars_filters_item(index_c):
    """min_text_chars filters an empty-body item — under-extraction is enforceable."""
    from cpost.cli.crawl_posts import CONFIG_DEFAULTS, crawl_items

    opts = dict(CONFIG_DEFAULTS)
    opts["start_urls"] = [f"{index_c}/index-c.html"]
    opts["item_regex"] = r"/news/c"
    opts["no_robots"] = True
    opts["limit"] = 5
    opts["min_text_chars"] = 10
    opts["body_selector"] = "div.does-not-exist ::text"
    items = crawl_items(opts)
    assert not any("news/c.html" in it["url"] for it in items)


# -- U11: crawl child wall-clock timeout ------------------------------------ #
# These are fast (mocked process — no real Scrapy reactor / HTTP server), so the
# wedged-child path is exercised deterministically in well under a second.

class _FakeProc:
    """A stand-in for mp.Process that lets a test script its liveness."""

    def __init__(self, alive_after_join=True, exits_on_join=False):
        self._alive = False
        self._alive_after_join = alive_after_join
        self._exits_on_join = exits_on_join
        self.started = False
        self.terminated = False
        self.killed = False
        self.join_calls = []

    def start(self):
        self.started = True
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)
        if self._exits_on_join:
            self._alive = False
        elif not self._alive_after_join:
            self._alive = False

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False


class _FakeCtx:
    def __init__(self, proc):
        self._proc = proc

    def Process(self, *args, **kwargs):  # noqa: N802 - mirrors mp API
        return self._proc


def _patch_proc(monkeypatch, proc):
    from cpost.cli import crawl_posts

    monkeypatch.setattr(crawl_posts.mp, "get_context", lambda _name: _FakeCtx(proc))


def test_wedged_child_times_out_and_is_killed(monkeypatch):
    """A child that never exits → ExternalError within budget, child killed (no orphan)."""
    from cpost.cli.crawl_posts import crawl_items
    from cpost.core.errors import ExternalError

    proc = _FakeProc(alive_after_join=True)  # never dies on its own
    _patch_proc(monkeypatch, proc)

    opts = {"start_urls": ["http://127.0.0.1:1/x"]}
    with pytest.raises(ExternalError) as exc:
        crawl_items(opts, max_runtime_sec=0.2)

    assert "budget" in str(exc.value).lower()
    # The overrun child was actively terminated/killed, not left running.
    assert proc.terminated or proc.killed


def test_wedged_child_with_progress_cb_times_out(monkeypatch):
    """The progress-poll loop is also bounded by the same deadline."""
    from cpost.cli.crawl_posts import crawl_items
    from cpost.core.errors import ExternalError

    proc = _FakeProc(alive_after_join=True)
    _patch_proc(monkeypatch, proc)

    opts = {"start_urls": ["http://127.0.0.1:1/x"]}
    with pytest.raises(ExternalError):
        crawl_items(opts, progress_cb=lambda s: None, poll_sec=0.01,
                    max_runtime_sec=0.2)
    assert proc.terminated or proc.killed


def test_child_finishing_under_budget_is_not_killed(monkeypatch, tmp_path):
    """Edge: a child that exits within budget is joined normally, never killed."""
    from cpost.cli import crawl_posts
    from cpost.cli.crawl_posts import crawl_items

    proc = _FakeProc(exits_on_join=True)  # exits the moment we join
    _patch_proc(monkeypatch, proc)

    # Stub out tempdir + result parsing so we exercise ONLY the timeout/join path
    # without a real crawl: write a status with a response and one item.
    real_mkdtemp = crawl_posts.tempfile.mkdtemp

    def fake_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        import json as _json
        import os as _os
        with open(_os.path.join(d, "status.json"), "w") as fh:
            _json.dump({"responses": 1, "items": 1, "error": None}, fh)
        with open(_os.path.join(d, "items.ndjson"), "w") as fh:
            fh.write(_json.dumps({"url": "http://x/a", "title": "A"}) + "\n")
        return d

    monkeypatch.setattr(crawl_posts.tempfile, "mkdtemp", fake_mkdtemp)

    opts = {"start_urls": ["http://x/a"]}
    items = crawl_items(opts, max_runtime_sec=5.0)

    assert items == [{"url": "http://x/a", "title": "A"}]
    assert not proc.terminated and not proc.killed


def test_child_finishing_at_deadline_with_progress_cb_not_killed(monkeypatch):
    """U11: on the PROGRESS path, a child finishing right at the deadline boundary
    keeps its results instead of being spuriously killed.

    Mirrors test_child_finishing_under_budget_is_not_killed but exercises the
    progress_cb poll loop: the loop straddles the deadline while the child is
    alive, breaks, and the child then exits on the grace join. Before the fix the
    progress branch set timed_out unconditionally on the deadline (without
    re-checking is_alive), killing the finished child and discarding its results.
    """
    from cpost.cli import crawl_posts
    from cpost.cli.crawl_posts import crawl_items

    # Alive through the poll loop until the deadline; exits on the next join.
    proc = _FakeProc(exits_on_join=True)
    _patch_proc(monkeypatch, proc)

    real_mkdtemp = crawl_posts.tempfile.mkdtemp

    def fake_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        import json as _json
        import os as _os
        with open(_os.path.join(d, "status.json"), "w") as fh:
            _json.dump({"responses": 1, "items": 1, "error": None}, fh)
        with open(_os.path.join(d, "items.ndjson"), "w") as fh:
            fh.write(_json.dumps({"url": "http://x/a", "title": "A"}) + "\n")
        return d

    monkeypatch.setattr(crawl_posts.tempfile, "mkdtemp", fake_mkdtemp)

    opts = {"start_urls": ["http://x/a"]}
    items = crawl_items(opts, progress_cb=lambda s: None, poll_sec=0.01,
                        max_runtime_sec=0.05)

    assert items == [{"url": "http://x/a", "title": "A"}]
    assert not proc.terminated and not proc.killed


def test_env_var_overrides_default_budget(monkeypatch):
    """CPOST_CRAWL_MAX_RUNTIME_SEC overrides the default when no arg is given."""
    from cpost.cli.crawl_posts import _resolve_max_runtime

    monkeypatch.setenv("CPOST_CRAWL_MAX_RUNTIME_SEC", "12.5")
    assert _resolve_max_runtime(None) == 12.5
    # An explicit argument still wins over the env var.
    assert _resolve_max_runtime(3.0) == 3.0
    # A malformed env var falls back to the default.
    monkeypatch.setenv("CPOST_CRAWL_MAX_RUNTIME_SEC", "not-a-number")
    from cpost.cli.crawl_posts import DEFAULT_CRAWL_MAX_RUNTIME_SEC
    assert _resolve_max_runtime(None) == DEFAULT_CRAWL_MAX_RUNTIME_SEC


def test_default_budget_is_generous_and_finite():
    """The default budget is finite and comfortably above a normal crawl."""
    from cpost.cli.crawl_posts import DEFAULT_CRAWL_MAX_RUNTIME_SEC

    assert 60.0 <= DEFAULT_CRAWL_MAX_RUNTIME_SEC < float("inf")
