"""Load clustering + scoring tunables from configs/scoring.yaml (plan U3/U4).

Flat key/value config merged over DEFAULTS, mirroring the lightweight YAML
loaders elsewhere (src/render_caption.py, browser/selector_recipe.py) -- no
pydantic. Thresholds and weights live here so calibrating against real
multi-source data is a config edit, not a code change.
"""

from pathlib import Path

import yaml

from cpost.core.errors import ValidationError

DEFAULTS = {
    # clustering (cpost.core.cluster)
    "ngram": 2,
    "similarity_threshold": 0.5,
    "time_window_hours": 72,
    # scoring (cpost.core.scoring)
    "confidence_source_cap": 3,
    "quality_full_text_chars": 1000,
    "quality_recency_window_hours": 168,
    "quality_material_cap": 3,
    "weight_completeness": 0.5,
    "weight_recency": 0.2,
    "weight_material": 0.3,
    # Confidence axis neutralized (plan U9): combined() ignores it, ranking by
    # quality alone. Mirrors configs/scoring.yaml so the in-code default holds
    # even when no yaml is loaded.
    "weight_confidence": 0.0,
    "weight_quality": 1.0,
    # --- 4D scoring (freshness / importance / traffic_potential / cross_site_coverage) ---
    # Top-level weights for the four new dimensions (normalised in score_cluster_v2).
    "weight_freshness": 0.25,
    "weight_importance": 0.25,
    "weight_traffic_potential": 0.25,
    "weight_cross_site_coverage": 0.25,
    # Freshness sub-dimensions: recency (latest article age) + velocity (article burst).
    "freshness_w_recency": 0.6,
    "freshness_w_velocity": 0.4,
    "freshness_velocity_window_hours": 24,   # burst detection window (articles/hours)
    # Importance sub-dimensions: text completeness + member count + text diversity.
    "importance_w_completeness": 0.5,
    "importance_w_material": 0.3,
    "importance_w_diversity": 0.2,
    "importance_diversity_threshold": 0.3,   # Jaccard threshold for "different content"
    # Traffic potential sub-dimensions: external article volume + source diversity.
    "traffic_article_cap": 50,
    "traffic_source_cap": 10,
    "traffic_diversity_cap": 3,
    "traffic_w_volume": 0.4,
    "traffic_w_sources": 0.3,
    "traffic_w_diversity": 0.3,
    # Cross-site coverage: distinct source_id count.
    "cross_site_source_cap": 5,
    # --- External search (cpost.core.external_search) ---
    "external_search_enabled": False,
    "external_search_timeout": 10,
    "external_search_max_per_source": 10,
    # actionable filter (plan U5): CLI --min-sources reads this as default;
    # 0 = no filter (backward-compatible).
    "actionable_min_sources": 0,
    # mirror detection (U3): candidate canonical URL overlap fraction above this
    # value triggers MIRROR classification (health-check-sources).
    "mirror_overlap_threshold": 0.6,
}


def load(path: str | None = None) -> dict:
    """Return DEFAULTS merged with the YAML at ``path`` (unknown keys ignored).

    Every recognized value is coerced to its default's numeric type so a typo in
    the YAML (e.g. ``ngram: abc``) fails fast as a ValidationError (exit 2) at
    load time, rather than as a bare ValueError (exit 5) deep inside scoring.
    """
    cfg = dict(DEFAULTS)
    if not path:
        return cfg
    p = Path(path)
    if not p.exists():
        return cfg
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid scoring config {path!r}: {exc}")
    if not isinstance(raw, dict):
        raise ValidationError(f"scoring config {path!r} must be a mapping")
    for key, value in raw.items():
        if key not in DEFAULTS:
            continue
        coerce = type(DEFAULTS[key])  # int or float
        try:
            cfg[key] = coerce(value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"scoring config {key!r} must be {coerce.__name__}, got {value!r}")
    return cfg
