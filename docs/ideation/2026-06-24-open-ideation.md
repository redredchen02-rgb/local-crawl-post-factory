---
date: 2026-06-24
topic: open-ideation
focus: open-ended
---

# Ideation: cpost Pipeline Improvements

## Codebase Context

**Project shape:** Python 3.11+ local-first CLI + FastAPI/HTMX WebUI. Pipeline: Crawl → Normalize → Library Ingest → Cluster Scoops → Score → Generate Article → Draft → Verify → Publish. 65 Python files, 963 tests, 94% coverage.

**Notable patterns:** NDJSON I/O contract (stdout/stderr/exit), Jobs system for async UI ops, Scrapy via subprocess (reactor constraint), WAL mode + busy_timeout on SQLite, mypy strict on core+cli only.

**Obvious pain points:**
- N=1 sequential crawl (largest time bottleneck)
- Playwright browser flakiness (untracked root causes)
- `external_search.py` returns `None` always — 2 of 4 scoring dimensions are blind
- No article quality feedback loop
- Stale/published scoops resurface in active queue
- LLM generation has no timeout, retry, or output schema validation

**Key learnings from prior plans:**
- Scrapy must run in subprocess (can't restart reactor in same process)
- `library.upsert()` ON CONFLICT preserves old source_id — user URL submissions may be misattributed
- WAL mode + busy_timeout already set (concurrent DB access is safe at low parallelism)
- Jobs system is the standard async UI pattern — new async ops follow this
- `source_id IS NULL` edge case exists in old data

---

## Ranked Ideas

### 1. External Search Fulfillment
**Description:** `external_search.py:search_cluster()` currently returns `None` — it's an explicit placeholder. Wire a real implementation: build a query from `representative_title`, fetch results via DuckDuckGo's unofficial JSON endpoint or a configurable RSS list (no API key required, same urllib pattern as llm.py), parse `article_count`, `source_count`, and `latest_at`, and return real signal. All scaffolding (call sites, config keys, fallback logic) already exists in scoring_config.py and score_scoops.py.
**Rationale:** Two of four 4D scoring dimensions — `traffic_potential` and `cross_site_coverage` — fall back to zero when external search returns None. The entire scoring model is structurally half-blind. This is the highest-ROI completion gap in the codebase: all scaffolding exists, only the network call is missing.
**Downsides:** DuckDuckGo unofficial endpoint may rate-limit or change without notice; need a configurable fallback and graceful degradation.
**Confidence:** 92%
**Complexity:** Low
**Status:** Explored — brainstorm initiated 2026-06-24

---

### 2. Eliminate / Simplify Browser Verify Step
**Description:** The verify-draft Playwright step exists to confirm a draft was saved. Three independent paths to simplify it: (a) **Extract confirmation from draft creation response** — `backend_driver.create_draft()` already checks for `draft_success_text` at creation time; capturing `post_id` from this response and writing it to the manifest as `draft_confirmed_id` makes the separate verify round-trip redundant; (b) **API-only check** — if the CMS exposes a REST endpoint, replace the Playwright session with an HTTP GET; (c) **Collapse drafted→verified as one state transition**. The verify step becomes a no-op.
**Rationale:** Browser automation is the documented #1 reliability risk. Eliminating the verify browser trip removes the entire failure class (login expiry, selector drift, timeouts) from the most common pipeline path. The draft creation success signal already exists — it just isn't persisted.
**Downsides:** Requires understanding the specific CMS's response contract; legacy repost track may still need verify separately.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

---

### 3. Parallel Source Crawl
**Description:** Replace the sequential `for source_cfg in sources:` loop in `crawl_all_sources()` with `concurrent.futures.ProcessPoolExecutor` (or `ThreadPoolExecutor` + subprocess calls), capped at `max_parallel_crawls=2–3` via config. Each `crawl_items()` call is already an independent subprocess — no shared Twisted reactor, no shared in-process state. The only shared resource is SQLite, which WAL mode + busy_timeout already handles at low concurrency.
**Rationale:** With 10 sources at 2 min/source, sequential crawl is 20 minutes. Parallel at n=3 brings it to ~7 min. This is the single largest wall-clock reduction available. Appeared in 5 of 6 ideation frames — highest convergence.
**Downsides:** Critic A flagged WAL write contention. At n=2–3 this is manageable (WAL serializes writes; busy_timeout prevents deadlock). At n=10+ it becomes risky. Must cap conservatively and test.
**Confidence:** 78%
**Complexity:** Low-Medium
**Status:** Unexplored

---

### 4. LLM Circuit Breaker + Output Validation
**Description:** Wrap every `llm.chat()` call in: (a) hard wall-clock timeout via `asyncio.wait_for` (e.g. 45s), (b) exponential-backoff retry (3 attempts, jitter), (c) post-response JSON schema validation against expected article structure (title present, body non-empty, tags is a list). If schema fails after retries, raise a typed `ArticleGenerationError` with the raw response attached. Circuit opens after 3 consecutive failures, skipping generation for that run rather than hanging.
**Rationale:** LLM calls currently have no timeout guard. A single hung call freezes the entire pipeline indefinitely. Silent malformed output (tags as string, empty body) propagates into drafts and eventually publish. Both failures are documented gaps.
**Downsides:** Circuit breaker logic adds state; need to decide retry-vs-fail semantics clearly.
**Confidence:** 90%
**Complexity:** Low
**Status:** Unexplored

---

### 5. Scoop List Quality (Cluster Decay + Suppression)
**Description:** Two low-cost changes that together clean up the active scoop list: (a) **Cluster Decay** — add a `status` column to `library_clusters` with an `expired` state; a maintenance pass (or scoring pass) sets `expired` when `freshness_score = 0` or `latest_published` age exceeds a configurable TTL. The active queue filters `WHERE status != 'expired'`; (b) **Scoop Suppression** — add a `suppressed_until` column (or boolean flag) on clusters; when a scoop is published, mark it suppressed for a configurable TTL (e.g. 72h), preventing it from re-appearing in subsequent runs. Both are additive schema changes + WHERE clause additions.
**Rationale:** Freshness=0 scoops and already-published topics both reappear in the operator's daily queue, requiring manual skip decisions. These are direct decision-fatigue sources. Both fixes are near-single-line WHERE clause changes on top of already-computed signals.
**Downsides:** TTL values need tuning; suppression should be lifted if the cluster gains significant new members.
**Confidence:** 92%
**Complexity:** Very Low
**Status:** Unexplored

---

### 6. Actionable Failure Recovery (Job Error Classification + Draft Re-queue)
**Description:** Two connected improvements to failure UX: (a) **Job error_class** — add `error_class: str | None` to `Job` in `jobs.py`; populate from `pipeline.py`'s existing `_error_class()` which already returns 'validation'/'system'/'session_expired'. WebUI renders a targeted recovery action per class: "Retry" for system errors, "Re-login" for session expiry, "Fix data" for validation; (b) **Draft re-queue** — add a `verify_failure_reason` enum to the manifest's backend section (populated by `backend_driver.verify_draft`); add a per-item "Retry from Draft" WebUI action that restarts from the draft stage without re-crawling.
**Rationale:** Job failures currently surface as raw Python exception strings with no recovery path. Operators facing a verify failure must re-run the entire pipeline. Both fixes connect existing machinery (`_error_class()` already exists, `runs` already records severity) rather than building new infrastructure.
**Downsides:** Per-item re-queue needs careful state machine design to avoid partial-state corruption.
**Confidence:** 88%
**Complexity:** Very Low
**Status:** Unexplored

---

### 7. Browser Failure Classification → audit.jsonl
**Description:** When Playwright fails, classify the exception into a fixed taxonomy: `login_expired | selector_not_found | timeout | navigation_error | rate_limited`. Append this `failure_type` tag to `audit.jsonl` via `audit.record(**extra)` alongside URL, step name, and timestamp. The exception types (`SessionExpiredError`, `PlaywrightTimeout`) are already distinct — classification is 5–10 lines of mapping logic. `audit.py`'s `record()` already accepts `**extra` kwargs.
**Rationale:** Browser failure root causes are currently untracked. Every fix today is reactive and invisible. A queryable taxonomy turns "why did verify fail last week?" from screenshot archaeology into a one-liner query. This is also the diagnostic prerequisite for any more sophisticated browser hardening (#2, #3).
**Downsides:** Classification may miss edge cases; taxonomy needs maintenance as new failure modes emerge.
**Confidence:** 95%
**Complexity:** Very Low
**Status:** Unexplored

---

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 3 | Article Feedback Loop | 4D scoring unvalidated in production; adjusting weights on noisy signals compounds error |
| 6 | Scoop Cache Membership Tracking | Cluster membership drift is edge case; bookkeeping overhead not justified at current scale |
| 7 | Prompt Version Registry + A/B | Requires publication volume for statistical significance; git already versions prompts |
| 10 | LLM-as-judge quality gate | Doubles LLM cost with no calibration baseline; quality definition undefined pre-feedback-loop |
| 11 | LLM Generation Cache Prewarming | Speculative generation burns tokens before scoop selection; cold hit rate too low |
| 13 | SSE Real-time Dashboard | Callback hooks exist but 1–2 operator local use case doesn't justify SSE infrastructure |
| 15 | Selector Pre-flight Check | Pre-flight itself requires browser; fails in real DOM only — doesn't avoid browser startup |
| 16 | Incremental Re-cluster on Ingest | O(n) clustering triggered on every URL ingest blocks UI; lazy cluster already fine |
| 17 | NDJSON Stage Replay | Semantically correct replay requires DB state injection — much harder than it looks |
| 18 (standalone) | Gossip URL as Pipeline Seed | Valuable but subsumed by Rank 2 (verify simplification opens this path naturally) |
| 19 | YAML Config Schema Validation | webui_config.py already has manual ValidationError (lines 104–182); net gain near zero |
| 20 | CI Test Matrix Split | CI not an operator pain point; 963 tests healthy in single matrix |
| 22 | Cluster Drift Alerting | Needs historical baseline; alerting before stable baseline creates alert fatigue |
| 23 | source_id NULL Migration | Real correctness bug but not operator-visible; deferred to planned data hygiene work |
| 24 | Multi-Article Format Generation | Multiplies LLM cost and review burden; no evidence of multi-channel distribution need |
| 25 | Score Before Crawl | Cold-start problem; historical yield data doesn't exist yet; premature for 5–10 sources |
| 27 | SQLite WAL Monitor | WAL bloat not a known pain point; SQLite auto-checkpoints at 1000 pages by default |
| 28 | Velocity-Aware Crawl Scheduling | Requires per-source history not yet accumulated; overkill for 5–10 sources |
| 29 | runs Table Analytics CLI | Builds analytical tooling for an unasked question; operator pain is in execution not retrospection |
| 30 | Mypy Coverage Ratchet | browser/webui intentionally tiered lower; ratchet breaks deliberate layered approach |
| 31 | Smart Source Management (combo) | Score-Before-Crawl and Velocity Scheduling both rejected; combo doesn't rescue them |
| 32 | Closed-Loop Article Quality (combo) | All three components rejected; packaging doesn't change the individual verdicts |
| 4 | Source Health Heatmap | Small team already knows their sources; heatmap is over-engineering for 5–10 sources |

## Session Log
- 2026-06-24: Initial ideation — 48 raw candidates generated (6 frames × 8 ideas), merged to 33 unique (including 3 cross-cutting combos), 7 survived adversarial filtering
- 2026-06-24: Brainstorm initiated on #1 External Search Fulfillment
