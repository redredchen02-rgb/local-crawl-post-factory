"""crawl-posts: same-site crawler emitting crawled-item NDJSON (origin §4.1, §11.1, R1/R2/R10).

Crawls one or more start URLs (same host only), treats pages matching
``--item-regex`` as content pages, and emits one crawled-item JSON object per
line to stdout. Nothing else may touch stdout.

HARD CONTRACTS:
  - stdout carries ONLY NDJSON items. ALL Scrapy logging/noise goes to stderr.
  - this command MUST NOT write any SQLite/state (R10).
  - unreachable site / timeout / DNS failure -> ExternalError (exit 4).
  - Scrapy not installed -> DependencyError (exit 3).

Scrapy's Twisted reactor cannot be restarted within a single process, so each
invocation runs the crawl in a *fresh child process* (multiprocessing spawn).
The child writes items to a temp NDJSON file and logs only to stderr; the parent
relays the file to real stdout. This keeps stdout pristine regardless of what
Scrapy prints.
"""

import argparse
import json
import multiprocessing as mp
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

from cpost.core import cli
from cpost.core.errors import DependencyError, ExternalError, ValidationError
from cpost.core.io_ndjson import write_line
from cpost.core.url_utils import host_of, normalize_url

CONFIG_DEFAULTS = {
    "item_regex": "",
    "allow_regex": "",
    "deny_regex": "",
    "max_pages": 200,
    "limit": 50,
    "depth": 3,
    "min_text_chars": 0,
    "max_text_chars": 20000,
    "source_id": "",
    # Per-source extraction overrides (empty = use built-in hardcoded fallback).
    # U10 consumes these; the keys must exist now so the CLI path never KeyErrors.
    "body_selector": "",
    "image_selector": "",
    "date_selector": "",
    "user_agent": "crawl-posts/1.0 (+local-crawl-post-factory)",
    "timeout_sec": 30,
    "concurrency": 8,
    "download_delay": 0.0,
    "no_robots": False,
}

# Hard wall-clock ceiling for a single crawl child process (U11/R5). A wedged
# Scrapy/Twisted reactor can otherwise hang the caller -- and the WebUI worker
# thread -- forever. On overrun the child is terminated/killed and the call
# raises ExternalError. Overridable per-call (``max_runtime_sec``) or globally
# via the CPOST_CRAWL_MAX_RUNTIME_SEC env var. Default a few minutes: well above
# any normal same-site crawl bounded by max_pages/limit, but finite.
DEFAULT_CRAWL_MAX_RUNTIME_SEC = 300.0


def _resolve_max_runtime(max_runtime_sec: float | None) -> float:
    """Resolve the wall-clock budget: explicit arg > env var > default."""
    if max_runtime_sec is not None:
        return float(max_runtime_sec)
    env = os.environ.get("CPOST_CRAWL_MAX_RUNTIME_SEC")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return DEFAULT_CRAWL_MAX_RUNTIME_SEC


BASE_SPIDER_SETTINGS = {
    "RETRY_ENABLED": True,
    "RETRY_TIMES": 2,
    "RETRY_HTTP_CODES": [502, 503, 504, 408, 429],
    "AUTOTHROTTLE_ENABLED": True,
    "AUTOTHROTTLE_START_DELAY": 0.5,
    "AUTOTHROTTLE_MAX_DELAY": 5.0,
    "AUTOTHROTTLE_TARGET_CONCURRENCY": 4.0,
    "DNSCACHE_ENABLED": True,
    "DOWNLOAD_MAXSIZE": 5 * 1024 * 1024,
    "COOKIES_ENABLED": False,
    "TELNETCONSOLE_ENABLED": False,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_progress(progress_path: str) -> dict | None:
    """Read a progress snapshot from *progress_path*.

    Returns ``None`` when the file does not exist or contains invalid JSON
    (the parent skips that poll cycle instead of crashing).
    """
    try:
        with open(progress_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _kill_child(proc, grace_sec: float = 2.0) -> None:
    """Stop a crawl child that overran its budget, leaving no orphan.

    Sends SIGTERM first, waits a short grace period, then SIGKILLs if the child
    is still alive. Always joins so the process table is cleaned up.
    """
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=grace_sec)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=grace_sec)


# --------------------------------------------------------------------------- #
# Child-process crawl worker (runs in a fresh interpreter via spawn).
# --------------------------------------------------------------------------- #
def _crawl_worker(opts: dict, out_path: str, status_path: str,
                  progress_path: str | None = None) -> None:
    """Run the Scrapy crawl. Writes items to ``out_path`` (NDJSON).

    Writes a small JSON status file to ``status_path`` describing whether any
    response was received and any fatal error, so the parent can map failures
    to the right exit code. All Scrapy logging is forced to stderr.

    When *progress_path* is given, the worker also writes a live progress
    snapshot (``{responses, items, last_url, last_title}``) atomically after
    each response so the parent can poll for real-time status.
    """
    status: dict[str, int | str | None] = {"responses": 0, "items": 0, "error": None}

    def _write_progress():
        if not progress_path:
            return
        tmp = progress_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False)
            os.replace(tmp, progress_path)
        except OSError:
            pass
    try:
        import logging

        from scrapy import Spider
        from scrapy.crawler import CrawlerProcess
        from scrapy.utils.log import configure_logging

        # Force every log handler onto stderr; nothing may reach stdout.
        configure_logging(install_root_handler=False)
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
        root.setLevel(logging.INFO)

        item_re = re.compile(opts["item_regex"]) if opts["item_regex"] else None
        allow_re = re.compile(opts["allow_regex"]) if opts["allow_regex"] else None
        deny_re = re.compile(opts["deny_regex"]) if opts["deny_regex"] else None
        start_urls = opts["start_urls"]
        allowed_hosts = {host_of(u) for u in start_urls}
        out_file = open(out_path, "w", encoding="utf-8")

        class _Spider(Spider):
            name = "crawl_posts"
            custom_settings: Any = {
                **BASE_SPIDER_SETTINGS,
                "USER_AGENT": opts["user_agent"],
                "ROBOTSTXT_OBEY": not opts["no_robots"],
                "DOWNLOAD_TIMEOUT": opts["timeout_sec"],
                "CONCURRENT_REQUESTS": opts["concurrency"],
                "DOWNLOAD_DELAY": opts.get("download_delay", 0.0),
                "DEPTH_LIMIT": opts["depth"],
                "LOG_ENABLED": True,
            }

            async def start(self):
                from scrapy import Request

                for u in start_urls:
                    yield Request(u, callback=self.parse, errback=self.on_error)

            def on_error(self, failure):
                status["error"] = repr(failure.value)

            def parse(self, response):
                status["responses"] += 1
                status["last_url"] = response.url
                title = (response.css("title::text").get() or "").strip()
                if not title:
                    title = (response.css("h1::text").get() or "").strip()
                status["last_title"] = title

                # Only handle HTML responses.
                content_type = response.headers.get("Content-Type", b"").decode(
                    "latin-1", "ignore"
                )
                is_html = "html" in content_type.lower() or content_type == ""
                if not is_html:
                    _write_progress()
                    return

                url = response.url
                emit = item_re.search(url) if item_re else True
                if deny_re and deny_re.search(url):
                    emit = False
                if emit and status["items"] < opts["limit"]:
                    item = self._extract(response)
                    # Defense in depth (U7): never emit a title-less item. A page
                    # with no <title>/<h1> would be quarantined by normalize-items
                    # anyway, so skip it at the source instead of writing a record
                    # downstream is guaranteed to reject.
                    if (
                        item["title"]
                        and len(item["text"]) >= opts["min_text_chars"]
                    ):
                        out_file.write(
                            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                        )
                        status["items"] += 1

                _write_progress()

                if status["items"] >= opts["limit"]:
                    return

                # Follow same-host links.
                for href in response.css("a::attr(href)").getall():
                    nxt = response.urljoin(href)
                    if host_of(nxt) not in allowed_hosts:
                        continue
                    if deny_re and deny_re.search(nxt):
                        continue
                    if allow_re and not allow_re.search(nxt) and not (
                        item_re and item_re.search(nxt)
                    ):
                        continue
                    from scrapy import Request

                    yield Request(nxt, callback=self.parse, errback=self.on_error)

            def _extract(self, response):
                title = (response.css("title::text").get() or "").strip()
                if not title:
                    title = (response.css("h1::text").get() or "").strip()
                description = (
                    response.css('meta[name="description"]::attr(content)').get()
                    or response.css(
                        'meta[property="og:description"]::attr(content)'
                    ).get()
                    or ""
                ).strip()
                # Per-source overrides (R6) arrive as plain-data opts keys so they
                # pickle cleanly into this spawned subprocess. An empty string means
                # "use the built-in hardcoded selector" (backward-compatible fallback).
                # A syntactically invalid custom selector raises in parsel/cssselect
                # (ValueError / SelectorSyntaxError); rather than letting one typo
                # zero the whole crawl, fall back to the default extraction per field.
                image_sel = opts.get("image_selector") or ""
                date_sel = opts.get("date_selector") or ""
                body_sel = opts.get("body_selector") or ""

                # Sentinel for "the custom selector was syntactically invalid, so
                # use the default" -- distinct from "valid selector matched nothing"
                # (which keeps the empty custom result, preserving R6 semantics).
                _INVALID = object()

                def _css_get(selector: str):
                    try:
                        return response.css(selector).get()
                    except Exception:  # noqa: BLE001 - invalid CSS -> fall back
                        return _INVALID

                def _css_getall(selector: str):
                    try:
                        return response.css(selector).getall()
                    except Exception:  # noqa: BLE001 - invalid CSS -> fall back
                        return _INVALID

                image_url = _css_get(image_sel) if image_sel else _INVALID
                if image_url is _INVALID:
                    image_url = (
                        response.css('meta[property="og:image"]::attr(content)').get()
                        or ""
                    )
                image_url = (image_url or "").strip()
                if image_url:
                    image_url = response.urljoin(image_url)
                published_at = _css_get(date_sel) if date_sel else _INVALID
                if published_at is _INVALID:
                    published_at = (
                        response.css(
                            'meta[property="article:published_time"]::attr(content)'
                        ).get()
                        or response.css('meta[name="date"]::attr(content)').get()
                        or ""
                    )
                published_at = (published_at or "").strip()
                # Descendant ``::text`` (note the space) captures both a tag's direct
                # text AND text nested in inline markup -- <strong>, <a>, <em> -- so
                # inline-formatted words inside a paragraph are no longer dropped. A
                # per-source body_selector overrides the built-in body p/h1/h2/li set.
                default_body_css = (
                    "body p ::text, body h1 ::text, body h2 ::text, body li ::text"
                )
                text_parts = _css_getall(body_sel) if body_sel else _INVALID
                if text_parts is _INVALID:
                    text_parts = response.css(default_body_css).getall()
                text = " ".join(t.strip() for t in text_parts if t.strip())
                text = re.sub(r"\s+", " ", text).strip()
                if opts["max_text_chars"] and len(text) > opts["max_text_chars"]:
                    text = text[: opts["max_text_chars"]]
                canonical = (
                    response.css('link[rel="canonical"]::attr(href)').get()
                    or response.url
                )
                return {
                    "source_id": opts["source_id"],
                    "url": response.url,
                    "canonical_url": normalize_url(
                        response.urljoin(canonical) if canonical else response.url
                    ),
                    "title": title,
                    "description": description,
                    "image_url": image_url,
                    "published_at": published_at,
                    "text": text,
                    "discovered_at": _utcnow_iso(),
                }

        process = CrawlerProcess(
            settings={
                "LOG_ENABLED": True,
                "TELNETCONSOLE_ENABLED": False,
                "CLOSESPIDER_PAGECOUNT": opts["max_pages"],
                "CLOSESPIDER_ITEMCOUNT": 0,
            }
        )
        process.crawl(_Spider)
        process.start()
    except ImportError as exc:  # pragma: no cover - scrapy missing
        status["error"] = f"__dependency__: {exc}"
    except Exception as exc:  # noqa: BLE001
        status["error"] = repr(exc)
    finally:
        if "out_file" in dir() and out_file and not out_file.closed:
            out_file.flush()
            out_file.close()
        with open(status_path, "w", encoding="utf-8") as fh:
            json.dump(status, fh)


def _run(args) -> int:
    # Resolve start URLs.
    start_urls = list(args.urls or [])
    if args.stdin:
        start_urls.extend(line.strip() for line in sys.stdin if line.strip())
    if not start_urls:
        raise ValidationError("no start URLs provided (pass positional URLs or --stdin)")

    try:
        import scrapy  # noqa: F401
    except ImportError as exc:
        raise DependencyError(f"Scrapy is not installed: {exc}")

    opts = dict(CONFIG_DEFAULTS)
    # Apply config file if present.
    cfg_path = args.config
    if cfg_path and os.path.exists(cfg_path):
        import yaml

        with open(cfg_path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        for k, v in loaded.items():
            if k in opts and v is not None:
                opts[k] = v
    # CLI overrides.
    cli_map = {
        "item_regex": args.item_regex,
        "allow_regex": args.allow_regex,
        "deny_regex": args.deny_regex,
        "max_pages": args.max_pages,
        "limit": args.limit,
        "depth": args.depth,
        "min_text_chars": args.min_text_chars,
        "max_text_chars": args.max_text_chars,
        "source_id": args.source_id,
        "user_agent": args.user_agent,
        "timeout_sec": args.timeout_sec,
        "concurrency": args.concurrency,
    }
    for k, v in cli_map.items():
        if v is not None:
            opts[k] = v
    if args.no_robots:
        opts["no_robots"] = True
    opts["start_urls"] = start_urls

    for item in crawl_items(opts):
        write_line(item)
    return 0


def crawl_items(opts: dict, progress_cb=None, poll_sec: float = 0.5,
                max_runtime_sec: float | None = None) -> list:
    """Run the crawl in a fresh child process and return the items as a list.

    Shared by the CLI (`_run`) and the in-process pipeline orchestrator so both
    use one crawl implementation. Same contracts: DependencyError if Scrapy is
    missing, ExternalError if the site is unreachable/timed out.

    When *progress_cb* is given, the parent polls a side-channel progress file
    ~every 0.5 s while the child is alive and calls ``progress_cb(snapshot)``
    with ``{responses, items, last_url, last_title}``.

    The child is bounded by a wall-clock budget (``max_runtime_sec``, default
    :data:`DEFAULT_CRAWL_MAX_RUNTIME_SEC`, env-overridable via
    ``CPOST_CRAWL_MAX_RUNTIME_SEC``). On overrun the child is terminated/killed
    and the call raises :class:`ExternalError`, so a wedged Twisted reactor can
    never hang the caller (or the WebUI worker thread) forever (U11/R5).
    """
    try:
        import scrapy  # noqa: F401
    except ImportError as exc:
        raise DependencyError(f"Scrapy is not installed: {exc}")

    budget = _resolve_max_runtime(max_runtime_sec)
    deadline = time.monotonic() + budget

    tmpdir = tempfile.mkdtemp(prefix="crawl_posts_")
    out_path = os.path.join(tmpdir, "items.ndjson")
    status_path = os.path.join(tmpdir, "status.json")
    progress_path = os.path.join(tmpdir, "progress.json") if progress_cb else None

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_crawl_worker,
        args=(opts, out_path, status_path, progress_path),
    )
    proc.start()

    timed_out = False
    if progress_cb:
        assert progress_path is not None
        _last_progress = None
        while proc.is_alive():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            snap = _read_progress(progress_path)
            if snap is not None and snap != _last_progress:
                _last_progress = snap
                progress_cb(snap)
            time.sleep(poll_sec)
        # One final read in case the child wrote progress between the last
        # poll and process exit.
        if not timed_out:
            snap = _read_progress(progress_path)
            if snap is not None and snap != _last_progress:
                progress_cb(snap)

    if not timed_out:
        # Bound the blocking join by whatever budget remains; if the child is
        # still alive afterwards it has overrun.
        remaining = max(0.0, deadline - time.monotonic())
        proc.join(timeout=remaining)
        if proc.is_alive():
            timed_out = True

    if timed_out:
        _kill_child(proc)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise ExternalError(
            f"crawl failed: exceeded wall-clock budget of {budget:g}s "
            "(child terminated)"
        )

    try:
        status = {"responses": 0, "items": 0, "error": None}
        if os.path.exists(status_path):
            with open(status_path, encoding="utf-8") as fh:
                try:
                    status = json.load(fh)
                except json.JSONDecodeError:
                    pass

        err = status.get("error")
        if err and str(err).startswith("__dependency__"):
            raise DependencyError(str(err))

        if status.get("responses", 0) == 0:
            detail = err or "no response from any start URL"
            raise ExternalError(f"crawl failed: site unreachable or timed out ({detail})")

        items = []
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        items.append(json.loads(line))
        return items
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="crawl-posts",
        description="Same-site crawler emitting crawled-item NDJSON to stdout.",
    )
    parser.add_argument("urls", nargs="*", help="one or more start URLs")
    parser.add_argument("--stdin", action="store_true", help="read start URLs from stdin")
    parser.add_argument("--config", default="configs/crawler.yaml", help="defaults YAML")
    parser.add_argument("--item-regex", dest="item_regex")
    parser.add_argument("--allow-regex", dest="allow_regex")
    parser.add_argument("--deny-regex", dest="deny_regex")
    parser.add_argument("--max-pages", dest="max_pages", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--depth", type=int)
    parser.add_argument("--min-text-chars", dest="min_text_chars", type=int)
    parser.add_argument("--max-text-chars", dest="max_text_chars", type=int)
    parser.add_argument("--source-id", dest="source_id")
    parser.add_argument("--user-agent", dest="user_agent")
    parser.add_argument("--timeout-sec", dest="timeout_sec", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--no-robots", dest="no_robots", action="store_true")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
