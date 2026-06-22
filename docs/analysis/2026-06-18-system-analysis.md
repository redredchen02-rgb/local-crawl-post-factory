---
title: "系統完整分析 — 現狀掃描 x Master Plan 對照"
date: 2026-06-18
type: analysis
status: active
supersedes: []
---

# 系統完整分析

## 1. 品質基線（截至 2026-06-18）

| 項目 | 結果 |
|------|------|
| Ruff | 0 errors ✅ |
| Mypy | 82 files, 0 errors ✅ |
| Tests | 全綠（即時通過數見 `make test-full`；2026-06-18 為 273，已隨後續 PR 成長） |
| Coverage | 以 `make test-full` 為準（不再硬編快照數字，避免漂移） |
| Source files | 見 repo 現況；數字會隨聚瓜/備稿等模組成長 |
| Test files | 見 `tests/` 現況 |
| Git since master plan | 6 PRs landed (#17-#22) |
| Fast test gate | 250 passed / 23 deselected (~6s) |
| Active execution plan | `docs/plans/2026-06-18-001-fix-workflow-pipeline-stabilization-plan.md` |

## 2. Master Plan vs 實際落地對照

源自 `docs/plans/2026-06-16-004-deep-optimization-master-plan.md`。

### P0: Plan/Config Hygiene ✅ 全部完成

### P1: Contract Solidification

| Unit | 狀態 | 說明 |
|------|------|------|
| U1.1 移除 deprecated `_xxx` 別名 | ✅ | 已落地 |
| U1.2 後台命令公開 run API | ✅ | `core/pipeline.py`、WebUI 單項 action、批量 action 均走 `draft_post.run` / `verify_draft.run` / `publish_post.run`；`_run` 僅作 CLI/測試兼容入口 |
| U1.3 CLI flag 與設定檔 parity | ❌ | 未做 |
| U1.4 錯誤契約 characterization | ❌ | 未做 |

### P2: Typed Models & Data Shape

| Unit | 狀態 | 說明 |
|------|------|------|
| U2.1 TypedDict 邊界模型 | ⚠️ 部分完成 | `core/schema.py` 已有 Pipeline/Manifest TypedDict；更深的 SQLite row/result 型別仍待治理 |
| U2.2 mypy 阻斷 | ✅ | CI 與 `make typecheck` 均為 blocking gate |
| U2.3 漸進提高 mypy 嚴格度 | ❌ | 未擴展 |
| U2.4 SQLite row/result 型別 | ❌ | 未做 |

### P3: Test Stratification & CI

| Unit | 狀態 | 說明 |
|------|------|------|
| U3.1 測試標記體系補齊 | ✅ | 已有 `slow`、`browser`、`integration`、`subprocess` markers |
| U3.2 Make targets | ✅ | 已有 `test-fast` / `test-full` / `test-slow` |
| U3.3 CI workflow | ✅ | `.github/workflows/ci.yml` 完善 |
| U3.4 覆蓋率策略 | ❌ | 未文檔化 |

### P4: Storage, Jobs & Observability — ❌ 全部未做

| Unit | 說明 |
|------|------|
| U4.1 統一 SQLite schema lifecycle | 未做 |
| U4.2 schema version / migration | 未做 |
| U4.3 jobs registry lifecycle | 未做 |
| U4.4 audit/run history retention | 未做 |
| U4.5 history/audit TTL cache | 未做 |

### P5: Performance & Scale

| Unit | 狀態 | 說明 |
|------|------|------|
| U5.1 package scan cache | ❌ | 未做 |
| U5.2 cover concurrency guardrails | ❌ | 未做 |
| U5.3 crawl progress polling interval | ❌ | hardcoded 0.5s |
| U5.4 backend retry strategy review | ✅ | #18 已落地（指數 backoff） |

### P6: WebUI UX — U6.1 ✅，其餘 ❌

### P7: Safety & Publish Hardening

| Unit | 狀態 | 說明 |
|------|------|------|
| U7.1 publish gate threat model | ❌ | 未做 |
| U7.2 credential exposure audit | ✅ | #17 已落地 |
| U7.3 package file serving audit | ❌ | 未做 |
| U7.4 publish receipt/rollback | ❌ | 未做 |

## 3. Plan-to-Code Drift（需修復的不一致）

1. **README / examples**: 舊文案仍暗示 WebUI 永遠不會自動發布；已更新為「預設手動、安全 gate 保留；auto_pipeline 是 opt-in 風險模式」。
2. **舊 plan 狀態**: auto-pipeline plan 與 deep optimization master plan 已標記為 superseded，避免後續 agent 按舊單元重做。
3. **Auto-pipeline result visibility**: 已修正 adapter 與 `/crawl` job，讓自動發布結果進入 job result 並由完成頁顯示。
4. **剩餘治理項**: jobs lifecycle、SQLite schema lifecycle、operator failure inspection、measurement-gated performance hooks 仍待按 stabilization plan 推進。

## 4. 可立即修復的 Quick Wins

- [x] Fix Makefile typecheck: blocking mypy
- [x] Fix pyproject.toml mypy comment: blocking gate
- [x] Fix README: typecheck description and current test targets
- [x] Fix README: dashboard is first screen now
- [x] Fix production paths: backend stages use public `run()` APIs
- [x] Add test markers to pyproject.toml: integration, subprocess
- [x] Add Makefile targets: test-fast, test-full, test-slow
- [x] Make auto-pipeline job results renderable after `/crawl`

## 5. 仍需深度優化的項目（優先級排序）

### P0 優先（漂移修正 + 文件同步）
- 舊 active plans 已由 stabilization plan supersede；README/examples 已同步 auto/manual 安全語意。

### P1 優先（契約固化）
- U1.2: public run API 已套到 core/WebUI production paths
- U1.3: CLI flag parity（中等）
- U1.4: 錯誤契約測試（中等）

### P2 優先（型別收斂）
- U2.1: TypedDict 邊界模型（中等）
- U2.3: mypy 嚴格度擴展（低，但有累積價值）
- U2.4: SQLite row 型別（低）

### P3 優先（測試分層）
- U3.1/U3.2 已完成；下一步是 workflow smoke coverage 與 coverage policy 文檔
- U3.4: 覆蓋率策略文檔（低）

### P4 （資料層治理）— 風險最高，但本地工具短期影響小
### P5 （效能）— 優先級偏低，無實際性能投訴
### P6 （WebUI UX）— U6.2 次高
### P7 （安全硬化）— U7.1 文檔可做，U7.3/7.4 中等
