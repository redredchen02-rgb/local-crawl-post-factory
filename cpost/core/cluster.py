"""Aggregate library items into scoops (clusters) by content similarity (plan U3).

Clustering is a *view* over the library: callers assign a ``cluster_id`` to each
item but never drop library rows. Two items join the same scoop when their
titles are similar (character n-gram Jaccard >= threshold) and, when both carry
a ``published_at``, fall within a time window.

Grouping is transitive (connected components via union-find), language-agnostic
(character n-grams handle Chinese and English alike, since CJK text has no word
spaces), and deterministic: the same item set always yields the same clusters,
and a cluster's id is derived from its sorted member URLs so reruns are stable.

Complexity is O(n^2) in the number of library items -- fine at the single-machine
"tens of items per run" scale this tool targets; revisit only if measured slow.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from cpost.core.timeutil import parse_iso

# Strip whitespace, punctuation and underscores; \W keeps Unicode word chars
# (including CJK) so the n-grams compare the meaningful characters only.
_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def _normalize_title(title: str) -> str:
    return _STRIP.sub("", (title or "").lower())


def _ngrams(text: str, n: int) -> set[str]:
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _within_window(p1: str | None, p2: str | None, hours: float) -> bool:
    """True when both timestamps fall within ``hours``; missing time = no constraint."""
    d1, d2 = parse_iso(p1), parse_iso(p2)
    if d1 is None or d2 is None:
        return True
    return abs((d1 - d2).total_seconds()) <= hours * 3600


def _published_key(value: str) -> datetime:
    """Chronological sort key: the aware instant, or epoch UTC if unparseable."""
    return parse_iso(value) or datetime.min.replace(tzinfo=timezone.utc)


def _cluster_id(member_urls: list[str]) -> str:
    key = "\n".join(sorted(member_urls))
    return "c_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def cluster_items(items: list[dict], *, ngram: int = 2,
                  similarity_threshold: float = 0.5,
                  time_window_hours: float = 72) -> list[dict]:
    """Group library items into scoops.

    Each item is a dict with at least ``canonical_url`` and ``title``; optional
    ``source_id``, ``source_text`` and ``published_at`` enrich the per-cluster
    summary. Returns a list of cluster dicts (sorted by ``cluster_id``)::

        {cluster_id, members: [url, ...], member_count, source_count,
         representative_url, representative_title,
         earliest_published, latest_published}

    ``source_count`` counts *distinct* ``source_id`` values -- an INFORMATIONAL
    signal only, NOT corroboration: members are keyed by ``canonical_url``, so
    mirrors/reposts sharing a URL collapse to one row and ``source_count`` cannot
    represent "same URL, N sources" (best-effort). Confidence scoring is
    neutralized (``weight_confidence: 0.0``), so this never drives ranking. The
    representative is the member with the longest ``source_text`` (most material),
    tie-broken by ``canonical_url`` for determinism.
    """
    items = sorted(items, key=lambda it: it["canonical_url"])
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    grams = [_ngrams(_normalize_title(it.get("title", "")), ngram) for it in items]
    for i in range(n):
        for j in range(i + 1, n):
            if (_jaccard(grams[i], grams[j]) >= similarity_threshold
                    and _within_window(items[i].get("published_at"),
                                        items[j].get("published_at"),
                                        time_window_hours)):
                union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    clusters = [_summarize([items[k] for k in idxs]) for idxs in groups.values()]
    clusters.sort(key=lambda c: c["cluster_id"])
    return clusters


def _summarize(members: list[dict]) -> dict:
    urls = [m["canonical_url"] for m in members]
    rep = max(members, key=lambda m: (len(m.get("source_text") or ""), m["canonical_url"]))
    # Sort by the parsed aware instant, not the raw string: mixed-offset values
    # (e.g. +08:00 vs +00:00) order differently lexically than chronologically.
    # Keep the original string for display. Unparseable strings sort first (oldest)
    # via a min-datetime fallback so they never crash on None comparison.
    published = sorted(
        (p for p in (m.get("published_at") for m in members) if p),
        key=_published_key,
    )
    sources = {m.get("source_id") for m in members if m.get("source_id")}
    return {
        "cluster_id": _cluster_id(urls),
        "members": sorted(urls),
        "member_count": len(members),
        "source_count": len(sources),
        "representative_url": rep["canonical_url"],
        "representative_title": rep.get("title"),
        "earliest_published": published[0] if published else None,
        "latest_published": published[-1] if published else None,
    }
