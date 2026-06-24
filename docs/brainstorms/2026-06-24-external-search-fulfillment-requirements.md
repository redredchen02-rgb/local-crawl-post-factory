---
date: 2026-06-24
topic: external-search-fulfillment
---

# External Search Fulfillment

## Problem Frame

`cpost/core/external_search.py:search_cluster()` is a documented placeholder that always returns `{external_article_count: None, external_source_count: None, ...}`. As a result, two sub-dimensions of the 4D scoring pipeline — `traffic_volume` and `traffic_source_diversity` inside `traffic_potential` — permanently evaluate to 0, making the 4D scoop ranking structurally half-blind regardless of how the scoring weights are tuned.

The integration scaffolding is already complete: `scoring.py` consumes the return value correctly, config keys exist (`external_search_enabled`, `external_search_engines`, `traffic_article_cap`, `traffic_source_cap`), and the return shape is defined. Only the HTTP call and parsing logic are missing.

## Data Flow

```
cluster.representative_title
         │
         ▼
   _best_title() → query string (truncated)
         │
         ├──► Google News RSS (primary)
         │         https://news.google.com/rss/search?q=...
         │         parse <item> count, unique domains, max pubDate
         │
         └──► DuckDuckGo HTML lite (fallback, on primary failure)
                   https://lite.duckduckgo.com/lite/?q=...
                   parse result snippets count, unique domains
         │
         ▼
   {external_article_count, external_source_count,
    external_latest_at, search_volume_proxy}
         │
         ▼
   score_cluster_v2(..., external={...})
   → traffic_potential dimension (0.25 of final score)
```

## Requirements

**Query Construction**
- R1. Derive the search query from `cluster["representative_title"]` via the existing `_best_title()` helper.
- R2. Truncate the title to at most 10 words before sending as a query to avoid over-specific zero-result searches.
- R3. Strip leading/trailing whitespace and collapse internal whitespace. Do not strip punctuation (phrases like "X vs Y" or "A：B" may be meaningful).

**Primary Backend — Google News RSS**
- R4. Query `https://news.google.com/rss/search?q={encoded_query}` with a configurable locale parameter (default: unset, returning mixed-language results; operator may set `external_search_locale: "zh-TW"` to scope to Traditional Chinese news).
- R5. Set a `User-Agent` header identifying the client (e.g., `cpost/1.0`) to reduce bot-rejection risk.
- R6. Parse the XML response: count `<item>` elements as `external_article_count`; extract unique second-level domains from `<link>` values as `external_source_count`; take the maximum `<pubDate>` value (ISO 8601) as `external_latest_at`.
- R7. Cap parsed counts at config values (`traffic_article_cap`, `traffic_source_cap`) before returning — do not let raw counts exceed normalization bounds.
- R8. Use `search_volume_proxy = external_article_count` as a simple proxy until a more accurate signal is available.

**Fallback Backend — DuckDuckGo HTML Lite**
- R9. If the primary backend raises an exception or returns a non-2xx status, retry once, then fall back to `https://lite.duckduckgo.com/lite/?q={encoded_query}`.
- R10. Set the same `User-Agent` header on DDG requests.
- R11. Parse the HTML response: count result snippet `<a class="result-link">` elements (or equivalent selector) as `external_article_count`; extract unique domains from result URLs as `external_source_count`. Set `external_latest_at = None` (DDG lite does not expose publication dates reliably).
- R12. If the fallback also fails, return `_empty()` — do not raise an exception.

**Caching**
- R13. Cache search results in a module-level in-memory dict keyed by the normalized query string (lowercase, stripped).
- R14. Each cache entry records the result dict and the timestamp of the search. Entries are considered valid for `external_search_cache_ttl_hours` hours (default: 4; add this key to `scoring_config.py` DEFAULTS and `scoring.yaml`).
- R15. On cache hit within TTL, return the cached result without making any HTTP request.
- R16. The cache does not persist across process restarts (in-memory is sufficient; the pipeline is short-lived).

**Timeouts and Graceful Degradation**
- R17. Each HTTP request (primary and fallback) uses the existing `external_search_timeout` config key (default: 10s) as a connection + read timeout.
- R18. Any exception during fetching or parsing — network error, XML parse error, timeout, unexpected HTML structure — causes that backend to be considered failed; the fallback is tried next. If both fail, `_empty()` is returned and the failure is logged at WARNING level (not ERROR, since scoring continues with zero signal).
- R19. The `external_search_enabled` config gate is respected: when `False`, `search_cluster()` returns `_empty()` immediately without any HTTP calls or cache interaction.

**Configuration**
- R20. Add `external_search_cache_ttl_hours: 4` to `scoring_config.py` DEFAULTS and `scoring.yaml`.
- R21. Add `external_search_locale: ""` to `scoring_config.py` DEFAULTS and `scoring.yaml` (empty string = no locale filter; set to e.g. `"zh-TW"` for Traditional Chinese news). Note: locale applies to Google News RSS only; DDG fallback ignores this setting.
- R22. Flip `external_search_enabled` default to `true` in `scoring.yaml` once the implementation is validated locally.

## Success Criteria

- For a representative sample of 10 clusters with `external_search_enabled: true`, ≥ 80% return non-None `external_article_count`.
- Scoop scores for clusters about widely-covered topics (e.g., major entertainment news) increase measurably in `traffic_potential` compared to scores with search disabled.
- Pipeline prep runs complete without errors or warnings attributable to external search failures (failures degrade gracefully to zero signal, not exceptions).
- A single prep run with 10 clusters makes at most 10 HTTP requests (cache prevents re-queries for duplicate or near-duplicate titles within the same run).

## Scope Boundaries

- No paid API keys, SerpAPI, Bing, or Google Custom Search API — only zero-credential endpoints.
- No persistent cache (no new SQLite table); in-memory TTL cache is sufficient for this stage.
- No result deduplication across clusters with different titles that happen to describe the same story.
- No language detection or automatic locale inference — locale is a static config setting.
- No changes to how `scoring.py` consumes the `external` dict — the interface contract is frozen.

## Key Decisions

- **Google News RSS as primary, DDG lite as fallback**: RSS returns structured data (titles, URLs, dates) that maps directly to the required return shape. DDG lite is HTML-parsed and loses publication dates, but still provides article count and domain diversity. Both avoid API keys.
- **In-memory TTL cache, not SQLite**: Pipeline runs are short-lived and the data refreshes frequently. Adding a new SQLite table for cache would outlast its usefulness. In-memory is simpler and sufficient.
- **Truncate query to 10 words**: Over-specific queries (full headline) often return 0 results. Shorter queries trade precision for recall, which is appropriate here — we want a volume signal, not exact match.
- **Log WARNING not ERROR on failure**: External search failure degrades scoring gracefully. It is not a pipeline error that warrants operator attention.

## Dependencies / Assumptions

- `external_search.py` already has `_best_title()`, `_empty()`, and the function signature — these are not changed.
- `scoring.py:score_cluster_v2()` already accepts `external: dict | None` — no changes to scoring interface.
- The pipeline runs in an environment with outbound HTTPS access to `news.google.com` and `lite.duckduckgo.com`.
- Content topics are primarily Traditional Chinese entertainment/gossip news — Google News RSS with `zh-TW` locale will likely return more relevant results than unlocalized queries.

## Outstanding Questions

### Resolve Before Planning
_(none — all product decisions resolved)_

### Deferred to Planning
- [Affects R6][Technical] What is the exact XML structure of Google News RSS responses for Chinese queries? Confirm `<item>/<link>` vs `<item>/<guid>` for URL extraction.
- [Affects R11][Technical] What is the reliable CSS/HTML selector for result links in DuckDuckGo HTML lite? May need to inspect a live response.
- [Affects R2][Needs research] Is a 10-word truncation appropriate for Chinese titles, which are denser? May need a character-count truncation (e.g., 30 chars) rather than word-count for CJK text.

## Next Steps
→ `/ce:plan` for structured implementation planning
