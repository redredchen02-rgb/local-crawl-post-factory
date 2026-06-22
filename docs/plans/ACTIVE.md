---
title: "Plans index — active / merged / superseded"
type: index
date: 2026-06-22
---

# Plans Index

Status at a glance for `docs/plans/`. One line per plan: status + one-phrase summary.
Cross-referenced against `git log` (PR numbers) and each plan's own frontmatter `status`.

## Active / in-flight

- `2026-06-22-003-refactor-parallel-safe-optimization-plan.md` — **ACTIVE** — parallel-safe optimization pass (lane-isolated work; plan file authored in a sibling worktree, not yet on `main`).

## Recently merged / completed

- `2026-06-22-002-fix-codebase-bug-sweep-plan.md` — **COMPLETED** — 30-defect bug sweep; U1/U2/U3/U9 in PR #37, U4–U20 in commit 47fc969 + adversarial follow-ups in PR #42.
- `2026-06-22-002-refactor-package-namespace-cpost-plan.md` — **COMPLETED** — namespace all packages under `cpost/` to fix pip-install collision (PR #36).
- `2026-06-22-001-feat-multi-source-aggregation-plan.md` — **COMPLETED** — sources as a first-class concept + multi-source crawl/robustness (PR #34; calibration PR #33; loop collapse PR #35).
- `2026-06-18-006-feat-daily-prep-scoop-flow-plan.md` — **COMPLETED** — 今日備稿 flow: `/today` workspace + generate-article (PR #32).
- `2026-06-18-004-feat-scoop-library-scoring-generation-plan.md` — **COMPLETED** — cluster library into scoops, score by confidence & quality (PR #30).
- `2026-06-18-005-refactor-remove-cover-feature-plan.md` — **COMPLETED** — remove the cover feature (PR #31).
- `2026-06-18-003-feat-fulltext-capture-drop-cover-plan.md` — **COMPLETED** — capture full article body + disable identical covers (PR #27).
- `2026-06-18-002-refactor-standalone-portability-plan.md` — **COMPLETED** — standalone portability: remove external functional references (PR #26).
- `2026-06-18-001-fix-workflow-pipeline-stabilization-plan.md` — **COMPLETED** — stabilize the end-to-end workflow pipeline + Phase 5 perf + Phase 7 receipt/rollback (PR #25); supersedes the deep-optimization master plan.

## Superseded / historical

- `2026-06-16-004-deep-optimization-master-plan.md` — **SUPERSEDED** (2026-06-18) by `2026-06-18-001-fix-workflow-pipeline-stabilization-plan.md`; itself supersedes the `002` runs-batching and `003` router-split plans. Kept as historical context only.
- `2026-06-16-002-perf-phase1-runs-batching-retry-cache-plan.md` — **SUPERSEDED** by the deep-optimization master plan (`016-004`); landed via PR #18.
- `2026-06-16-003-refactor-webui-app-router-split-plan.md` — **SUPERSEDED** by the deep-optimization master plan (`016-004`); landed via PR #19.
- `2026-06-16-001-feat-auto-pipeline-plan.md` — **SUPERSEDED** by `2026-06-18-001` (workflow stabilization); original auto-pipeline landed via PR #9, unified entry point in PR #21.

## Older plans (pre-2026-06-16, shipped)

The `2026-06-15-00x` plans (crawl factory, webui settings, daily-ops hardening, CI safety net, quality uplift, live crawl progress) are all shipped and predate this index; see git history (PRs #7–#17) for landing details.
