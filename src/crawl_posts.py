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

from core import cli
from core.errors import DependencyError, ExternalError, ValidationError
from core.io_ndjson import write_line
from core.url_utils import host_of, normalize_url

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
    "user_agent": "crawl-posts/1.0 (+local-crawl-post-factory)",
    "timeout_sec": 30,
    "concurrency": 8,
    "download_delay": 0.0,
    "no_robots": False,
}

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
                    if (
                        len(item["text"]) >= opts["min_text_chars"]
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
                image_url = (
                    response.css('meta[property="og:image"]::attr(content)').get() or ""
                ).strip()
                if image_url:
                    image_url = response.urljoin(image_url)
                published_at = (
                    response.css(
                        'meta[property="article:published_time"]::attr(content)'
                    ).get()
                    or response.css('meta[name="date"]::attr(content)').get()
                    or ""
                ).strip()
                text_parts = response.css(
                    "body p::text, body h1::text, body h2::text, body li::text"
                ).getall()
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
        out_file.flush()
        out_file.close()
    except ImportError as exc:  # pragma: no cover - scrapy missing
        status["error"] = f"__dependency__: {exc}"
    except Exception as exc:  # noqa: BLE001
        status["error"] = repr(exc)
    finally:
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


def crawl_items(opts: dict, progress_cb=None, poll_sec: float = 0.5) -> list:
    """Run the crawl in a fresh child process and return the items as a list.

    Shared by the CLI (`_run`) and the in-process pipeline orchestrator so both
    use one crawl implementation. Same contracts: DependencyError if Scrapy is
    missing, ExternalError if the site is unreachable/timed out.

    When *progress_cb* is given, the parent polls a side-channel progress file
    ~every 0.5 s while the child is alive and calls ``progress_cb(snapshot)``
    with ``{responses, items, last_url, last_title}``.
    """
    try:
        import scrapy  # noqa: F401
    except ImportError as exc:
        raise DependencyError(f"Scrapy is not installed: {exc}")

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

    if progress_cb:
        assert progress_path is not None
        _last_progress = None
        while proc.is_alive():
            snap = _read_progress(progress_path)
            if snap is not None and snap != _last_progress:
                _last_progress = snap
                progress_cb(snap)
            time.sleep(poll_sec)
        # One final read in case the child wrote progress between the last
        # poll and process exit.
        snap = _read_progress(progress_path)
        if snap is not None and snap != _last_progress:
            progress_cb(snap)

    proc.join()

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
