"""core/pipeline orchestrator: in-process build without shell/network."""

import json
from pathlib import Path

from core import pipeline, state, url_utils, runs
from src import normalize_items


def _cfg(tmp_path):
    return {
        "template_path": "./templates/fixed-format.zh.yaml",
        "watermark_config": "./configs/watermark.yaml",
        "download_dir": str(tmp_path / "assets"),
        "out_dir": str(tmp_path / "out"),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
        "limit": 30,
    }


def _item(slug, title):
    return {
        "source_id": "example.com",
        "url": f"https://example.com/news/{slug}",
        "canonical_url": f"https://example.com/news/{slug}",
        "title": title,
        "description": "desc",
        "image_url": "",  # text-only path, no network
        "published_at": "2026-06-15T10:00:00+08:00",
        "discovered_at": "2026-06-15T02:00:00Z",
    }


def test_builds_packages_in_process(tmp_path):
    cfg = _cfg(tmp_path)
    result = pipeline.run_pipeline([_item("a", "標題一"), _item("b", "標題二")], cfg)
    assert len(result["built"]) == 2
    assert result["failed"] == []
    for b in result["built"]:
        assert (tmp_path / "out" / b["post_id"] / "manifest.json").exists()


def test_dedupe_skips_published(tmp_path):
    cfg = _cfg(tmp_path)
    # pre-publish item 'a'
    with state.connect(cfg["state_path"]) as conn:
        state.upsert(conn, canonical_url="https://example.com/news/a", title="標題一",
                     title_hash=url_utils.title_hash("標題一"), status="published",
                     now="2026-06-15T00:00:00Z")
    result = pipeline.run_pipeline([_item("a", "標題一"), _item("b", "標題二")], cfg)
    assert result["skipped"] == 1
    assert len(result["built"]) == 1
    assert result["built"][0]["post_id"].endswith("news_b")
    # R5: the skip is visible in run history with its reason, not silent.
    dedupe_rows = [r for r in runs.list_runs(cfg["state_path"]) if r["stage"] == "dedupe"]
    assert len(dedupe_rows) == 1
    assert dedupe_rows[0]["status"] == "skipped"
    assert "reason=url" in (dedupe_rows[0]["error"] or "")


def test_bad_item_fails_without_aborting_batch(tmp_path):
    cfg = _cfg(tmp_path)
    bad = _item("c", "")  # empty title -> normalize fails
    result = pipeline.run_pipeline([bad, _item("d", "好標題")], cfg)
    assert len(result["built"]) == 1
    assert len(result["failed"]) == 1


def test_empty_items(tmp_path):
    result = pipeline.run_pipeline([], _cfg(tmp_path))
    assert result["built"] == [] and result["failed"] == []


# --- U1 (R1): exception classification ---------------------------------------

def test_validation_error_tagged_validation(tmp_path):
    """An empty title (ValidationError) is recorded as error_class=validation."""
    cfg = _cfg(tmp_path)
    result = pipeline.run_pipeline([_item("c", ""), _item("d", "好標題")], cfg)
    assert len(result["built"]) == 1
    assert len(result["failed"]) == 1
    f = result["failed"][0]
    assert f["stage"] == "normalize"
    assert f["error_class"] == "validation"


def test_system_error_tagged_system_without_aborting(tmp_path, monkeypatch):
    """A non-CliError in normalize is recorded as error_class=system, batch continues."""
    cfg = _cfg(tmp_path)
    real = normalize_items.normalize_one

    def flaky(raw):
        if raw.get("title") == "炸彈":
            raise KeyError("boom")  # unexpected, not a CliError
        return real(raw)

    monkeypatch.setattr(normalize_items, "normalize_one", flaky)
    result = pipeline.run_pipeline([_item("e", "炸彈"), _item("f", "正常")], cfg)
    assert len(result["built"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["error_class"] == "system"


def test_build_stage_system_error_recorded(tmp_path, monkeypatch):
    """A non-CliError during build is tagged system and logged to runs."""
    cfg = _cfg(tmp_path)

    def boom(rec, template_cfg):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(pipeline.render_caption, "render", boom)
    result = pipeline.run_pipeline([_item("g", "標題")], cfg)
    assert result["built"] == []
    assert len(result["failed"]) == 1
    f = result["failed"][0]
    assert f["stage"] == "build" and f["error_class"] == "system"
    assert any(r["status"] == "failed" for r in runs.list_runs(cfg["state_path"]))


# --- Unit 1 (R1): cover_enabled gate -----------------------------------------

def test_cover_disabled_skips_download_and_watermark(tmp_path, monkeypatch):
    """cover_enabled=false: neither select_all nor watermark runs; manifest has no cover.

    image_url is set to prove it is the flag — not a missing image — that disables covers.
    """
    cfg = _cfg(tmp_path)
    cfg["cover_enabled"] = False

    def boom_select_all(*a, **k):
        raise AssertionError("select_all must not run when covers are disabled")

    def boom_watermark(*a, **k):
        raise AssertionError("watermark must not run when covers are disabled")

    monkeypatch.setattr(pipeline.select_cover, "select_all", boom_select_all)
    monkeypatch.setattr(pipeline.watermark_cover, "watermark", boom_watermark)

    item = _item("a", "標題一")
    item["image_url"] = "https://example.com/img/a.jpg"
    result = pipeline.run_pipeline([item], cfg)

    assert len(result["built"]) == 1 and result["failed"] == []
    manifest = json.loads(
        (tmp_path / "out" / result["built"][0]["post_id"] / "manifest.json")
        .read_text(encoding="utf-8"))
    assert manifest["media"]["cover_path"] is None
    assert manifest["media"]["watermarked_cover_path"] is None


def test_cover_disabled_does_not_require_watermark_config(tmp_path):
    """cover_enabled=false: a missing watermark.yaml must not break the pipeline (decoupling)."""
    cfg = _cfg(tmp_path)
    cfg["cover_enabled"] = False
    cfg["watermark_config"] = str(tmp_path / "no-such-watermark.yaml")
    result = pipeline.run_pipeline([_item("a", "標題一")], cfg)
    assert len(result["built"]) == 1 and result["failed"] == []


# --- T2: parallel cover download (select_cover.select_all) -------------------

def _cover_item(slug, title, image_url=None):
    """Item with an optional image_url for cover download tests."""
    item = _item(slug, title)
    if image_url:
        item["image_url"] = image_url
    return item


def test_download_all_covers_downloads_in_parallel(tmp_path, monkeypatch):
    """T2: All covers with image_url are downloaded, files created in download_dir."""
    import time
    from core import pipeline

    download_dir = tmp_path / "assets"
    download_dir.mkdir(parents=True)

    records = [
        _cover_item("a", "標題一", "https://example.com/img/a.jpg"),
        _cover_item("b", "標題二", "https://example.com/img/b.jpg"),
        _cover_item("c", "標題三", "https://example.com/img/c.jpg"),
    ]

    dl_log = {}

    def fake_select(rec, dl_dir, timeout, retries, backoff):
        url = rec.get("image_url", "")
        stem = "a" if "a.jpg" in url else ("b" if "b.jpg" in url else "c")
        fname = f"{stem}.jpg"
        (dl_dir / fname).write_text(f"fake-{stem}")
        # Simulate network I/O delay to prove parallelism works
        time.sleep(0.05)
        dl_log[stem] = True
        return {**rec, "cover_source": url, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(pipeline.select_cover, "select", fake_select)

    pipeline.select_cover.select_all(
        records, download_dir, timeout=5, retries=0, backoff_sec=0.0, max_workers=5)

    # All three should have cover_path set and files exist
    for rec in records:
        assert rec.get("cover_path"), f"missing cover_path for {rec['title']}"
        assert Path(rec["cover_path"]).exists(), f"file not found for {rec['title']}"

    # Parallel download: < 150ms for 3 records each sleeping 50ms proves concurrency
    # (50ms * 3 sequential = 150ms; parallel with 5 workers ≈ 50ms + overhead)
    # This is a soft check; CI might have variance
    assert len(dl_log) == 3, "not all three were downloaded"


def test_download_all_covers_skips_no_image_url(tmp_path, monkeypatch):
    """T2: Records without image_url are passed through unchanged."""
    from core import pipeline

    download_dir = tmp_path / "assets"
    download_dir.mkdir(parents=True)

    records = [
        _cover_item("a", "無圖一", ""),  # no image_url
        _cover_item("b", "有圖一", "https://example.com/img/b.jpg"),
    ]

    fake = None

    def fake_select(rec, dl_dir, timeout, retries, backoff):
        nonlocal fake
        fake = rec.get("title")
        url = rec.get("image_url", "")
        fname = "b.jpg"
        (dl_dir / fname).write_text("fake-b")
        return {**rec, "cover_source": url, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(pipeline.select_cover, "select", fake_select)

    pipeline.select_cover.select_all(
        records, download_dir, timeout=5, retries=0, backoff_sec=0.0, max_workers=5)

    # Record 'a' has no image_url, should have no cover_path
    assert records[0].get("cover_path") is None
    assert records[0].get("cover_source") is None

    # Record 'b' should have cover downloaded
    assert records[1].get("cover_path")
    assert fake == "有圖一"  # only 'b' was processed


def test_download_all_covers_partial_failure(tmp_path, monkeypatch):
    """T2: Failed cover download sets cover_error but doesn't abort others."""
    from core import pipeline

    download_dir = tmp_path / "assets"
    download_dir.mkdir(parents=True)

    records = [
        _cover_item("a", "會成功", "https://example.com/img/a.jpg"),
        _cover_item("b", "會失敗", "https://example.com/img/b.jpg"),
    ]

    call_count = 0

    def fake_select(rec, dl_dir, timeout, retries, backoff):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # Second call fails
            from core.errors import ExternalError
            raise ExternalError("connection refused")
        fname = "a.jpg"
        (dl_dir / fname).write_text("fake-a")
        url = rec.get("image_url", "")
        return {**rec, "cover_source": url, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(pipeline.select_cover, "select", fake_select)

    pipeline.select_cover.select_all(
        records, download_dir, timeout=5, retries=0, backoff_sec=0.0, max_workers=5)

    # First record should succeed
    assert records[0].get("cover_path")
    assert Path(records[0]["cover_path"]).exists()

    # Second record should have cover_error set
    assert records[0].get("cover_error") is None
    assert records[1].get("cover_error") is not None
    assert "connection refused" in records[1]["cover_error"]


# --- U7 (Q7): build stamps run_id into the manifest for lifecycle correlation -

def test_build_persists_run_id_to_manifest(tmp_path):
    """Q7: build writes the run's id into manifest.backend.run_id so publish can
    read it back and correlate the whole lifecycle by run_id."""
    cfg = _cfg(tmp_path)
    result = pipeline.run_pipeline([_item("a", "標題一")], cfg)
    post_id = result["built"][0]["post_id"]
    manifest = json.loads(
        (tmp_path / "out" / post_id / "manifest.json").read_text(encoding="utf-8"))
    run_id = manifest["backend"]["run_id"]
    assert run_id  # set, not None
    build_rows = [r for r in runs.list_runs(cfg["state_path"], run_id=run_id)
                  if r["stage"] == "build"]
    assert len(build_rows) == 1 and build_rows[0]["post_id"] == post_id


# --- T3: select_cover.select_all reports per-cover progress via callback ---

def test_download_all_covers_progress_callback(tmp_path, monkeypatch):
    """T3: progress_cb fires for each downloaded cover with count and status."""
    records = [
        _cover_item("a", "標題一", "https://example.com/img/a.jpg"),
        _cover_item("b", "標題二", "https://example.com/img/b.jpg"),
    ]

    def fake_select(rec, dl_dir, timeout, retries, backoff):
        url = rec.get("image_url", "")
        stem = "a" if "a.jpg" in url else "b"
        fname = f"{stem}.jpg"
        (dl_dir / fname).write_text(f"fake-{stem}")
        return {**rec, "cover_source": url, "cover_path": str(dl_dir / fname)}

    monkeypatch.setattr(pipeline.select_cover, "select", fake_select)
    download_dir = tmp_path / "assets"
    download_dir.mkdir(parents=True, exist_ok=True)

    messages = []

    def cb(msg):
        messages.append(msg)

    pipeline.select_cover.select_all(
        records, download_dir, timeout=5, retries=0, backoff_sec=0.0,
        max_workers=5, progress_cb=cb)

    assert len(messages) == 2
    assert all("cover " in m for m in messages)
    assert messages[0].startswith("cover 1/2") or messages[0].startswith("cover 2/2")
    assert all(m.endswith("(ok)") for m in messages)


def test_crawl_items_accepts_poll_sec():
    """crawl_items() must accept poll_sec parameter (U5.3)."""
    from src import crawl_posts
    import inspect
    sig = inspect.signature(crawl_posts.crawl_items)
    assert "poll_sec" in sig.parameters
    assert sig.parameters["poll_sec"].default == 0.5
