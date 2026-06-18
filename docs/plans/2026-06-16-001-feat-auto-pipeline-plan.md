---
title: "feat: Add auto-pipeline mode for one-click crawl→publish"
type: feat
status: superseded
date: 2026-06-16
origin: docs/brainstorms/2026-06-16-auto-pipeline-requirements.md
superseded_by: docs/plans/2026-06-18-001-fix-workflow-pipeline-stabilization-plan.md
---

# feat: Add auto-pipeline mode for one-click crawl→publish

> Superseded on 2026-06-18 by `docs/plans/2026-06-18-001-fix-workflow-pipeline-stabilization-plan.md`. The auto-pipeline feature has moved into the current codebase; remaining work should follow the stabilization plan, which reconciles docs, result rendering, public stage contracts, and operator recovery.

## Overview

Add an `auto_pipeline` boolean toggle to WebUI settings. When enabled, the `/crawl` endpoint automatically chains draft→verify→publish for all newly built packages after crawl completes — no manual per-package interaction required. The existing reviewed gate (Gate ①) is bypassed via `reviewed.mark()` before calling `check_publish_gates()`; Gates ② and ③ remain active. Failed stages retry up to 3 times (1s delay); failures are logged per-item and do not block remaining items. Manual mode (default OFF) is completely unchanged.

## Problem Frame

Users run the crawl→draft→verify→publish pipeline daily. Each stage requires a separate manual trigger in the WebUI, with a review gate between packages. The auto-pipeline feature eliminates this friction by executing the full chain in one job, with results visible in the existing history page.

(see origin: docs/brainstorms/2026-06-16-auto-pipeline-requirements.md)

## Requirements Trace

- R1–R3. `auto_pipeline` boolean in `webui.yaml`, persisted, settings page shows warning when ON
- R4. `/crawl._work()` triggers auto draft→verify→publish after `run_pipeline()` when `auto_pipeline=True`
- R5. Bypass Gate ① (reviewed) via `reviewed.mark()`; Gate ② (draft_verified) and Gate ③ (title) remain enforced
- R6. Progress logged via existing `jobs.report()` / `jobs.set_current()` in same job stream
- R7. End-of-run summary: success / failed / skipped counts (with skip reason breakdown)
- R8–R10. Per-stage retry max 3 attempts, 1s between retries; failure after retries → log + skip item, continue batch

## Scope Boundaries

- No cron/schedule automation (user still presses "start crawl")
- No macOS notifications or Telegram push
- No multi-site management
- `check_publish_gates()` signature **not modified** — bypass is a caller-side concern
- `batch_action()` endpoint **not modified** — auto-pipeline uses its own in-job loop

## Context & Research

### Relevant Code and Patterns

- `core/webui_config.py` — `DEFAULTS` dict (L14-33), `load()` (L54), `save()` (L113); bool fields need no `_INT_FIELDS` entry — add `auto_pipeline: False` directly to DEFAULTS
- `webui/app.py` — `batch_action()` (L203-252): the closest existing pattern for per-item iteration with per-item error isolation; `_submit_job()` (L254-287): daemon-thread job pattern; `check_publish_gates()` (L360-372): three sequential gates
- `core/reviewed.py` — `mark(path, post_id, cid)` (L72): upsert to `reviewed` table; `content_id(manifest)` (L36): SHA-256 of title+body+canonical_url; `get(path, post_id) -> str | None` (L82)
- `core/jobs.py` — `submit(fn)` (L35), `report(job, msg)` (L64, append-only), `set_current(job, msg)` (L69, overwrite)
- `src/draft_post._run`, `src/verify_draft._run`, `src/publish_post._run` — all take `SimpleNamespace(args)`, require `args.approve=True` for publish
- `webui/templates/settings.html` — existing form fields pattern to follow for new toggle

### Institutional Learnings

- No `docs/solutions/` in this repo — no prior learnings to carry forward

### External References

- Not required — local patterns are sufficient and well-established

## Key Technical Decisions

- **Gate ① bypass via `reviewed.mark()`**: In auto-mode, before calling `check_publish_gates()`, call `reviewed.mark(state_path, post_id, content_id(manifest))`. This makes Gate ① pass legitimately without modifying `check_publish_gates()` or duplicating it. Side effect: package appears as "reviewed" in the manual review UI — acceptable because auto-mode is an explicit user opt-in.

- **Gate ③ (title) bypass via manifest title**: Pass `manifest["content"]["title"]` as both `submitted_title` and `manifest_title` to `check_publish_gates()`. Gate ③ compares them and passes when equal — no gate logic change needed.

- **Same job stream as crawl**: Auto-pipeline runs inside `/crawl`'s `_work(job)` closure after `run_pipeline()` returns. The frontend's existing polling mechanism follows the same job to completion — no new endpoint, no secondary job_id to track.

- **Per-stage retry, not per-item full retry**: Retry is applied independently to draft, verify, and publish for each item. If draft succeeds but verify fails all retries, the item is marked failed and skipped for publish. This matches the existing sequential gate model.

- **Verify-filter before publish**: After the draft loop and verify loop complete, auto-pipeline reads current status from manifest/DB and only attempts publish for packages with `status == "draft_verified"`. Packages that failed verify are counted as "skipped (verify failed)" in the summary, not "publish failed".

- **`_retry()` helper in `webui/app.py`**: Module-private helper `_retry(fn, times=3, delay=1.0) -> tuple[Any, Exception | None]` to avoid duplicating try/sleep/loop logic across stages.

- **`_action_ns` must be extracted to module level**: Currently `_action_ns` is a local function defined inside the `start_crawl` request handler closure (L179-194). `_run_auto_pipeline` is a module-level function and cannot call a closure-local helper. The fix is to extract `_action_ns(post_id, stage, cfg)` (adding `cfg` as an explicit parameter) to module scope. All existing callers inside `start_crawl` are updated to pass `cfg` explicitly. This is a prerequisite for Unit 3.

## Open Questions

### Resolved During Planning

- **C3 (same job vs. job chain)**: Same job. Confirmed by research — `jobs.submit` is fire-and-forget; keeping same `_work` closure is the simplest path and keeps the frontend polling working without changes.
- **C2 (Gate ① bypass mechanism)**: `reviewed.mark()` caller-side — no `check_publish_gates` signature change needed.
- **I5 (Gate ③ in auto-mode)**: Use manifest title as submitted_title — self-consistent pass, not a skip.
- **C1 (verify-fail → publish behavior)**: Filter by `draft_verified` status before publish loop — cleaner than relying on gate rejection.
- **`run_pipeline()` return value**: Returns `{"built": [{"post_id": str, "title": str, "manifest_path": str}, ...], "failed": [...], "skipped": int}`. Auto-pipeline iterates `result["built"]` to get post_ids and manifest paths — no filesystem scan needed.

### Deferred to Implementation

- **I2 (draft idempotency)**: Whether `draft_post._run` is a no-op for already-drafted packages needs confirmation at implementation time. If not idempotent, auto-pipeline loop should skip packages not in `package_built` state before drafting.
- **M3 (job status for partial success)**: Whether to add a `done-with-errors` status to the `Job` model or rely on the summary message alone — decide at implementation based on how the existing progress template renders.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
/crawl._work(job)
  ├── crawl_items(cfg)              # existing
  ├── run_pipeline(items, cfg)  →  built: list[post_id]   # existing
  │
  └── [auto_pipeline=True only]
       _run_auto_pipeline(job, cfg, built)
         │
         ├── [built is empty] → jobs.report("無新稿件") → return
         │
         ├── DRAFT LOOP  for post_id in built:
         │     _retry(lambda: draft_post._run(ns), 3, 1.0)
         │     → success: continue  |  fail: mark failed, skip
         │
         ├── VERIFY LOOP  for post_id in drafted_ok:
         │     _retry(lambda: verify_draft._run(ns), 3, 1.0)
         │     → success: continue  |  fail: mark failed, skip
         │
         ├── PUBLISH FILTER  post_ids where status=="draft_verified"
         │
         ├── PUBLISH LOOP  for post_id in verified_ok:
         │     reviewed.mark(state_path, post_id, content_id(manifest))
         │     _retry(lambda: publish_post._run(ns_with_approve), 3, 1.0)
         │     → success: count  |  fail: mark failed
         │
         └── SUMMARY  jobs.report(f"完成：成功{n}/失敗{f}/跳過{s}(驗證失敗{sv})")
```

## Implementation Units

- [ ] **Unit 1: webui_config — add `auto_pipeline` field**

**Goal:** Persist the auto-mode toggle in `webui.yaml` with correct default and type coercion.

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Modify: `core/webui_config.py`
- Test: `tests/test_webui_config.py`

**Approach:**
- Add `auto_pipeline: False` to `DEFAULTS` dict
- `_coerce()` currently only handles `_INT_FIELDS` and `_FLOAT_FIELDS` — it has **no bool handling**. Add `auto_pipeline` to a new `_BOOL_FIELDS` set and coerce: `"on"` → `True`, any other value (including absent/`""`) → `False`. Apply this coercion in `_coerce()` alongside existing int/float branches.
- YAML round-trip is safe (bare `false` parses as Python `False`); the coercion handles the form POST `"on"` / absent-checkbox case.

**Patterns to follow:**
- Existing field additions in `DEFAULTS` (L14-33); `_coerce()` type logic pattern

**Test scenarios:**
- Happy path: `load()` returns `auto_pipeline=False` when key absent from YAML (default)
- Happy path: `save()` round-trips `auto_pipeline=True` and reloads correctly
- Edge case: `save()` with form POST value `"on"` coerces to `True`; `""` or absent coerces to `False`
- Edge case: `load()` from YAML with `auto_pipeline: true` (string) and `auto_pipeline: True` (bool) both return Python `True`

**Verification:**
- `webui_config.load()` returns `{"auto_pipeline": False}` for a config without the key
- `webui_config.save({..., "auto_pipeline": True})` writes `auto_pipeline: true` to YAML and reloads as `True`

---

- [ ] **Unit 2: Settings page — toggle + warning banner**

**Goal:** Expose `auto_pipeline` as a checkbox in the settings form with a visible warning when enabled.

**Requirements:** R1, R3

**Dependencies:** Unit 1

**Files:**
- Modify: `webui/templates/settings.html`

**Approach:**
- Add `<input type="checkbox" name="auto_pipeline" ...>` following existing field pattern
- When `cfg.auto_pipeline` is True, render a warning `<div>` (e.g., red/amber banner) explaining reviewed gate is bypassed
- Warning renders server-side via Jinja2 conditional — no JS needed
- Add `auto_pipeline` to `save_settings()` Form parameters in `webui/app.py` (checkbox sends `"on"` when checked, absent when unchecked)

**Patterns to follow:**
- Existing `<input>` fields in `settings.html`; existing `save_settings()` Form params (L46-67)

**Test scenarios:**
- Happy path: settings page renders with `auto_pipeline` checkbox unchecked by default
- Happy path: warning banner appears when `cfg.auto_pipeline = True`
- Happy path: form POST with checkbox checked saves `auto_pipeline=True` and reloads with warning
- Edge case: form POST without checkbox (unchecked) saves `auto_pipeline=False`

**Test expectation:** Settings HTML is a Jinja2 template; test via the existing pattern of rendering the template with mock cfg or via integration test that POSTs to `/settings`.

**Verification:**
- GET `/settings` with `auto_pipeline=True` in config renders the warning element
- POST `/settings` with `auto_pipeline=on` updates config and redirects with warning visible

---

- [ ] **Unit 3: `_retry()` helper + `_run_auto_pipeline()` function**

**Goal:** Extract `_action_ns` to module scope, implement `_retry()` helper, and implement the auto-pipeline executor `_run_auto_pipeline()` with per-item retry, gate bypass, and summary.

**Requirements:** R4, R5, R6, R7, R8, R9, R10

**Dependencies:** Unit 1

**Files:**
- Modify: `webui/app.py`
- Test: `tests/test_auto_pipeline.py` (new file)

**Approach:**
- **Extract `_action_ns`**: Move from `start_crawl` local closure to module level, adding `cfg` as an explicit parameter. Update the two existing call-sites inside `start_crawl` to pass `cfg`. No behavior change — pure refactor required as prerequisite.
- `_retry(fn, times=3, delay=1.0) -> tuple[Any, Exception | None]`: calls `fn()`, catches any exception, sleeps `delay` and retries; returns `(result, None)` on success or `(None, last_exception)` on final failure
- `_run_auto_pipeline(job, cfg, built: list[dict])` where each dict is `{"post_id": str, "title": str, "manifest_path": str}` from `run_pipeline()["built"]`:
  - Early-return with `jobs.report(job, "無新稿件，跳過自動發布")` if `built` is empty
  - DRAFT LOOP: `_action_ns(post_id, "draft", cfg)` (module-level after extraction), then `_retry(lambda: draft_post._run(ns), 3, 1.0)`; collect `drafted_ok`, `failed` with stage label
  - VERIFY LOOP: same pattern with `verify_draft._run`; collect `verified_ok`, add verify failures to `failed`
  - PUBLISH FILTER: read manifest status for each `verified_ok` post_id; only proceed if `"draft_verified"` (defensive guard — verify loop should already ensure this)
  - Before each publish: `reviewed.mark(cfg["state_path"], post_id, reviewed.content_id(manifest_data))` — requires reading the manifest JSON for `content_id` computation
  - Construct publish ns: same as `_action_ns` but add `approve=True`
  - PUBLISH LOOP: `_retry(lambda: publish_post._run(ns), 3, 1.0)`
  - `jobs.set_current(job, f"自動發布中 {i+1}/{total}…")` during each stage
  - SUMMARY: `jobs.report(job, f"自動發布完成：成功 {s} / 失敗 {f} / 跳過 {skip}（驗證失敗 {verify_fail}）")`

**Patterns to follow:**
- `batch_action()` per-item loop with per-item isolation (L203-252)
- `_action_ns()` for building SimpleNamespace args (L179-194)
- `_submit_job()` pattern for error handling (L254-287)
- `reviewed.mark()` / `reviewed.content_id()` in `core/reviewed.py`

**Test scenarios:**
- Happy path: 3 packages all succeed draft→verify→publish; summary shows "成功 3 / 失敗 0 / 跳過 0"
- Happy path: empty post_ids → early-return message, no draft/verify/publish calls
- Edge case: 1 package fails draft after 3 retries → skipped from verify+publish; summary "成功 0 / 失敗 1 / 跳過 0"
- Edge case: 1 package passes draft, fails verify after 3 retries → skipped from publish; summary "成功 0 / 失敗 1 / 跳過 1（驗證失敗 1）"
- Edge case: publish gate rejects one package (e.g., draft_verified status check fails) → counted as publish failure
- Error path: `reviewed.mark()` raises → treat as publish failure for that item, retry publish (which will call mark again)
- Integration: `jobs.report()` called with progress messages at each stage transition (verify via mock)

**Test isolation note:** `reviewed.mark()` writes to SQLite. All unit tests must mock `core.reviewed.mark` to prevent cross-test state contamination.

**Verification:**
- Unit tests pass with mocked `draft_post._run`, `verify_draft._run`, `publish_post._run`, and `core.reviewed.mark`
- Summary string contains correct counts after a mixed success/failure run
- `reviewed.mark()` is called exactly once per successfully-verified package before publish

---

- [ ] **Unit 4: Wire auto-pipeline into `/crawl` endpoint**

**Goal:** Call `_run_auto_pipeline()` at the end of the `/crawl` job when `auto_pipeline=True`.

**Requirements:** R4, R6

**Dependencies:** Unit 3

**Files:**
- Modify: `webui/app.py` (the `start_crawl` handler's `_work` closure)

**Approach:**
- After `result = pipeline.run_pipeline(...)`, extract post_ids via `[b["post_id"] for b in result.get("built", [])]`. The `run_pipeline()` return shape is confirmed: `{"built": [{"post_id": str, "title": str, "manifest_path": str}, ...], "failed": [...], "skipped": int}`. Pass the full `built` list to `_run_auto_pipeline` so it has access to `manifest_path` without additional filesystem traversal.
- Check `cfg.get("auto_pipeline", False)` and call `_run_auto_pipeline(job, cfg, result["built"])`
- The `_work` closure already has access to `job` and `cfg` — no new parameters needed

**Patterns to follow:**
- Existing `_work(job)` closure in `start_crawl` (L75-92); `jobs.report()` calls pattern

**Test scenarios:**
- Happy path: when `auto_pipeline=True`, `_run_auto_pipeline` is called after `run_pipeline` with the built post_ids
- Happy path: when `auto_pipeline=False`, `_run_auto_pipeline` is NOT called (existing behavior unchanged)
- Edge case: `run_pipeline` returns 0 built packages → `_run_auto_pipeline` called with empty list → early-return message
- Integration: full `/crawl` POST with auto_pipeline=True mock → job completes with draft+verify+publish side effects (or mock them)

**Verification:**
- Existing crawl tests still pass (auto_pipeline defaults to False)
- New integration test: crawl job with `auto_pipeline=True` invokes all three stage runners

## System-Wide Impact

- **Interaction graph:** `/crawl` job duration extends significantly in auto-mode (crawl + 3 stages × N packages × up to 3 retries each). The existing HTMX polling interval handles long-running jobs without change — `jobs.set_current()` keeps the live status fresh each item.
- **Concurrency semantic risk:** If a user is manually reviewing a package in the WebUI at the same moment auto-pipeline calls `reviewed.mark()` and publishes it, the manual review action becomes a post-publish no-op. This is a semantic conflict, not a data corruption issue (SQLite WAL prevents corruption). Mitigation: settings warning (R3) must explicitly state "執行自動發布期間請勿手動審閱稿件".
- **Error propagation:** Failures are isolated per-item per-stage. A single package's publish failure does not interrupt the loop. Final job status remains `done` even with per-item failures (summary message carries the error signal).
- **State lifecycle risks:** `reviewed.mark()` writes to the `reviewed` SQLite table. In auto-mode, all published packages will appear as "reviewed" in the manual review UI — intentional, user opted in. No double-publish risk because `publish_post._run` writes `published` to state DB, and subsequent `dedupe-posts` skips published URLs.
- **SQLite write contention:** Auto-pipeline makes multiple sequential writes to `reviewed` and state DB over potentially minutes. If a manual `batch_action` runs concurrently on the same package, SQLite WAL serializes writes safely — no corruption risk, but one may see a transient busy error. Retry logic on the affected stage handles this.
- **API surface parity:** `check_publish_gates()` signature is unchanged. `batch_action()` is unchanged. The only new public surface is `auto_pipeline` in `webui.yaml`.
- **Unchanged invariants:** Manual mode (auto_pipeline=False) behavior is entirely unaffected. All three publish gates remain active — only Gate ① is pre-satisfied via `reviewed.mark()`.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `/crawl` job runs for a long time in auto-mode; HTMX polling may appear stalled | `jobs.set_current()` updates live status each item; frontend polling renders this immediately. No timeout change needed. |
| `reviewed.mark()` side-effect makes all auto-published posts appear "reviewed" in manual UI | Acceptable — user opted in. Settings warning (R3) explains this. |
| `draft_post._run` not idempotent on already-drafted packages | Verify at implementation (deferred); if not idempotent, add pre-filter by status before draft loop |
| Publish fails because manifest title missing or empty | Gate ③ will catch and count as publish failure. Not special-cased — rely on existing gate. |
| `reviewed.mark()` semantic conflict with concurrent manual review | Settings warning (R3) must say "執行期間請勿手動審閱". No code-level lock needed for v1. |

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-16-auto-pipeline-requirements.md](docs/brainstorms/2026-06-16-auto-pipeline-requirements.md)
- Related code: `webui/app.py` L203-252 (`batch_action`), L179-194 (`_action_ns`), L254-287 (`_submit_job`), L360-372 (`check_publish_gates`)
- Related code: `core/reviewed.py` L36 (`content_id`), L72 (`mark`), L82 (`get`)
- Related code: `core/webui_config.py` L14-33 (`DEFAULTS`), L54 (`load`), L113 (`save`)
- Related code: `core/jobs.py` L35 (`submit`), L64 (`report`), L69 (`set_current`)
