"""Score scoops on two axes: multi-source confidence + content quality (plan U4).

Confidence rises with the number of *independent* sources corroborating a scoop
(the count of distinct ``source_id``, computed upstream in clustering -- so
repeating the same source never inflates it). Quality blends content
completeness, recency, and material volume.

Both axes are kept separate (so the selection UI can filter on either) and also
combined into one sortable score. Every function is pure and deterministic;
``now`` is passed in so recency is testable. All outputs are clamped to 0..1 and
never NaN, even on empty/degenerate input.
"""

from __future__ import annotations

from core.timeutil import parse_iso


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def confidence(source_count: int, *, source_cap: int) -> float:
    """0..1 multi-source corroboration, saturating at ``source_cap`` sources."""
    if source_cap <= 0:
        return 1.0 if source_count > 0 else 0.0
    return _clamp01(source_count / source_cap)


def completeness(max_text_len: int, *, full_text_chars: int) -> float:
    if full_text_chars <= 0:
        return 1.0 if max_text_len > 0 else 0.0
    return _clamp01(max_text_len / full_text_chars)


def recency(latest_published: str | None, now: str, *, window_hours: float) -> float:
    """1.0 at ``now``, decaying linearly to 0 at the window edge; missing time -> 0."""
    dt = parse_iso(latest_published)
    ref = parse_iso(now)
    if dt is None or ref is None or window_hours <= 0:
        return 0.0
    age_h = (ref - dt).total_seconds() / 3600
    if age_h <= 0:
        return 1.0
    return _clamp01(1 - age_h / window_hours)


def material(member_count: int, *, material_cap: int) -> float:
    if material_cap <= 0:
        return 1.0 if member_count > 0 else 0.0
    return _clamp01(member_count / material_cap)


def _weighted(parts: list[tuple[float, float]]) -> float:
    """Weighted average of (value, weight) pairs; 0 when weights sum to <= 0."""
    total_w = sum(w for _, w in parts)
    if total_w <= 0:
        return 0.0
    return _clamp01(sum(v * w for v, w in parts) / total_w)


def quality(*, completeness_v: float, recency_v: float, material_v: float,
            w_completeness: float, w_recency: float, w_material: float) -> float:
    return _weighted([(completeness_v, w_completeness),
                      (recency_v, w_recency),
                      (material_v, w_material)])


def combined(confidence_v: float, quality_v: float, *,
             w_confidence: float, w_quality: float) -> float:
    return _weighted([(confidence_v, w_confidence), (quality_v, w_quality)])


def score_cluster(cluster: dict, members: list[dict], now: str, cfg: dict) -> dict:
    """Compute ``{confidence, quality, score}`` for one cluster and its members."""
    max_text_len = max((len(m.get("source_text") or "") for m in members), default=0)
    conf = confidence(int(cluster.get("source_count") or 0),
                      source_cap=int(cfg["confidence_source_cap"]))
    comp = completeness(max_text_len, full_text_chars=int(cfg["quality_full_text_chars"]))
    rec = recency(cluster.get("latest_published"), now,
                  window_hours=float(cfg["quality_recency_window_hours"]))
    mat = material(int(cluster.get("member_count") or 0),
                   material_cap=int(cfg["quality_material_cap"]))
    qual = quality(completeness_v=comp, recency_v=rec, material_v=mat,
                   w_completeness=float(cfg["weight_completeness"]),
                   w_recency=float(cfg["weight_recency"]),
                   w_material=float(cfg["weight_material"]))
    sc = combined(conf, qual, w_confidence=float(cfg["weight_confidence"]),
                  w_quality=float(cfg["weight_quality"]))
    return {"confidence": conf, "quality": qual, "score": sc}
