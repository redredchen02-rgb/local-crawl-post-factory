# Changelog

All notable changes to this project will be documented in this file.

## [0.2.2.0] - 2026-06-16

### Added
- **Dashboard 首頁**（`GET /`）：開啟 WebUI 即顯示全局統計卡片（待處理 / 已驗證 / 已發布）+ 最近 5 條運行歷史 + 爬取快捷鈕；統計卡片每 10 秒自動刷新（`/_dashboard_stats` partial）。設定頁移至 `/settings`。
- **Package 列表行內操作**：上膛清單每行新增「草稿」「驗證」按鈕，直接觸發 hx-post，無需跳轉 detail 頁；操作結果 inline 顯示於該行。
- **Detail 頁流程進度條**：`建包 → 建草稿 → 驗證 → 發布` 步驟指示器，依當前 status 高亮當前步驟；操作結果 inline swap 取代按鈕區域；發布表單預填標題。
- **Toast 通知系統**：右上角固定 `#toast-container`，偵測 HTMX 回應中的 `<p class="ok/error">` 自動彈出通知；成功通知 3 秒自消，失敗通知需手動關閉，最多同時 3 條。
- **Loading 狀態 + 防雙擊**：所有 HTMX 操作按鈕加入 `.htmx-indicator` 旋轉動畫；請求進行中設 `pointer-events:none` 防重複提交。
- **全選 Checkbox**：上膛清單表頭全選 checkbox（已於 v0.2.0.0 實作，本版確認正常）。

## [0.2.1.1] - 2026-06-16

### Changed
- **公共 pipeline API（P7）**：`core/pipeline.py` 新增 `run_auto_pipeline()` 作為 draft→verify→publish 的統一入口；`webui/_auto_pipeline.py` 縮減為 20 行 adapter，透過 `on_progress` / `on_status` / `on_session_expired` callback 橋接 WebUI job 系統，消除兩套邏輯偏離風險。
- **`_action_ns` 遷移**：從 `webui/_auto_pipeline` 移至 `webui/_helpers`，手動單項操作（draft/verify/publish）繼續可用。
- **測試層級調整**：`tests/test_auto_pipeline.py` 的邏輯單元測試改為直接測 `core.pipeline.run_auto_pipeline`，`_retry` 由 `core.pipeline` 提供。

## [0.2.1.0] - 2026-06-16

### Changed
- **webui 路由拆分**：`webui/app.py` 由 734 行拆分為 6 個 `APIRouter` 模組（`settings_auth`, `crawl`, `packages`, `actions`, `trash`, `history_audit`），app.py 縮減至 64 行。
- **純函式輔助層**：I/O helper 移至 `webui/_helpers.py`；auto-pipeline 邏輯移至 `webui/_auto_pipeline.py`；router 共用 context（`cfg_from_request`, `auth_light`, `submit_job`）移至 `webui/routers/_ctx.py`。
- **check_publish_gates**：從 `webui/app` 移至 `webui/_helpers`，消除路由模組對 app 的循環引入。
- **note_expiry callback 修正**：`_run_auto_pipeline` 在 draft/verify/publish 每個階段的 `SessionExpiredError` 都正確回呼 `note_expiry`（先前僅接受參數但未呼叫）。
- **imports 整理**：`webui/routers/actions.py` 移除所有函式體內延遲 import，改為模組頂層 import。

## [0.2.0.0] - 2026-06-16

### Added
- **垃圾桶頁**（`/trash`）：列出所有被移入 `out/.trash/` 的貼文，支援單筆「復原」及「清空垃圾桶」（永久刪除）。復原衝突時回 409 並保留垃圾桶原件。
- **批量刪除**：上膛清單勾選多筆後可一鍵批量移入垃圾桶（`/batch/delete`）。
- **全選 checkbox**：上膛清單表頭新增全選/取消全選 checkbox；個別勾選時顯示 indeterminate 狀態；批量操作完成後自動清除所有勾選。
- **history 頁篩選 UI**：新增 post_id 文字搜尋及 severity 下拉篩選，每 5 秒自動刷新時保留篩選狀態。
- **detail 頁刪除按鈕**：可直接從 detail 頁刪除貼文並跳回上膛清單。

### Changed
- **上膛清單預設視圖**：status 預設改為「進行中（未發布）」，隱藏已發布貼文；新增「全部（含已發布）」選項（`status=all`）。
- **detail 頁 published 狀態**：已發布貼文不再顯示建草稿/驗證/CLI 指令等無意義按鈕，改為顯示綠色「已完成」橫幅。
- **audit / history post_id 連結**：兩頁 post_id 欄位均可點擊直接跳往 detail 頁。
- **`.batch-bar` 排版**：批量操作按鈕改為 flex 橫排並加上適當間距。
- **Settings 自動化說明**：auto_pipeline 開關下補充說明哪些設定在自動模式仍生效。

## [0.1.0.0] - 2026-06-16

### Added
- **一鍵全自動發布**：WebUI 設定頁新增「自動發布模式」開關。啟用後，單次「爬取最新並建包」即自動串聯草稿→驗證→發布全流程，無需手動逐步觸發 CLI。
- **自動重試**：草稿、驗證、發布三個階段各自獨立重試最多 3 次（間隔 1 秒），減少因瞬時錯誤導致的中斷。
- **自動審稿門 bypass**：自動模式下自動標記 Gate ①（reviewed），Gates ②③ 仍正常執行以確保稿件品質。
- **自動模式設定警告**：設定頁啟用自動發布後顯示黃色警示橫幅，提醒使用者勿在自動發布執行中手動審閱稿件。
- **執行進度回報**：自動發布期間透過 WebUI 歷史記錄顯示各階段進度及最終成功/失敗/跳過統計。

### Changed
- `_action_ns()` 從 `start_crawl` 的區域函數提升至模組層級，供自動發布循環與手動動作共用。
- WebUI 爬取按鈕說明文字依自動/手動模式動態切換。
