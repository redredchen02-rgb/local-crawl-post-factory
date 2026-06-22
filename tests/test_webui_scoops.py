"""WebUI /today 今日備稿 workspace: scoop list, filters, prep trigger, nav."""

from fastapi.testclient import TestClient

from core import library, webui_config
from webui.app import create_app


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
    import core.scoop_pipeline as sp
    monkeypatch.setattr(sp, "run_prep_pipeline",
                        lambda cfg, progress_cb=None, on_source=None: {
                            "ingested": 0, "clusters": 0, "scored": 0,
                            "single_source": False, "top": [], "failed": []})
    r = client.post("/today/prep")
    assert r.status_code == 200
    # either still polling (today/jobs) or already finished (prep done view)
    assert "today/jobs" in r.text or "備稿完成" in r.text or "狀態" in r.text


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
