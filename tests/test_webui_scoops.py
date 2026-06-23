"""WebUI /today 今日備稿 workspace: scoop list, filters, prep trigger, nav."""

import re
import time

from fastapi.testclient import TestClient

from cpost.core import jobs as jobs_mod
from cpost.core import library, pipeline, scoring, scoring_config, webui_config
from cpost.webui.app import create_app


def _client_and_state(tmp_path, **overrides):
    cfgp = tmp_path / "webui.yaml"
    settings = {"start_url": "https://example.com", "out_dir": str(tmp_path / "out")}
    settings.update(overrides)
    webui_config.save(str(cfgp), settings)
    cfg = webui_config.load(str(cfgp))
    return TestClient(create_app(str(cfgp))), cfg["state_path"]


def _seed(state_path, clusters):
    """clusters: list of (cluster_id, title, sources, score, quality)."""
    now = "2026-06-15T00:00:00Z"
    with library.connect(state_path) as conn:
        cl = []
        for cid, title, sources, _score, _quality in clusters:
            urls = []
            for i, sid in enumerate(sources):
                u = f"https://{sid}.example.com/{cid}/{i}"
                urls.append(u)
                library.upsert(conn, canonical_url=u, title=f"{title}{i}", now=now,
                               source_id=sid, source_text="正文內容")
            cl.append({"cluster_id": cid, "members": urls,
                       "member_count": len(urls), "source_count": len(set(sources)),
                       "representative_url": urls[0], "representative_title": title,
                       "earliest_published": now, "latest_published": now})
        library.assign_clusters(conn, cl, now)
        for cid, _title, sources, score, quality in clusters:
            library.set_cluster_scores(conn, cid, confidence=len(set(sources)) / 3,
                                       quality=quality, score=score, now=now)


def test_today_lists_scoops_sorted_by_score(tmp_path):
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c_low", "低分瓜內容", ["s1"], 0.3, 0.3),
        ("c_high", "高分瓜內容", ["s1", "s2"], 0.9, 0.8),
    ])
    r = client.get("/today")
    assert r.status_code == 200
    assert "高分瓜內容" in r.text and "低分瓜內容" in r.text
    assert r.text.index("高分瓜內容") < r.text.index("低分瓜內容")  # score desc


def test_min_confidence_filters_to_multi_source(tmp_path):
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c1", "單源瓜內容", ["s1"], 0.5, 0.5),
        ("c2", "多源瓜內容", ["s1", "s2"], 0.8, 0.5),
    ])
    r = client.get("/today/list", params={"min_confidence": 2})
    assert "多源瓜內容" in r.text and "單源瓜內容" not in r.text


def test_ranking_driven_by_quality_not_confidence(tmp_path):
    # Confidence axis is neutralized (weight_confidence: 0.0 in configs/scoring.yaml):
    # a high-quality single-source scoop must outrank a low-quality multi-source
    # one. Scores are computed through the real scoring config so reverting the
    # weight back to >0 (which would let source_count dominate) fails this test.
    cfg = scoring_config.load("configs/scoring.yaml")
    assert cfg["weight_confidence"] == 0.0  # guard: neutralization must hold

    def _score(source_count, quality_v):
        conf = scoring.confidence(source_count,
                                  source_cap=int(cfg["confidence_source_cap"]))
        return scoring.combined(conf, quality_v,
                                w_confidence=float(cfg["weight_confidence"]),
                                w_quality=float(cfg["weight_quality"]))

    # Low source_count + high quality vs high source_count + low quality.
    lo_src_hi_q = _score(1, 0.9)
    hi_src_lo_q = _score(3, 0.2)
    assert lo_src_hi_q > hi_src_lo_q  # quality alone decides ordering

    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c_multi_lowq", "多源低品質瓜內容", ["s1", "s2", "s3"], hi_src_lo_q, 0.2),
        ("c_single_hiq", "單源高品質瓜內容", ["s1"], lo_src_hi_q, 0.9),
    ])
    r = client.get("/today")
    assert r.status_code == 200
    # High-quality single-source scoop ranks first despite fewer sources.
    assert r.text.index("單源高品質瓜內容") < r.text.index("多源低品質瓜內容")


def test_single_source_default_not_emptied_and_flagged(tmp_path):
    client, state = _client_and_state(tmp_path)
    _seed(state, [("c1", "唯一單源瓜", ["only"], 0.5, 0.5)])
    r = client.get("/today")
    assert "唯一單源瓜" in r.text          # default min_confidence=0 keeps it
    assert "暫不具區分力" in r.text         # single-source note shown


def test_empty_library_no_500(tmp_path):
    client, _ = _client_and_state(tmp_path)
    r = client.get("/today")
    assert r.status_code == 200
    assert "沒有可選的瓜" in r.text


def test_generate_zero_selected_rejected(tmp_path):
    client, _ = _client_and_state(tmp_path)
    r = client.post("/today/generate", data={})
    assert r.status_code == 400


def test_prep_trigger_returns_job_view(tmp_path, monkeypatch):
    client, _ = _client_and_state(tmp_path)
    import cpost.core.scoop_pipeline as sp
    monkeypatch.setattr(sp, "run_prep_pipeline",
                        lambda cfg, progress_cb=None, on_source=None,
                        crawl_progress_cb=None: {
                            "ingested": 0, "clusters": 0, "scored": 0,
                            "single_source": False, "top": [], "failed": []})
    r = client.post("/today/prep")
    assert r.status_code == 200
    # either still polling (today/jobs) or already finished (prep done view)
    assert "today/jobs" in r.text or "備稿完成" in r.text or "狀態" in r.text


def _today_job_id(html):
    m = re.search(r"/today/jobs/([0-9a-f]+)", html)
    return m.group(1) if m else None


def test_prep_crawl_telemetry_no_dict_in_log_and_status_advances(tmp_path, monkeypatch):
    """U18 integration: a prep run with a crawl source must surface live crawl
    telemetry on the status line (current), never as a raw dict in the job log.

    The crawl subprocess fires {responses, items, last_url, last_title} dict
    snapshots. Before the fix the prep path routed those through jobs.report,
    stringifying dicts into job.progress. Now they map to jobs.set_current.
    """
    client, _ = _client_and_state(tmp_path)

    def fake_crawl(cfg, progress_cb=None, poll_sec=0.5):
        if progress_cb:
            progress_cb({"responses": 3, "items": 1, "last_url": "https://ex.com/a",
                         "last_title": "爬到的標題甲"})
        return [{
            "source_id": "example.com",
            "url": "https://example.com/a",
            "canonical_url": "https://example.com/a",
            "title": "今日大事件的完整標題敘述",
            "text": "內容段落敘述。" * 80,
            "description": "desc",
            "published_at": "2026-06-15T10:00:00+08:00",
            "discovered_at": "2026-06-15T02:00:00Z",
        }]

    monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)
    r = client.post("/today/prep")
    assert r.status_code == 200
    jid = _today_job_id(r.text)
    assert jid

    for _ in range(100):
        client.get(f"/today/jobs/{jid}")
        j = jobs_mod.get(jid)
        if j and j["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    job = jobs_mod.get(jid)
    assert job is not None
    assert job["status"] == "done"

    # No raw dict repr leaked into the human-readable log.
    assert all(isinstance(m, str) for m in job["progress"])
    assert all("'responses'" not in m and "爬到的標題甲" not in m
               for m in job["progress"])
    # The live crawl telemetry advanced the status line during the crawl
    # (set_current, not report). After the crawl no further set_current runs,
    # so the final status holds the last crawl snapshot.
    assert "爬取進度 3 頁" in job["current"]
    assert "爬到的標題甲" in job["current"]
    # Stage reports still flow to the log as plain strings.
    assert any("爬取完成" in m for m in job["progress"])


def test_min_score_filters_low_scoring(tmp_path):
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c_hi", "高分瓜內容", ["s1"], 0.9, 0.9),
        ("c_lo", "低分瓜內容", ["s1"], 0.2, 0.2),
    ])
    r = client.get("/today/list", params={"min_score": 0.5})
    assert "高分瓜內容" in r.text and "低分瓜內容" not in r.text


def test_today_job_not_found_404(tmp_path):
    client, _ = _client_and_state(tmp_path)
    r = client.get("/today/jobs/nonexistent")
    assert r.status_code == 404


def test_null_score_cluster_does_not_crash(tmp_path):
    # A cluster that was clustered but not yet scored has NULL score columns;
    # the list filter must not crash on it (the `or 0` guards None).
    client, state = _client_and_state(tmp_path)
    now = "2026-06-15T00:00:00Z"
    with library.connect(state) as conn:
        u = "https://s1.example.com/unscored"
        library.upsert(conn, canonical_url=u, title="未打分", now=now,
                       source_id="s1", source_text="正文")
        library.assign_clusters(conn, [{
            "cluster_id": "c_unscored", "members": [u], "member_count": 1,
            "source_count": 1, "representative_url": u,
            "representative_title": "未打分的瓜", "earliest_published": now,
            "latest_published": now}], now)  # no set_cluster_scores -> score NULL
    r = client.get("/today/list")
    assert r.status_code == 200
    assert "未打分的瓜" in r.text


def test_nav_has_today_link(tmp_path):
    client, _ = _client_and_state(tmp_path)
    r = client.get("/today")
    assert 'href="/today"' in r.text


# ---------------------------------------------------------------------------
# U6: /scoops — cross-site badge + min_sources filter
# ---------------------------------------------------------------------------

def test_scoops_no_param_shows_all(tmp_path):
    """`GET /scoops` (no min_sources) returns all scoops unchanged."""
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c1", "單源瓜", ["s1"], 0.5, 0.5),
        ("c2", "多源瓜", ["s1", "s2"], 0.8, 0.7),
    ])
    r = client.get("/scoops")
    assert r.status_code == 200
    assert "單源瓜" in r.text
    assert "多源瓜" in r.text


def test_scoops_min_sources_filters(tmp_path):
    """`GET /scoops?min_sources=2` shows only source_count >= 2 scoops."""
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c1", "單源瓜內容", ["s1"], 0.5, 0.5),
        ("c2", "多源瓜內容", ["s1", "s2"], 0.8, 0.7),
    ])
    r = client.get("/scoops", params={"min_sources": 2})
    assert r.status_code == 200
    assert "多源瓜內容" in r.text
    assert "單源瓜內容" not in r.text


def test_scoops_multi_source_badge_shown(tmp_path):
    """A scoop with source_count=3 when min_sources=2 → 🔥 badge in HTML."""
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c3", "三源瓜內容", ["s1", "s2", "s3"], 0.9, 0.9),
    ])
    r = client.get("/scoops", params={"min_sources": 2})
    assert r.status_code == 200
    assert "三源瓜內容" in r.text
    assert "🔥" in r.text


def test_scoops_min_sources_zero_no_badge(tmp_path):
    """With min_sources=0 (default), no 🔥 badge is shown."""
    client, state = _client_and_state(tmp_path)
    _seed(state, [
        ("c1", "多源瓜內容", ["s1", "s2"], 0.8, 0.7),
    ])
    r = client.get("/scoops", params={"min_sources": 0})
    assert r.status_code == 200
    assert "多源瓜內容" in r.text
    assert "🔥" not in r.text


def test_scoops_empty_library(tmp_path):
    """`GET /scoops` on empty library → 200, no crash."""
    client, _ = _client_and_state(tmp_path)
    r = client.get("/scoops")
    assert r.status_code == 200
