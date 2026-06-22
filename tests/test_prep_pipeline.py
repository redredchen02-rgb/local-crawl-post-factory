"""cpost.core.scoop_pipeline.run_prep_pipeline: today-prep orchestration in-process.

Crawl is stubbed (no subprocess/network); asserts the crawl→ingest→cluster→score
chain lands ranked scoops in the library and survives bad items / reruns.
"""

from cpost.core import library, scoop_pipeline


def _raw(slug, title, source_id="src_a", text=None,
         published="2026-06-15T10:00:00+08:00"):
    return {
        "source_id": source_id,
        "url": f"https://{source_id}.example.com/news/{slug}",
        "canonical_url": f"https://{source_id}.example.com/news/{slug}",
        "title": title,
        "text": text if text is not None else ("內容段落敘述。" * 80),
        "description": "desc",
        "published_at": published,
        "discovered_at": "2026-06-15T02:00:00Z",
    }


def _cfg(tmp_path):
    return {"state_path": str(tmp_path / "state.sqlite"), "scoring_config": None}


def _patch_crawl(monkeypatch, items):
    monkeypatch.setattr(scoop_pipeline, "crawl_all_sources",
                        lambda cfg, progress_cb=None, on_source=None: list(items))


def test_prep_produces_scored_scoops(tmp_path, monkeypatch):
    same = "藝人A被爆新戀情震驚全網一夜洗版"
    _patch_crawl(monkeypatch, [
        _raw("a1", same, source_id="src_a"),
        _raw("b1", same, source_id="src_b"),            # same event, 2nd source
        _raw("c1", "某科技公司季度財報大幅下滑引發拋售", source_id="src_a"),
    ])
    result = scoop_pipeline.run_prep_pipeline(_cfg(tmp_path))
    assert result["ingested"] == 3
    assert result["clusters"] >= 2
    assert result["scored"] == result["clusters"]
    scores = [t["score"] for t in result["top"]]
    assert scores == sorted(scores, reverse=True)       # top sorted by score desc
    assert any(t["source_count"] == 2 for t in result["top"])  # cross-source scoop


def test_single_source_flagged(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch, [
        _raw("a1", "事件一的標題敘述內容", source_id="only"),
        _raw("a2", "事件二完全不同的標題", source_id="only"),
    ])
    result = scoop_pipeline.run_prep_pipeline(_cfg(tmp_path))
    assert result["single_source"] is True
    assert all(t["source_count"] == 1 for t in result["top"])


def test_generator_consumed_writes_library(tmp_path, monkeypatch):
    # Regression guard: ingest() is a generator; if it isn't consumed the
    # library stays empty and clusters=0.
    _patch_crawl(monkeypatch, [_raw("a1", "唯一一則新聞的標題")])
    cfg = _cfg(tmp_path)
    scoop_pipeline.run_prep_pipeline(cfg)
    with library.connect(cfg["state_path"]) as conn:
        assert library.count(conn) == 1


def test_empty_crawl_no_error(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch, [])
    result = scoop_pipeline.run_prep_pipeline(_cfg(tmp_path))
    assert result == {"ingested": 0, "clusters": 0, "scored": 0,
                      "single_source": False, "top": [], "failed": []}


def test_prep_threads_on_source(tmp_path, monkeypatch):
    """flow G3: run_prep_pipeline must thread on_source into crawl_all_sources so
    the /today path surfaces per-source failures instead of swallowing them."""
    def fake_all_sources(cfg, progress_cb=None, on_source=None):
        if on_source:
            on_source("src_x", "failed: boom")
        return []

    monkeypatch.setattr(scoop_pipeline, "crawl_all_sources", fake_all_sources)
    seen = []
    result = scoop_pipeline.run_prep_pipeline(
        _cfg(tmp_path), on_source=lambda sid, r: seen.append((sid, r)))
    assert seen == [("src_x", "failed: boom")]
    assert result["ingested"] == 0  # clean finish on empty crawl


def test_crawl_snapshots_go_to_crawl_cb_not_progress(tmp_path, monkeypatch):
    """U18: the dict-shaped crawl telemetry must travel on crawl_progress_cb;
    progress_cb (the string log) must never receive a dict.

    crawl_all_sources threads progress_cb into crawl_items, which fires it with
    {responses, items, last_url, last_title} dict snapshots. Routing that through
    the job-log callback would stringify dicts into job.progress.
    """
    snap = {"responses": 3, "items": 2, "last_url": "u", "last_title": "標題甲"}

    def fake_all_sources(cfg, progress_cb=None, on_source=None):
        if progress_cb:
            progress_cb(snap)            # crawl telemetry is a dict
        return []

    monkeypatch.setattr(scoop_pipeline, "crawl_all_sources", fake_all_sources)
    crawl_seen: list = []
    progress_seen: list = []
    scoop_pipeline.run_prep_pipeline(
        _cfg(tmp_path),
        progress_cb=progress_seen.append,
        crawl_progress_cb=crawl_seen.append)

    assert crawl_seen == [snap]                         # dict landed on crawl cb
    assert all(isinstance(m, str) for m in progress_seen)  # log stays human-readable
    assert snap not in progress_seen                    # no raw dict in the log
    assert all("responses" not in m for m in progress_seen)  # no stringified dict


def test_stage_reports_still_strings(tmp_path, monkeypatch):
    """Happy path: stage reports keep flowing to progress_cb as plain strings."""
    _patch_crawl(monkeypatch, [_raw("a1", "正常的一則新聞標題")])
    progress_seen: list = []
    scoop_pipeline.run_prep_pipeline(
        _cfg(tmp_path), progress_cb=progress_seen.append)
    assert progress_seen                                # stage reports emitted
    assert all(isinstance(m, str) for m in progress_seen)
    assert any("爬取完成" in m for m in progress_seen)


def test_no_crawl_cb_omits_telemetry_but_keeps_stage_reports(tmp_path, monkeypatch):
    """crawl_progress_cb=None -> crawl_all_sources gets progress_cb=None (no live
    telemetry), yet the string stage reports still flow."""
    received = {}

    def fake_all_sources(cfg, progress_cb=None, on_source=None):
        received["progress_cb"] = progress_cb
        return []

    monkeypatch.setattr(scoop_pipeline, "crawl_all_sources", fake_all_sources)
    progress_seen: list = []
    scoop_pipeline.run_prep_pipeline(
        _cfg(tmp_path), progress_cb=progress_seen.append)
    assert received["progress_cb"] is None              # crawl phase silent
    assert any("爬取完成" in m for m in progress_seen)   # stage reports still flow


def test_bad_item_isolated(tmp_path, monkeypatch):
    _patch_crawl(monkeypatch, [
        _raw("good", "正常的一則新聞標題"),
        _raw("bad", ""),                                # empty title -> raises
    ])
    result = scoop_pipeline.run_prep_pipeline(_cfg(tmp_path))
    assert result["ingested"] == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["stage"] == "normalize"


def test_prep_result_declared_keys(tmp_path, monkeypatch):
    """R7: run_prep_pipeline returns exactly the PrepPipelineResult keys, and
    each top entry exactly the PrepTopScoop keys."""
    same = "藝人A被爆新戀情震驚全網一夜洗版"
    _patch_crawl(monkeypatch, [
        _raw("a1", same, source_id="src_a"),
        _raw("b1", same, source_id="src_b"),
    ])
    result = scoop_pipeline.run_prep_pipeline(_cfg(tmp_path))
    assert set(result) == {
        "ingested", "clusters", "scored", "single_source", "top", "failed"}
    assert result["top"]
    for entry in result["top"]:
        assert set(entry) == {
            "cluster_id", "representative_title", "source_count",
            "confidence", "quality", "score"}


def test_idempotent_rerun(tmp_path, monkeypatch):
    items = [
        _raw("a1", "重複跑測試的標題甲乙丙", source_id="s1"),
        _raw("a2", "重複跑測試的標題甲乙丙", source_id="s2"),
        _raw("b1", "另一件完全不相關的事情"),
    ]
    _patch_crawl(monkeypatch, items)
    cfg = _cfg(tmp_path)
    r1 = scoop_pipeline.run_prep_pipeline(cfg)
    r2 = scoop_pipeline.run_prep_pipeline(cfg)
    assert r1["clusters"] == r2["clusters"]
    assert r1["scored"] == r2["scored"]
    with library.connect(cfg["state_path"]) as conn:
        assert library.count(conn) == 3                 # no duplicate rows on rerun
