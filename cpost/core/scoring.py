"""Score scoops on four intuitive dimensions: freshness, importance, traffic
potential, and cross-site coverage (4D scoring v2).

Each dimension is a 0..1 score computed from the cluster's library members plus
optional external-search enrichment. They are combined via configurable weights
into a single sortable ``score``. The original confidence/quality axes are kept
as derived informational fields for backward compatibility.

Every function is pure and deterministic; ``now`` is passed in so recency is
testable. All outputs are clamped to 0..1 and never NaN, even on empty/degenerate
input.
"""

from __future__ import annotations

from cpost.core.timeutil import parse_iso


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _weighted(parts: list[tuple[float, float]]) -> float:
    """Weighted average of (value, weight) pairs; 0 when weights sum to <= 0."""
    total_w = sum(w for _, w in parts)
    if total_w <= 0:
        return 0.0
    return _clamp01(sum(v * w for v, w in parts) / total_w)


# --- legacy confidence / quality (kept for backward compat) -------------------


def confidence(source_count: int, *, source_cap: int) -> float:
    if source_cap <= 0:
        return 1.0 if source_count > 0 else 0.0
    return _clamp01(source_count / source_cap)


def completeness(max_text_len: int, *, full_text_chars: int) -> float:
    if full_text_chars <= 0:
        return 1.0 if max_text_len > 0 else 0.0
    return _clamp01(max_text_len / full_text_chars)


def recency(latest_published: str | None, now: str, *, window_hours: float) -> float:
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


def quality(*, completeness_v: float, recency_v: float, material_v: float,
            w_completeness: float, w_recency: float, w_material: float) -> float:
    return _weighted([(completeness_v, w_completeness),
                      (recency_v, w_recency),
                      (material_v, w_material)])


def combined(confidence_v: float, quality_v: float, *,
             w_confidence: float, w_quality: float) -> float:
    return _weighted([(confidence_v, w_confidence), (quality_v, w_quality)])


# --- 4D scoring dimension functions -------------------------------------------


def freshness(*, recency_v: float, velocity_v: float,
              w_recency: float, w_velocity: float) -> float:
    """How fresh / newly broken is this scoop.

    ``recency_v`` measures the age of the latest article (0 = ancient).
    ``velocity_v`` measures how many articles appeared in the burst window
    (0 = stale). Combined into a weighted score.
    """
    return _weighted([(recency_v, w_recency), (velocity_v, w_velocity)])


def freshness_velocity(member_count: int, *, window_hours: float,
                       published_ats: list[str | None], now: str,
                       velocity_cap: int) -> float:
    """Article burst velocity: fraction of members published in the *velocity*
    window (``window_hours`` before ``now``). Saturates at ``velocity_cap``.
    Returns 0 when window_hours <= 0 or no members.
    """
    if window_hours <= 0 or member_count <= 0:
        return 0.0
    ref = parse_iso(now)
    if ref is None:
        return 0.0
    cutoff = ref.timestamp() - window_hours * 3600
    recent = sum(
        1 for pa in published_ats
        if (dt := parse_iso(pa)) is not None and dt.timestamp() >= cutoff
    )
    cap = max(velocity_cap, 1)
    return _clamp01(recent / cap)


def importance(*, completeness_v: float, material_v: float, diversity_v: float,
               w_completeness: float, w_material: float, w_diversity: float) -> float:
    """How important / big is this scoop (text depth + member quantity + diversity)."""
    return _weighted([(completeness_v, w_completeness),
                      (material_v, w_material),
                      (diversity_v, w_diversity)])


def importance_diversity(members: list[dict], *, threshold: float) -> float:
    """Content diversity: fraction of member pairs whose title Jaccard similarity
    falls below *threshold* (i.e. they tell different angles of the same story).
    0 = all members say the same thing; 1 = every member adds a new angle.
    Returns 1.0 when only 0-1 members (no pairs to compare).
    """
    titles = [m.get("title") or "" for m in members]
    n = len(titles)
    if n <= 1:
        return 1.0 if n == 1 else 0.0
    diverse = 0
    total = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            sim = _jaccard_str(titles[i], titles[j])
            if sim < threshold:
                diverse += 1
    return 1.0 if total == 0 else _clamp01(diverse / total)


def _jaccard_str(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_grams = {a[i:i + 2].lower() for i in range(max(0, len(a) - 1))}
    b_grams = {b[i:i + 2].lower() for i in range(max(0, len(b) - 1))}
    if not a_grams or not b_grams:
        return 0.0
    inter = len(a_grams & b_grams)
    union = len(a_grams | b_grams)
    return inter / union if union else 0.0


def cross_site_coverage(source_count: int, *, source_cap: int) -> float:
    """How many distinct sites cover this scoop (saturates at *source_cap*)."""
    if source_cap <= 0:
        return 1.0 if source_count > 0 else 0.0
    return _clamp01(source_count / source_cap)


def traffic_potential(*, volume_v: float, source_diversity_v: float,
                      coverage_v: float,
                      w_volume: float, w_sources: float, w_diversity: float) -> float:
    """Traffic / SEO potential based on external search signals.

    ``volume_v`` = external article count (normalised).
    ``source_diversity_v`` = external source count (normalised).
    ``coverage_v`` = cross_site_coverage score (from our own sources).
    """
    return _weighted([(volume_v, w_volume),
                      (source_diversity_v, w_sources),
                      (coverage_v, w_diversity)])


def traffic_volume(external_article_count: int | None, *, cap: int) -> float:
    """Normalise external article count, saturating at *cap*. Returns 0 when None."""
    if external_article_count is None or cap <= 0:
        return 0.0
    return _clamp01(external_article_count / cap)


def traffic_source_diversity(external_source_count: int | None, *, cap: int) -> float:
    """Normalise external source diversity, saturating at *cap*. Returns 0 when None."""
    if external_source_count is None or cap <= 0:
        return 0.0
    return _clamp01(external_source_count / cap)


# --- score_cluster_v2: 4D orchestration ----------------------------------------


def score_cluster_v2(cluster: dict, members: list[dict], now: str,
                     cfg: dict, external: dict | None = None) -> dict:
    """Compute the 4D score vector for one cluster.

    ``external`` is an optional dict from ``external_search.search_cluster()``:
        {external_article_count, external_source_count, external_latest_at,
         search_volume_proxy}
    When None, traffic-potential sub-fields that depend on external data default to 0.
    Returns a dict with the 4 dimension scores, a combined ``score``, plus
    derived ``confidence`` and ``quality`` for backward compat.
    """
    ext = external or {}
    max_text_len = max((len(m.get("source_text") or "") for m in members), default=0)
    source_count = int(cluster.get("source_count") or 0)
    member_count = int(cluster.get("member_count") or 0)
    pub_ats = [m.get("published_at") for m in members]

    # Legacy axes (backward compat)
    conf = confidence(source_count, source_cap=int(cfg["confidence_source_cap"]))
    comp = completeness(max_text_len, full_text_chars=int(cfg["quality_full_text_chars"]))
    rec = recency(cluster.get("latest_published"), now,
                  window_hours=float(cfg["quality_recency_window_hours"]))
    mat = material(member_count, material_cap=int(cfg["quality_material_cap"]))

    # --- 4D dimensions ---
    # All new keys use cfg.get() with explicit defaults so that callers
    # using older configs (without 4D keys) don't break.

    # 1. Freshness = recency + velocity
    rec_v = recency(cluster.get("latest_published"), now,
                    window_hours=float(cfg["quality_recency_window_hours"]))
    vel_v = freshness_velocity(
        member_count,
        window_hours=float(cfg.get("freshness_velocity_window_hours", 24)),
        published_ats=pub_ats,
        now=now,
        velocity_cap=int(cfg.get("quality_material_cap", 3)),
    )
    fresh = freshness(
        recency_v=rec_v, velocity_v=vel_v,
        w_recency=float(cfg.get("freshness_w_recency", 0.6)),
        w_velocity=float(cfg.get("freshness_w_velocity", 0.4)),
    )

    # 2. Importance = completeness + material + diversity
    div_v = importance_diversity(members,
                                 threshold=float(cfg.get("importance_diversity_threshold", 0.3)))
    imp = importance(
        completeness_v=comp, material_v=mat, diversity_v=div_v,
        w_completeness=float(cfg.get("importance_w_completeness", 0.5)),
        w_material=float(cfg.get("importance_w_material", 0.3)),
        w_diversity=float(cfg.get("importance_w_diversity", 0.2)),
    )

    # 3. Traffic potential = external volume + external diversity + our coverage
    vol_v = traffic_volume(ext.get("external_article_count"),
                           cap=int(cfg.get("traffic_article_cap", 50)))
    ext_src_v = traffic_source_diversity(ext.get("external_source_count"),
                                         cap=int(cfg.get("traffic_source_cap", 10)))
    cov_v = cross_site_coverage(source_count,
                                source_cap=int(cfg.get("traffic_diversity_cap", 3)))
    tp = traffic_potential(
        volume_v=vol_v, source_diversity_v=ext_src_v, coverage_v=cov_v,
        w_volume=float(cfg.get("traffic_w_volume", 0.4)),
        w_sources=float(cfg.get("traffic_w_sources", 0.3)),
        w_diversity=float(cfg.get("traffic_w_diversity", 0.3)),
    )

    # 4. Cross-site coverage
    csc = cross_site_coverage(source_count,
                              source_cap=int(cfg.get("cross_site_source_cap", 5)))

    # Combined 4D score (weights normalised by _weighted)
    score = _weighted([
        (fresh, float(cfg.get("weight_freshness", 0.25))),
        (imp, float(cfg.get("weight_importance", 0.25))),
        (tp, float(cfg.get("weight_traffic_potential", 0.25))),
        (csc, float(cfg.get("weight_cross_site_coverage", 0.25))),
    ])

    # Legacy combined (kept for backward compat callers)
    qual = quality(completeness_v=comp, recency_v=rec, material_v=mat,
                   w_completeness=float(cfg["weight_completeness"]),
                   w_recency=float(cfg["weight_recency"]),
                   w_material=float(cfg["weight_material"]))
    sc_legacy = combined(conf, qual, w_confidence=float(cfg["weight_confidence"]),
                         w_quality=float(cfg["weight_quality"]))

    return {
        "confidence": conf,
        "quality": qual,
        "score": score,
        "score_legacy": sc_legacy,
        "freshness": fresh,
        "importance": imp,
        "traffic_potential": tp,
        "cross_site_coverage": csc,
        "external_article_count": ext.get("external_article_count"),
        "external_source_count": ext.get("external_source_count"),
        "external_latest_at": ext.get("external_latest_at"),
        "search_volume_proxy": ext.get("search_volume_proxy"),
    }


# --- legacy entry point (delegates to v2 when called without external data) ----


def score_cluster(cluster: dict, members: list[dict], now: str, cfg: dict) -> dict:
    """Legacy entry point: delegates to ``score_cluster_v2`` with no external data.
    Returns the same shape as before (confidence, quality, score) for backward
    compat callers, while the v2 result carries all 4D fields as extras.
    """
    return score_cluster_v2(cluster, members, now, cfg, external=None)
