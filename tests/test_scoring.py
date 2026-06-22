import math

from core import scoring

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
    assert s == {"confidence": 0.0, "quality": 0.0, "score": 0.0}


def test_score_cluster_deterministic():
    a = scoring.score_cluster(_cluster(2, 2, NOW), [{"source_text": "x" * 500}], NOW, _CFG)
    b = scoring.score_cluster(_cluster(2, 2, NOW), [{"source_text": "x" * 500}], NOW, _CFG)
    assert a == b
