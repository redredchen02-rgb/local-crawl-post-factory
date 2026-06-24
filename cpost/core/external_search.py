"""Optional external-source search for 4D scoring v2 (plan scoring-pipeline-v2).

Searches the open web for articles related to a cluster's topic, returning
counts that ``score_cluster_v2`` feeds into the ``traffic_potential`` dimension.
Config-gated via ``external_search_enabled`` in scoring config; when disabled or
when the search fails, returns a zero-signal dict so scoring never blocks.

The function signature is deliberately simple so callers in the scoring hot path
never need to branch on the disabled state.
"""

from __future__ import annotations


def search_cluster(cluster: dict, members: list[dict], cfg: dict) -> dict:
    """Search external sources for articles related to this cluster's topic.

    Parameters
    ----------
    cluster : dict
        Raw cluster row (must contain ``representative_title`` or ``cluster_id``).
    members : list[dict]
        Cluster member items (each with optional ``title``, ``source_text``).
    cfg : dict
        Scoring config dict; respects ``external_search_enabled``,
        ``external_search_max_results``, and ``external_search_engines``.

    Returns
    -------
    dict
        Shape matching ``score_cluster_v2``\\'s ``external`` parameter:
        ``{external_article_count, external_source_count,
          external_latest_at, search_volume_proxy}``.
        When search is disabled or fails, all values are None (scoring falls
        back to zero for sub-dimensions that depend on them).
    """
    enabled = cfg.get("external_search_enabled", False)
    if not enabled:
        return _empty()

    # --- build a query from the best available title ---
    title = (
        cluster.get("representative_title")
        or _best_title(members)
    )
    if not title:
        return _empty()

    # Currently: placeholder that returns empty (search integration deferred).
    # Future implementations can plug in DuckDuckGo / SerpAPI / RSS here.
    _ = title  # reserved for future query construction
    _ = int(cfg.get("external_search_max_results", 5))
    _ = cfg.get("external_search_engines", ["duckduckgo"])
    return _empty()


def _best_title(members: list[dict]) -> str | None:
    """Return the longest non-empty title among members (best proxy for topic)."""
    titles = [m.get("title") or "" for m in members]
    valid = [t for t in titles if t.strip()]
    if not valid:
        return None
    return max(valid, key=len)


def _empty() -> dict:
    return {
        "external_article_count": None,
        "external_source_count": None,
        "external_latest_at": None,
        "search_volume_proxy": None,
    }
