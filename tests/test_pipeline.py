"""core/pipeline orchestrator: in-process build without shell/network."""

import json

import pytest

from core import pipeline, state, url_utils, runs
from core.errors import ValidationError
from src import normalize_items


def _cfg(tmp_path):
    return {
        "template_path": "./templates/fixed-format.zh.yaml",
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


def test_crawl_items_accepts_poll_sec():
    """crawl_items() must accept poll_sec parameter (U5.3)."""
    from src import crawl_posts
    import inspect
    sig = inspect.signature(crawl_posts.crawl_items)
    assert "poll_sec" in sig.parameters
    assert sig.parameters["poll_sec"].default == 0.5


# --- U2 (R2): crawl_all_sources enabled filtering + on_source reporting --------

def test_crawl_all_sources_two_sources_first_fails(monkeypatch):
    """Integration contract (test-first): two enabled sources, the first errors —
    the batch is not aborted, the second still crawls, and BOTH outcomes are
    reported via the new on_source callback (not progress_cb)."""
    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        if cfg["source_id"] == "bad":
            raise RuntimeError("boom")
        return [{"source_id": cfg["source_id"], "canonical_url": "https://good/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    reports: list[tuple] = []
    progress: list = []
    cfg = {"sources": [{"source_id": "bad", "start_url": "https://bad"},
                       {"source_id": "good", "start_url": "https://good"}]}
    items = pipeline.crawl_all_sources(
        cfg, progress_cb=progress.append, on_source=lambda sid, r: reports.append((sid, r)))
    assert [i["source_id"] for i in items] == ["good"]      # batch not aborted
    by_src = dict(reports)
    assert by_src["good"] == 1                              # success count
    assert isinstance(by_src["bad"], str) and "boom" in by_src["bad"]  # failure msg
    # The failure string must NOT leak onto progress_cb (arch F1).
    assert not any(isinstance(p, str) and "boom" in p for p in progress)


def test_crawl_all_sources_skips_disabled(monkeypatch):
    """A source with enabled:false is skipped entirely (not crawled, not reported)."""
    calls = []

    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        calls.append(cfg["source_id"])
        return [{"source_id": cfg["source_id"], "canonical_url": f"https://{cfg['source_id']}/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    reports = []
    cfg = {"sources": [{"source_id": "a", "start_url": "https://a", "enabled": False},
                       {"source_id": "b", "start_url": "https://b"}]}
    items = pipeline.crawl_all_sources(cfg, on_source=lambda sid, r: reports.append(sid))
    assert calls == ["b"]
    assert {i["source_id"] for i in items} == {"b"}
    assert reports == ["b"]


def test_crawl_all_sources_zero_vs_error_distinguishable(monkeypatch):
    """flow G3: all-enabled-return-zero vs all-error must be operator-distinguishable
    on on_source — zero yields int 0, error yields a message string."""
    def all_zero(cfg, progress_cb=None, poll_sec=0.5):
        return []

    monkeypatch.setattr(pipeline, "crawl_items", all_zero)
    zero_reports = []
    cfg = {"sources": [{"source_id": "a", "start_url": "https://a"},
                       {"source_id": "b", "start_url": "https://b"}]}
    pipeline.crawl_all_sources(cfg, on_source=lambda sid, r: zero_reports.append((sid, r)))
    assert all(r == 0 for _, r in zero_reports)

    def all_error(cfg, progress_cb=None, poll_sec=0.5):
        raise RuntimeError("down")

    monkeypatch.setattr(pipeline, "crawl_items", all_error)
    err_reports = []
    pipeline.crawl_all_sources(cfg, on_source=lambda sid, r: err_reports.append((sid, r)))
    assert all(isinstance(r, str) for _, r in err_reports)


# --- FIX 1: all-disabled + start_url falls back, callback isolation, bad shapes -

def test_crawl_all_sources_all_disabled_falls_back_to_start_url(monkeypatch):
    """All sources disabled but a start_url is set -> single-url fallback crawls
    that start_url (not a silent zero)."""
    crawled = []

    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        crawled.append(cfg["start_url"])
        return [{"source_id": "fallback", "canonical_url": "https://fb/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    cfg = {"start_url": "https://fallback.example/",
           "sources": [{"source_id": "a", "start_url": "https://a", "enabled": False},
                       {"source_id": "b", "start_url": "https://b", "enabled": False}]}
    items = pipeline.crawl_all_sources(cfg)
    assert crawled == ["https://fallback.example/"]  # start_url crawled, not skipped
    assert len(items) == 1


def test_crawl_all_sources_on_source_success_callback_raises_not_failed(monkeypatch):
    """A callback that raises on the SUCCESS path must not mislabel the source as
    failed, double-report, or abort the batch — the crawl already succeeded."""
    def fake_crawl_items(cfg, progress_cb=None, poll_sec=0.5):
        return [{"source_id": cfg["source_id"], "canonical_url": "https://x/1"}]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl_items)
    reports: list[tuple] = []

    def on_source(sid, r):
        reports.append((sid, r))
        raise RuntimeError("callback boom")  # raised AFTER the successful crawl

    cfg = {"sources": [{"source_id": "a", "start_url": "https://a"},
                       {"source_id": "b", "start_url": "https://b"}]}
    items = pipeline.crawl_all_sources(cfg, on_source=on_source)
    assert len(items) == 2  # batch completed, not aborted
    # Each source reported exactly once, as a success int — never "failed: ...".
    assert [sid for sid, _ in reports] == ["a", "b"]
    assert all(r == 1 for _, r in reports)
    assert not any(isinstance(r, str) for _, r in reports)


def test_crawl_all_sources_non_list_sources_raises(monkeypatch):
    monkeypatch.setattr(pipeline, "crawl_items",
                        lambda cfg, progress_cb=None, poll_sec=0.5: [])
    with pytest.raises(ValidationError):
        pipeline.crawl_all_sources({"sources": "not-a-list"})


def test_crawl_all_sources_non_dict_entry_raises(monkeypatch):
    monkeypatch.setattr(pipeline, "crawl_items",
                        lambda cfg, progress_cb=None, poll_sec=0.5: [])
    with pytest.raises(ValidationError):
        pipeline.crawl_all_sources({"sources": ["not-a-dict"]})
