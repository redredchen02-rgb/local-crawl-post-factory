# Changelog

All notable changes to this project will be documented in this file.

## [0.2.2.1] - 2026-06-18

### Fixed
- **獨立可重新定位**：啟動器 `啟動本地服務.command` 改以自身位置解析專案目錄（取代硬編碼絕對路徑），pyenv PATH 改條件式 — 資料夾可複製/搬移到任意路徑或機器後直接啟動。
- **設定存檔不再寫死絕對路徑**：WebUI「儲存設定」流程改以 `webui_config.load_raw()`（未解析）合併，避免把解析後的機器絕對路徑寫回 `configs/webui.yaml`（會破壞可攜性）；`configs/webui.yaml` 執行期路徑對齊專案根（`../`）。
- **設定錯誤頁修復**：`/settings` 驗證失敗分支補上 `diag`，輸入非法值不再導致頁面渲染崩潰。

### Changed
- **倉庫衛生**：`.gitignore` 忽略工具目錄與備份（`*.bak`、`.omo/`、`.mimocode/`）並防止執行期產物再漏進 `configs/`；執行期狀態統一落在專案根 `state/`、`logs/`、`auth/`。
- **VERSION 對齊**：`VERSION` 檔與 `pyproject.toml` 同步至 `0.2.2.1`（先前 `VERSION` 落後）。

### Added
- **可攜性回歸守門測試**（`tests/test_portability_guard.py`）：偵測追蹤檔中的機器絕對路徑與設定檔非相對路徑，鎖定可重新定位狀態。

## [0.2.2.0] - 2026-06-16

### Added
- **版本號頁腳**：所有頁面底部顯示 `v{version}`，版本來源 `importlib.metadata`（開發環境顯示 `dev`）。
- **設定頁診斷區塊**：顯示 config 路徑、state DB 路徑、storage-state 路徑、output 目錄的存在狀態，方便快速確認環境設定是否完整。
- **Inline 編輯**：detail 頁標題與文案各有「編輯」按鈕，展開 inline form，儲存後自動收合；空值送出時回 400 提示。
- **Retry 按鈕**：detail 頁失敗區塊（`.failure-box`）在 draft/verify 階段顯示「重試」按鈕，可直接觸發重試而不需捲動至後台動作區。
- **發布後自動刷新**：detail 頁發布成功後 2 秒自動重新整理，呈現最新狀態。
- **→ 運行歷史 link**：detail 頁 post_id 欄位旁新增快捷連結，直達該貼文歷史紀錄。

### Changed
- **`package_built` badge 琥珀色**：`.pill.package_built` 改為橙黃色，與 drafted（橙色）、draft_verified（綠色）、published（藍色）視覺區分更明確。
- **failure 區塊樣式**：改用 `.failure-box` 包裝，背景紅色弱底，標題移入 box 內，視覺更聚焦。

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
