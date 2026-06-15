---
date: 2026-06-15
topic: local-crawl-post-factory
---

# Local Crawl-to-Post Automation (`local-crawl-post-factory`)

## Problem Frame

操作一個自有/私有網站時，要把「自家內容的最新 URL」重新打包成標準化貼文（固定格式文案 + 加浮水印封面），再灌進另一個**沒有 API、只能靠後台表單**的私有 admin。目前這流程全靠人工複製貼上，慢、易錯、無法批次/排程。

目標是一條 **CLI-first、可組合 pipeline、stateless-by-default、可安全跑 cron/agent** 的本地管線，把爬取、內容生成、媒體處理、後台建草稿、驗證、發布**用明確階段隔開**，且**爬完絕不直接發布**。

完整 spec 見原始 command 輸入（本文件不重述全部細節，只記錄定案與範圍邊界）。

## User Flow

```text
crawl-posts → normalize-items → dedupe-posts → render-caption
  → select-cover → watermark-cover → build-manifest        ← 首版交付範圍 (Phase 1-3)
  ─────────────────────────────────────────────────────────
  → draft-post → verify-draft → publish-post --approve      ← 後續版本 (Phase 4-5)
```

每段都是獨立 CLI、stdin→stdout NDJSON 串接；發布需經 `manifest → draft → verify → approve → publish`，禁止跳階。

## Requirements

**首版範圍（Phase 1-3，本次交付）**
- R1. 實作並完整測試 `normalize-items`、`dedupe-posts`、`render-caption`、`build-manifest`、`crawl-posts`、`select-cover`、`watermark-cover` 七個 CLI。
- R2. 所有命令遵守 I/O 契約：成功時 stdout 只輸出結構化 JSON/NDJSON、stderr 空、exit 0；失敗時 stdout 空、stderr 一行診斷、exit 1–5（依 spec §2.3/§13 的碼）。
- R3. 命令可在 shell pipeline 中無狀態串接（spec §12.1 的整條 pipe 能跑通）。
- R4. `watermark-cover` 與 `select-cover` 絕不覆寫原始檔；輸出檔名 deterministic。
- R5. 相同 normalized item + config 必須產生相同 manifest 與輸出檔（deterministic post package）。

**後台自動化（Phase 4-5，後續版本，本次只定契約不實作）**
- R6. `draft-post` / `verify-draft` / `publish-post` 的 CLI flags、manifest 狀態欄位、`backend.yaml` selector schema **本次就先定稿**，避免首版 manifest/state 結構日後返工。
- R7. 後台為**自家 admin**：所有選擇器一律來自 `backend.yaml`，Python 邏輯**零硬編碼 selector**、不用絕對座標、優先 `wait_for_selector`/`wait_for_url`/可見文字驗證。
- R8. `publish-post` 沒有 `--approve`、或 manifest 狀態非 `draft_verified` 時必須拒絕執行。

**狀態與去重**
- R9. dedupe 判定基準＝**只認 `published`**：只有真正發布過（SQLite 標記 published）的 `canonical_url` / `title_hash` 才算「處理過」並被跳過。
- R10. `crawl-posts` 不得寫 state；state 寫入時機依 spec §9（build-manifest 起可寫 `package_built`，publish 寫 `published`）。

## Success Criteria

- 原始 spec §17 驗收條款 1–8、12–14 全數通過（即 Phase 1-3 + 契約穩定 + pipeline 可組合 + 失敗模式有測試）。
- spec §12.1 整條 build-packages pipeline 能對一個真實自有站台跑出完整 `out/<post_id>/` 包。
- pytest 覆蓋：CLI 契約、normalize、dedupe（含 published 跳過 / 新 URL 放行）、caption 模板、manifest 生成與非法 manifest 拒絕、watermark 不覆寫原檔。

## Scope Boundaries

- 首版**不碰瀏覽器**：不實作 `draft-post`/`verify-draft`/`publish-post` 行為（只定義其契約 R6）。
- 不做 WebUI、不做 LLM 文案（固定模板）、不依賴付費 LLM API。
- 不繞過 login/CAPTCHA/anti-bot；只操作自有或授權站台。
- 不做 `crawl-and-publish` 這類合併命令。

## Key Decisions

- **後台＝自家 admin**：selector recipe 走純 config-driven（R7），不為通用多 CMS 過度設計，但 `backend.yaml` schema 要乾淨到換站只改 config。
- **首版切在 Phase 3**：資料+媒體管線可完全離線/pytest 驗證，先把最穩的部分做扎實，把最脆弱的瀏覽器層隔離到後續。
- **dedupe 只認 published**：最安全、語意最簡單；副作用是首版（尚無 publish）dedupe 實質永遠放行 —— 這是預期行為，非 bug。

## Dependencies / Assumptions

- Python 3.11；Scrapy（爬蟲）、Pillow（浮水印）、SQLite（狀態）、pytest（測試）。Playwright 為 Phase 4 依賴，首版可只列入 optional/契約。
- 假設已有一個自有站台可供 `crawl-posts` 實測；登入態日後用 Playwright `storage_state`，不存明文密碼。

## Outstanding Questions

### Deferred to Planning
- [Affects R9][Technical] 首版 dedupe 既然實質永遠放行，是否要提供一個 `--seen-from-manifest` 或 dry-run 旗標，讓開發期能模擬「已處理」以測 dedupe 邏輯？（或純靠單元測試塞 SQLite fixture 即可。）
- [Affects R1][Technical] `crawl-posts` 用 Scrapy 跑單次 CLI 輸出 NDJSON 的最簡整合方式（CrawlerProcess vs subprocess）—— 規劃時定。
- [Affects R6][Needs research] `backend.yaml` 的 `result_title: 'table >> text={title}'` 這類動態插值 selector 在自家 admin 的實際 DOM 是否可靠 —— 需在有真實後台時驗證。
- [Affects R5][Technical] deterministic 輸出檔名的雜湊基準（content_hash 取哪些欄位）規劃時定。

## Next Steps
→ `/ce:plan` for structured implementation planning（focus: Phase 1-3 實作 + Phase 4-5 契約定稿）
