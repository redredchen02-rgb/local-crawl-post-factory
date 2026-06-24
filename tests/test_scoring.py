import math

from cpost.core import scoring

NOW = "2026-06-18T00:00:00+00:00"


# --- confidence: multi-source corroboration ---

def test_confidence_rises_with_sources_and_caps():
    assert scoring.confidence(1, source_cap=3) < scoring.confidence(2, source_cap=3)
    assert scoring.confidence(2, source_cap=3) < scoring.confidence(3, source_cap=3)
    assert scoring.confidence(3, source_cap=3) == 1.0
    assert scoring.confidence(9, source_cap=3) == 1.0  # saturates


def test_confidence_zero_sources_is_zero():
    assert scoring.confidence(0, source_cap=3) == 0.0


def test_confidence_zero_cap_no_div_error():
    assert scoring.confidence(2, source_cap=0) == 1.0
    assert scoring.confidence(0, source_cap=0) == 0.0


# --- completeness ---

def test_completeness_normalizes_and_caps():
    assert scoring.completeness(0, full_text_chars=1000) == 0.0
    assert scoring.completeness(500, full_text_chars=1000) == 0.5
    assert scoring.completeness(5000, full_text_chars=1000) == 1.0


def test_completeness_zero_chars_returns_one_if_any_text():
    """completeness(): full_text_chars <= 0 returns 1.0 if max_text_len > 0 else 0.0 (scoring.py:42)."""
    assert scoring.completeness(100, full_text_chars=0) == 1.0
    assert scoring.completeness(0, full_text_chars=0) == 0.0


# --- recency ---

def test_recency_decays_linearly():
    assert scoring.recency(NOW, NOW, window_hours=168) == 1.0
    # 2026-06-15T00:00 is exactly 72h before NOW; window 144h -> half decayed
    half = scoring.recency("2026-06-15T00:00:00+00:00", NOW, window_hours=144)
    assert math.isclose(half, 0.5, abs_tol=1e-9)


def test_recency_beyond_window_is_zero():
    assert scoring.recency("2026-01-01T00:00:00+00:00", NOW, window_hours=168) == 0.0


def test_recency_naive_published_at_does_not_crash():
    """Regression: naive latest_published vs aware now must not raise TypeError."""
    assert scoring.recency("2026-06-18T00:00:00", NOW, window_hours=168) == 1.0


def test_recency_zero_or_negative_window_is_zero():
    assert scoring.recency(NOW, NOW, window_hours=0) == 0.0
    assert scoring.recency(NOW, NOW, window_hours=-5) == 0.0


def test_recency_missing_timestamp_is_zero():
    assert scoring.recency(None, NOW, window_hours=168) == 0.0


def test_recency_future_is_full():
    assert scoring.recency("2026-12-01T00:00:00+00:00", NOW, window_hours=168) == 1.0


# --- material ---

def test_material_caps():
    assert scoring.material(1, material_cap=3) < scoring.material(3, material_cap=3)
    assert scoring.material(9, material_cap=3) == 1.0


def test_material_zero_cap_returns_one_if_any_members():
    """material(): material_cap <= 0 returns 1.0 if member_count > 0 else 0.0 (scoring.py:59)."""
    assert scoring.material(1, material_cap=0) == 1.0
    assert scoring.material(0, material_cap=0) == 0.0


# --- quality / combined weighting ---

def test_quality_all_max_is_one():
    q = scoring.quality(completeness_v=1.0, recency_v=1.0, material_v=1.0,
                        w_completeness=0.5, w_recency=0.2, w_material=0.3)
    assert q == 1.0


def test_quality_normalizes_weights():
    # only completeness weighted -> quality == completeness regardless of others
    q = scoring.quality(completeness_v=0.4, recency_v=1.0, material_v=1.0,
                        w_completeness=1.0, w_recency=0.0, w_material=0.0)
    assert math.isclose(q, 0.4)


def test_combined_weighting():
    c = scoring.combined(1.0, 0.0, w_confidence=0.6, w_quality=0.4)
    assert math.isclose(c, 0.6)


def test_weighted_zero_weights_no_nan():
    q = scoring.quality(completeness_v=1.0, recency_v=1.0, material_v=1.0,
                        w_completeness=0.0, w_recency=0.0, w_material=0.0)
    assert q == 0.0  # not NaN


# --- score_cluster orchestration ---

_CFG = {
    "confidence_source_cap": 3, "quality_full_text_chars": 1000,
    "quality_recency_window_hours": 168, "quality_material_cap": 3,
    "weight_completeness": 0.5, "weight_recency": 0.2, "weight_material": 0.3,
    "weight_confidence": 0.6, "weight_quality": 0.4,
}


def _cluster(source_count, member_count, latest):
    return {"source_count": source_count, "member_count": member_count,
            "latest_published": latest}


def test_score_cluster_multisource_beats_singlesource():
    rich = scoring.score_cluster(
        _cluster(3, 3, NOW),
        [{"source_text": "x" * 1000}] * 3, NOW, _CFG)
    thin = scoring.score_cluster(
        _cluster(1, 1, "2026-01-01T00:00:00+00:00"),
        [{"source_text": "x" * 50}], NOW, _CFG)
    assert rich["confidence"] > thin["confidence"]
    assert rich["quality"] > thin["quality"]
    assert rich["score"] > thin["score"]


def test_score_cluster_empty_members_no_crash():
    s = scoring.score_cluster(_cluster(0, 0, None), [], NOW, _CFG)
    assert s["confidence"] == 0.0
    assert s["quality"] == 0.0
    assert s["score"] == 0.0
    # Extra 4D fields present
    assert "freshness" in s and "importance" in s
    assert "traffic_potential" in s and "cross_site_coverage" in s


def test_score_cluster_deterministic():
    a = scoring.score_cluster(_cluster(2, 2, NOW), [{"source_text": "x" * 500}], NOW, _CFG)
    b = scoring.score_cluster(_cluster(2, 2, NOW), [{"source_text": "x" * 500}], NOW, _CFG)
    assert a == b


# --- 4D: freshness ---

def test_freshness_weighted_average():
    v = scoring.freshness(recency_v=1.0, velocity_v=0.5,
                          w_recency=0.6, w_velocity=0.4)
    assert v == 0.8  # (1.0*0.6 + 0.5*0.4) / 1.0


def test_freshness_only_velocity_counts():
    v = scoring.freshness(recency_v=0.0, velocity_v=1.0,
                          w_recency=0.0, w_velocity=1.0)
    assert v == 1.0


def test_freshness_zero_weights():
    v = scoring.freshness(recency_v=0.8, velocity_v=0.4,
                          w_recency=0.0, w_velocity=0.0)
    assert v == 0.0


def test_freshness_clamps_excess():
    v = scoring.freshness(recency_v=1.5, velocity_v=1.5,
                          w_recency=0.5, w_velocity=0.5)
    assert v == 1.0


# --- 4D: freshness_velocity ---

def test_freshness_velocity_all_recent():
    # 3 members all published within the 24h window
    v = scoring.freshness_velocity(
        3, window_hours=24,
        published_ats=[NOW, NOW, NOW],
        now=NOW, velocity_cap=5)
    assert v == 0.6  # 3/5


def test_freshness_velocity_some_recent():
    # 2 out of 3 in window
    v = scoring.freshness_velocity(
        3, window_hours=72,
        published_ats=[NOW, "2026-01-01T00:00:00+00:00", "2026-06-17T00:00:00+00:00"],
        now=NOW, velocity_cap=5)
    assert v == 0.4  # 2/5


def test_freshness_velocity_none_recent():
    v = scoring.freshness_velocity(
        3, window_hours=24,
        published_ats=["2026-01-01T00:00:00+00:00"] * 3,
        now=NOW, velocity_cap=5)
    assert v == 0.0


def test_freshness_velocity_all_old_becomes_zero():
    v = scoring.freshness_velocity(
        3, window_hours=24,
        published_ats=["2026-01-01T00:00:00+00:00"],
        now=NOW, velocity_cap=5)
    assert v == 0.0


def test_freshness_velocity_saturates():
    v = scoring.freshness_velocity(
        10, window_hours=24,
        published_ats=[NOW] * 10,
        now=NOW, velocity_cap=3)
    assert v == 1.0  # capped at 3/3


def test_freshness_velocity_no_members():
    v = scoring.freshness_velocity(
        0, window_hours=24,
        published_ats=[], now=NOW, velocity_cap=5)
    assert v == 0.0


def test_freshness_velocity_invalid_now_returns_zero():
    """freshness_velocity(): when now is not parseable, ref is None → 0.0 (scoring.py:100)."""
    v = scoring.freshness_velocity(
        3, window_hours=24,
        published_ats=[NOW], now="not-a-date", velocity_cap=5)
    assert v == 0.0


def test_freshness_velocity_zero_window():
    v = scoring.freshness_velocity(
        5, window_hours=0,
        published_ats=[NOW] * 5, now=NOW, velocity_cap=5)
    assert v == 0.0


def test_freshness_velocity_none_published_at():
    v = scoring.freshness_velocity(
        3, window_hours=24,
        published_ats=[None, None, None],
        now=NOW, velocity_cap=5)
    assert v == 0.0


def test_freshness_velocity_mixed_none_and_valid():
    v = scoring.freshness_velocity(
        3, window_hours=24,
        published_ats=[None, NOW, "2026-01-01T00:00:00+00:00"],
        now=NOW, velocity_cap=5)
    assert v == 0.2  # 1/5 (only NOW counts)


# --- 4D: importance ---

def test_importance_weighted_average():
    v = scoring.importance(completeness_v=1.0, material_v=0.5, diversity_v=0.0,
                           w_completeness=1.0, w_material=0.0, w_diversity=0.0)
    assert v == 1.0


def test_importance_spreads():
    v = scoring.importance(completeness_v=1.0, material_v=0.5, diversity_v=0.5,
                           w_completeness=0.5, w_material=0.3, w_diversity=0.2)
    expected = (1.0 * 0.5 + 0.5 * 0.3 + 0.5 * 0.2) / 1.0
    assert v == expected


def test_importance_all_zero():
    v = scoring.importance(completeness_v=0.0, material_v=0.0, diversity_v=0.0,
                           w_completeness=0.3, w_material=0.3, w_diversity=0.4)
    assert v == 0.0


def test_importance_zero_weights():
    v = scoring.importance(completeness_v=1.0, material_v=1.0, diversity_v=1.0,
                           w_completeness=0.0, w_material=0.0, w_diversity=0.0)
    assert v == 0.0


# --- 4D: importance_diversity + _jaccard_str ---

def test_importance_diversity_identical():
    members = [{"title": "Breaking News"}, {"title": "Breaking News"}]
    assert scoring.importance_diversity(members, threshold=0.3) == 0.0


def test_importance_diversity_completely_different():
    members = [{"title": "Apple Launch Event"}, {"title": "Earthquake in Japan"}]
    v = scoring.importance_diversity(members, threshold=0.3)
    assert v == 1.0


def test_importance_diversity_empty():
    assert scoring.importance_diversity([], threshold=0.3) == 0.0


def test_importance_diversity_single():
    members = [{"title": "Only One"}]
    assert scoring.importance_diversity(members, threshold=0.3) == 1.0


def test_importance_diversity_no_title_field():
    members = [{}, {}]
    v = scoring.importance_diversity(members, threshold=0.3)
    assert v == 1.0  # both empty → jaccard 0.0 for each → diversity counted


def test_importance_diversity_threshold_adjustment():
    # two similar but not identical titles
    members = [{"title": "Apple iPhone 16 Launch"}, {"title": "Apple iPhone 16 Pro Launch"}]
    strict = scoring.importance_diversity(members, threshold=0.2)
    loose = scoring.importance_diversity(members, threshold=0.8)
    assert strict >= loose  # stricter threshold → more pairs count as diverse


# --- 4D: cross_site_coverage ---

def test_cross_site_coverage_linear():
    assert scoring.cross_site_coverage(0, source_cap=5) == 0.0
    assert scoring.cross_site_coverage(1, source_cap=5) == 0.2
    assert scoring.cross_site_coverage(3, source_cap=5) == 0.6
    assert scoring.cross_site_coverage(5, source_cap=5) == 1.0
    assert scoring.cross_site_coverage(20, source_cap=5) == 1.0


def test_cross_site_coverage_zero_cap():
    assert scoring.cross_site_coverage(0, source_cap=0) == 0.0
    assert scoring.cross_site_coverage(3, source_cap=0) == 1.0


# --- 4D: traffic_potential ---

def test_traffic_potential_all_max():
    v = scoring.traffic_potential(volume_v=1.0, source_diversity_v=1.0, coverage_v=1.0,
                                  w_volume=0.4, w_sources=0.3, w_diversity=0.3)
    assert v == 1.0


def test_traffic_potential_volume_zero():
    v = scoring.traffic_potential(volume_v=0.0, source_diversity_v=1.0, coverage_v=1.0,
                                  w_volume=1.0, w_sources=0.0, w_diversity=0.0)
    assert v == 0.0


def test_traffic_potential_zero_weights():
    v = scoring.traffic_potential(volume_v=0.5, source_diversity_v=0.5, coverage_v=0.5,
                                  w_volume=0.0, w_sources=0.0, w_diversity=0.0)
    assert v == 0.0


# --- 4D: traffic_volume ---

def test_traffic_volume_linear():
    assert scoring.traffic_volume(0, cap=50) == 0.0
    assert scoring.traffic_volume(25, cap=50) == 0.5
    assert scoring.traffic_volume(50, cap=50) == 1.0
    assert scoring.traffic_volume(200, cap=50) == 1.0


def test_traffic_volume_none():
    assert scoring.traffic_volume(None, cap=50) == 0.0


def test_traffic_volume_zero_cap():
    assert scoring.traffic_volume(10, cap=0) == 0.0


# --- 4D: traffic_source_diversity ---

def test_traffic_source_diversity_linear():
    assert scoring.traffic_source_diversity(0, cap=10) == 0.0
    assert scoring.traffic_source_diversity(5, cap=10) == 0.5
    assert scoring.traffic_source_diversity(10, cap=10) == 1.0
    assert scoring.traffic_source_diversity(50, cap=10) == 1.0


def test_traffic_source_diversity_none():
    assert scoring.traffic_source_diversity(None, cap=10) == 0.0


def test_traffic_source_diversity_zero_cap():
    assert scoring.traffic_source_diversity(5, cap=0) == 0.0


# --- score_cluster_v2 integration ---

_CFG_V2 = {**_CFG,
    "freshness_w_recency": 0.6, "freshness_w_velocity": 0.4,
    "freshness_velocity_window_hours": 24,
    "importance_w_completeness": 0.5, "importance_w_material": 0.3, "importance_w_diversity": 0.2,
    "importance_diversity_threshold": 0.3,
    "traffic_w_volume": 0.4, "traffic_w_sources": 0.3, "traffic_w_diversity": 0.3,
    "traffic_article_cap": 50, "traffic_source_cap": 10, "traffic_diversity_cap": 3,
    "cross_site_source_cap": 5,
    "weight_freshness": 0.25, "weight_importance": 0.25,
    "weight_traffic_potential": 0.25, "weight_cross_site_coverage": 0.25,
}


def test_score_cluster_v2_returns_all_keys():
    result = scoring.score_cluster_v2(
        _cluster(2, 2, NOW),
        [{"source_text": "x" * 500, "title": "A", "published_at": NOW}],
        NOW, _CFG_V2)
    expected_keys = {
        "confidence", "quality", "score", "score_legacy",
        "freshness", "importance", "traffic_potential", "cross_site_coverage",
        "external_article_count", "external_source_count",
        "external_latest_at", "search_volume_proxy",
    }
    assert set(result.keys()) == expected_keys


def test_score_cluster_v2_empty():
    result = scoring.score_cluster_v2(_cluster(0, 0, None), [], NOW, _CFG_V2)
    for k in ("score", "score_legacy", "freshness", "importance",
              "traffic_potential", "cross_site_coverage"):
        assert result[k] == 0.0, f"{k} should be 0.0, got {result[k]}"
    assert result["confidence"] == 0.0
    assert result["quality"] == 0.0


def test_score_cluster_v2_with_external_data():
    result = scoring.score_cluster_v2(
        _cluster(3, 3, NOW),
        [{"source_text": "x" * 1000, "title": "A", "published_at": NOW}] * 3,
        NOW, _CFG_V2,
        external={
            "external_article_count": 30,
            "external_source_count": 5,
            "external_latest_at": NOW,
            "search_volume_proxy": 0.8,
        })
    assert result["external_article_count"] == 30
    assert result["external_source_count"] == 5
    assert result["traffic_potential"] > 0.0


def test_score_cluster_v2_without_external_data():
    """Without external data, traffic-related sub-scores default to 0."""
    result = scoring.score_cluster_v2(
        _cluster(2, 2, NOW),
        [{"source_text": "x" * 500, "title": "A", "published_at": NOW}] * 2,
        NOW, _CFG_V2)
    assert result["external_article_count"] is None
    assert result["external_source_count"] is None
    assert result["external_latest_at"] is None
    assert result["search_volume_proxy"] is None


def test_score_cluster_v2_old_cfg_no_crash():
    """score_cluster_v2 must not crash when called with an old-style cfg (no 4D keys)."""
    result = scoring.score_cluster_v2(
        _cluster(1, 1, NOW),
        [{"source_text": "x" * 500, "title": "A", "published_at": NOW}],
        NOW, _CFG)  # uses in-code defaults for missing keys
    assert result["score"] >= 0.0


# --- score_cluster (legacy) backward compat ---

def test_score_cluster_still_works():
    s = scoring.score_cluster(
        _cluster(2, 2, NOW),
        [{"source_text": "x" * 500}] * 2, NOW, _CFG)
    assert set(s.keys()) >= {"confidence", "quality", "score"}
