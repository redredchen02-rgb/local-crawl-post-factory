---
date: 2026-06-15
topic: project-optimization
---

# 全面優化：穩健性 → 可維護性 → 運維體驗（分階段）

## Problem Frame

`local-crawl-post-factory` 的原計畫範圍（基礎管線 + 日常營運硬化 + WebUI 全控制台）已落地，144 測試全綠、CLI 契約嚴格、架構清晰。本次不是補功能漏洞，而是**在「每天穩定跑真實站台」這個定位下，挑高槓桿改進繼續加價值**。

全代碼審查找到的問題歸成三簇，按與「會不會靜默出錯」的相關度排優先級：穩健性最高、可維護性次之、運維體驗最後。三簇全做，分三階段交付，每階段獨立可發布、向後相容、既有測試維持綠。

## Requirements

**第一階段 — 穩健性與去重正確性（防靜默出錯）**
- R1. 細分 pipeline 的異常捕獲（`core/pipeline.py` normalize/build 階段的裸 `except Exception`）：驗證類錯誤只跳過該項並記錄，系統類錯誤須傳播或按嚴重度處理，不得被統一吞掉。
- R2. 統一網路類重試：`select-cover` 圖片下載的瞬時超時應比照後台命令具備有限次重試（新增 `--retries` 或在 pipeline 階段層重試），不再一次超時就 exit 4。
- R3. 修正後台 driver 的錯誤分類（`browser/backend_driver.py`）：明確區分「可重試（Playwright 逾時）／不可重試（SessionExpired、Validation）／需判斷的其他 Playwright 錯誤」，避免把網路/資源錯誤誤診為 session 過期。
- R4. 修正去重判定（`core/state.py` / `dedupe_posts`）：`canonical_url OR title_hash` 改為更嚴格的判定（同時匹配，或引入 content_hash），避免不同文章因標題哈希碰撞被誤跳。
- R5. 去重決策可審計：每次跳過/放行記錄原因（命中哪個鍵、對應的既有記錄），便於事後回查「某篇為何沒被處理」。

**第二階段 — 可維護性（降低改動成本）**
- R6. 提取 pipeline 共用操作為穩定公共 API：把各 src 模組被 `core/pipeline.py` 繞用的私有函數（`_normalize`/`_dedupe`/`_render`/`_select`/`_watermark`/`_build`）規範為公共介面，CLI 入口與 WebUI in-process 都走同一介面，消除兩處因實作細節變動而偏離的風險。
- R7. 配置可移植：`core/webui_config.py` 的相對路徑（state/out/download_dir）改為相對於專案根或設定檔位置，並支援環境變數覆蓋（如 `CPOST_STATE_PATH`），文件明確啟動目錄與優先級。

**第三階段 — 運維體驗（日常順手）**
- R8. WebUI 批量操作：上膛清單支援多選 + 批量「建草稿／驗證／發布」，發布仍受既有三重閘門約束（逐項驗證狀態與標題確認，不得繞過）。
- R9. 審計與運行歷史增強：`audit.jsonl` / `runs` 增加 severity 與關聯 ID（parent_run_id，貫穿一篇 post 的生命週期），`/audit` 視圖支援按 post_id / severity 篩選。
- R10. session 過期後的重登入體驗改善：UI 在偵測過期時提供更明確的重登引導或一鍵入口（具體機制待規劃）。

## Success Criteria

- 對一個會間歇逾時的站台連跑一週每日批次：瞬時失敗被重試吸收，真失敗有現場可查、不靜默；不會因標題碰撞誤跳有效內容（R1–R5）。
- 改動任一 pipeline 階段的邏輯時，只需改一處公共介面，CLI 與 WebUI 自動一致；換啟動目錄不再寫錯 state（R6–R7）。
- 大批量發布（單日數十篇）可在 WebUI 多選批量完成，且每篇仍過三重閘門；出問題時能用 post_id 一次撈出整條生命週期記錄（R8–R10）。
- 每階段交付後既有測試維持綠，新增行為皆有測試（含去重誤跳的拒絕路徑、批量發布的閘門拒絕路徑）。

## Scope Boundaries

- 維持單站單後台、localhost-only、無帳號系統、外部 cron 排程（沿用既有邊界，不在本次擴張）。
- 不依賴付費 LLM、不改固定模板文案生成方式。
- 不自動繞過 CAPTCHA / 反爬 / 登入；登入仍是人工 `auth-login`。
- 不破壞既有 CLI I/O 契約與退出碼語意（優化以重構 + 新增為主，向後相容）。
- 增量爬蟲（只爬上次之後更新的頁面）暫不納入——當前以人工審核為前提，重複爬成本可接受。

## Key Decisions

- **分三階段、按「防靜默出錯」排序**：穩健性/去重正確性直接決定內容會不會漏發或誤跳，最高優先；可維護性是為日後改動與新入口打底；運維體驗錦上添花。理由：在「每天跑真站台」定位下，正確性 > 開發效率 > 操作便利。
- **去重改嚴（R4）優先於改寬**：寧可偶爾重複處理一篇（人工審核可攔），不可靜默漏發——故傾向 AND 或 content_hash，而非維持 OR。最終策略於規劃階段定。
- **重構不破契約（R6）**：公共 API 提取以「不改 CLI 行為」為硬約束，純內部重構 + 測試護欄。

## Dependencies / Assumptions

- 沿用既有 `core/`、`browser/`、`webui/`、CLI 與 `configs/*.yaml`，以擴充/重構為主。
- 假設營運者已用 `auth-login` 取得 storage-state，過期願人工重登。
- R3/R10 的真實偵測訊號（被導回登入頁等）仍需有真實後台時校準（既有前提）。

## Outstanding Questions

### Deferred to Planning
- [R2][Technical] `select-cover` 重試的退避策略與上限，以及與既有後台 `--retries` 是否共用設定來源。
- [R3][Needs research] 各類 Playwright 錯誤到「可重試／不可重試」的精確映射，需真實站台驗證。
- [R4][Technical] 去重最終策略：`url AND title_hash` vs 引入 `content_hash` 三層；以及對既有 state schema 的遷移影響。
- [R6][Technical] 公共 API 的擺放（各 src 模組導出 vs 新建 `core/pipeline_ops.py`）與對既有 import 的影響。
- [R8][Technical] 批量操作的後端執行模型（串行 vs 有限並發）與對單站禮貌限速的互動。
- [R9][Technical] 關聯 ID 的生成與貫穿方式，及與既有 `runs` 表/`audit.jsonl` 的真相來源關係，避免重複。
- [R10][Technical] UI 重登機制：引導到終端跑 `auth-login` vs localhost 後端開 headed 瀏覽器。

## Next Steps
→ `/ce:plan` for structured implementation planning（建議先就第一階段 R1–R5 做計畫）
