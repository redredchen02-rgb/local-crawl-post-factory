---
title: "feat: WebUI 即時爬取進度（live crawl progress）"
type: feat
status: active
date: 2026-06-15
---

# feat: WebUI 即時爬取進度（live crawl progress）

## Overview

在 WebUI 按下「爬取最新並建包」後，目前畫面在整個爬取階段沒有任何動靜，直到全部爬完才一次跳出結果。本計畫讓**爬取階段即時回報進度**：子程序把進度寫入側通道檔，父程序邊跑邊讀並灌進現有的 job 進度，前端沿用既有的每秒輪詢，於畫面上呈現一條「即時狀態列」——`已爬 N 頁 · 擷取 M 篇 · 目前：<網址>「<標題>」`，完成後列出建包標題清單（使用者已選定此形態）。

不更動爬蟲的輸出契約、不引入新傳輸層（不上 SSE/WebSocket）、不改動 dedupe/發布行為。

## Problem Frame

使用者回報：「我現在在 WebUI 看不到任何動作，但終端機看得到它在運作。」

第一手程式碼追蹤確認根因（非輪詢失效）：

- `webui/app.py` 的 `/crawl` → `_work`：只在爬取**前**報「crawling…」、爬取**後**報「crawled N item(s)」，中間整段爬取沒有任何回報。
- `core/pipeline.py` `crawl_items` → `src/crawl_posts.py` `crawl_items`：父程序 `proc.start()` 後直接 **`proc.join()` 阻塞**，等子程序整段爬完才讀檔；爬取期間父程序對進度零可見度。
- `src/crawl_posts.py` `_crawl_worker`：子程序把擷取項目寫入暫存 NDJSON（結束才 flush），`status.json` 只在 `finally` 寫一次（結束時）。
- 終端機看似「活著」，只因 Scrapy 自身把每個請求 log 到 **stderr**（終端機繼承了該流）——這條流從不流向 WebUI 的 job。

對照之下，建包階段（`run_pipeline`）本來就有逐項回報（`progress_cb`），而 `_job_status.html` 本來就每秒輪詢 `/jobs/{id}` 並渲染 `job.progress`。**輪詢與渲染機制完好；唯一缺口是爬取階段不產生即時進度，且進度模型是無上限的純附加清單，不適合承載逐頁心跳。**

## Requirements Trace

- R1. 爬取階段須即時（秒級）回報進度到 WebUI，而非只在開始/結束各一句。
- R2. 進度須讓使用者「知道爬了什麼內容」：頁數、擷取篇數、目前網址/標題。
- R3. 完成後仍列出建包成果（標題清單），維持既有結果視圖。
- R4. 沿用既有 HTMX 輪詢傳輸，不新增 SSE/WebSocket。
- R5. 不破壞 `crawl-posts` 的 stdout 純 NDJSON 硬契約；進度走側通道，永不進 stdout。
- R6. 不改動 dedupe（唯讀）與發布（人工 CLI）行為。

## Scope Boundaries

- 不引入 SSE/WebSocket 或任何新傳輸層。
- 不做進度的跨重啟持久化（job 設計上就是記憶體內、重啟即失，套件已落地於 `out/`，可接受）。
- 不更動 `crawl-posts` CLI 的 stdout 輸出契約與退出碼語意。
- 不更動 dedupe 的唯讀性質、不更動發布閘門（沿用 [[dedupe-readonly-constraint]]）。
- 不重寫爬蟲擷取邏輯、不改 Scrapy 設定（並發/延遲/深度等）。
- 不處理多使用者並發（單人本地工具）。

## Context & Research

### Relevant Code and Patterns

- `core/jobs.py` — 記憶體 job 登記表：`Job.status/progress/result/error`、`submit/get/report`。`report()` 對 `progress` 純附加。**本計畫在此新增「即時狀態列」欄位。**
- `webui/app.py` `/crawl` 的 `_work`（約 `app.py:70`）— 進度回報的接線點；建包階段已用 `progress_cb=lambda m: jobs.report(job, m)`。
- `core/pipeline.py` `crawl_items`（`pipeline.py:25`）/ `run_pipeline`（`pipeline.py:40`，已支援 `progress_cb`）— 爬取與建包的單一實作來源。
- `src/crawl_posts.py` `_crawl_worker`（`crawl_posts.py:59`）與 `crawl_items`（`crawl_posts.py:277`）— 子程序爬取、檔案式 IPC（`out_path` NDJSON + `status_path` JSON）、`proc.join()` 阻塞點。**硬契約：stdout 只能是 NDJSON，所有 Scrapy 噪音走 stderr。**
- `webui/templates/_job_status.html` — 既有每秒輪詢（`hx-trigger="every 1s"`）並渲染 `job.progress`。**本計畫在此加上即時狀態列。**
- `webui/templates/settings.html:34` — 觸發鈕 `hx-post="/crawl" hx-target="#job"`，整條 UI 流已接好。
- `webui/static/app.css:13-16` — 已有 `--pending`/`--pending-weak` 進行中色票，可直接用於「運作中」視覺。

既有的「輪詢」傳輸模式（`every 1s`、`every 10s`、`load`）遍布全站，無任何 SSE/streaming——本計畫與之對齊。

### Institutional Learnings

- [[dedupe-readonly-constraint]] — dedupe 維持唯讀；本計畫的進度回報不得觸及 dedupe/publish 行為。
- 子程序測試慣例（`tests/test_crawl_posts.py`）：以 `http.server` 起本地 fixture 站（`tests/fixtures/site/`）、以子程序跑真 Scrapy、斷言 stdout 為純 NDJSON——這是 Unit 2 測試的基礎。

### External References

未使用——本地模式充足，且非高風險領域（無認證/金流/資料遷移），不需外部研究。

## Key Technical Decisions

- **傳輸沿用 HTMX 輪詢，不上 SSE/WebSocket。** 理由：單人本地工具；既有 1s 輪詢已達秒級「即時」；與全站模式一致；零新依賴。
- **子程序→父程序採「檔案輪詢」（同一暫存目錄，新增獨立進度檔）。** 子程序在爬取**進行中**把 `{responses, items, last_url, last_title}` 原地刷新寫入**新的 `progress.json`**——與既有、僅在 `finally` 寫一次的 `status.json` **分離**；後者仍是「無回應→`ExternalError`／退出碼 4」判定的唯一權威，不被進度寫入污染。父程序以 `proc.is_alive()` 為條件邊跑邊讀進度檔，取代裸 `proc.join()`。理由：沿用檔案式 IPC 風格、spawn 安全、無 Queue/pickle 複雜度、保住 stdout 與退出碼契約。（合併審查發現：可行性＋對抗審查均指出「沿用既有 IPC」措辭誤導——既有 `status.json` 只在結束寫一次，本功能需要的是執行中持續寫入的新路徑。）
- **進度模型 = 即時狀態列（live current）＋ 有上限的里程碑清單（progress）。** 爬取心跳更新「current」單行（原地刷新、不暴漲）；階段轉換與每篇建包附加到「progress」（受 `limit`≤30 約束，有界）。理由：避免上百頁爬取把清單撐爆；契合使用者選定的終端機式心跳行。
- **進度檔採原子寫入（temp + 置換）＋ 容錯讀取。** 理由：跨程序讀寫避免讀到半截 JSON；父程序讀取失敗時略過該次輪詢、不崩。
- **爬取進度 → `job.current`；里程碑/建包 → `job.progress`。** 理由：讓即時行乾淨、讓日誌有意義且有界。

## Open Questions

### Resolved During Planning

- 進度顯示形態：**即時狀態列 + 成果清單**（使用者於規劃時選定；非逐頁滾動、非純計數）。
- 傳輸方式：**HTMX 輪詢**（沿用既有，不新增 SSE）。
- 「知道爬了什麼」的粒度：頁數、擷取篇數、目前網址 + 標題（取自進度檔的 `last_url`/`last_title`）。

### Deferred to Implementation

- 父程序輪詢間隔與子程序寫檔節流的實際數值（依手感微調；初值方向見 Unit 2）。
- 是否每篇即時 flush NDJSON：預設**否**（標題改由進度檔的 `last_title` 取得，故非必要）；實作時若發現需要再開。
- 既有 `mkdtemp` 暫存目錄未清理（既有行為、非本計畫造成）：是否順手清理為選配，不阻塞。
- 確切的輔助函式/欄位命名，留待實作。

## High-Level Technical Design

> *以下示意「即時進度」的跨程序資料流，屬審查用的方向性指引，非實作規格。實作者應視為脈絡，而非照抄的程式碼。*

```mermaid
sequenceDiagram
    participant UI as 瀏覽器（_job_status.html）
    participant App as webui/app.py _work
    participant Job as core/jobs.py Job
    participant Par as crawl_posts.crawl_items（父）
    participant Chi as _crawl_worker（子程序/Scrapy）
    participant F as 進度檔（側通道）

    UI->>App: POST /crawl
    App->>Job: submit(_work) → 立即回 job_id
    App->>Par: crawl_items(opts, progress_cb)
    Par->>Chi: spawn 子程序
    loop 每次 response（節流）
        Chi->>F: 原子寫入 {responses, items, last_url, last_title}
    end
    loop while 子程序存活（父輪詢 ~0.5s）
        Par->>F: 容錯讀取進度
        Par->>App: progress_cb(快照)
        App->>Job: set_current("已爬 N 頁 · 擷取 M 篇 · 目前：…")
    end
    Chi-->>Par: 結束（寫最終 status.json）
    Par->>App: 回傳 items（沿用既有錯誤/退出碼判定）
    App->>Job: report("爬取完成：N 頁，擷取 M 篇") + 進入建包
    Note over UI,Job: 期間 UI 每 1s GET /jobs/{id}，<br/>渲染 job.current（即時行）＋ job.progress（里程碑）
```

關鍵不變式：子程序對 **stdout** 仍只吐 NDJSON；進度只走側通道檔與 stderr。父程序的錯誤判定（無回應→`ExternalError`/退出碼 4）維持不變。

## Implementation Units

- [ ] **Unit 1: job 模型新增「即時狀態列」**

**Goal:** 讓 job 能承載一條原地更新的即時狀態，與既有里程碑清單分離。

**Requirements:** R1, R2

**Dependencies:** 無

**Files:**
- Modify: `core/jobs.py`
- Test: `tests/test_jobs.py`

**Approach:**
- `Job` 新增 `current`（字串，最新即時狀態，初值空），`snapshot()` 一併輸出。
- 新增一個「設定即時狀態」的函式（更新 `current`，執行緒安全、不附加到清單）；`report()` 維持對 `progress` 純附加的既有語意不變。

**Execution note:** 純資料模型、適合 test-first。

**Patterns to follow:** 沿用 `core/jobs.py` 既有 `report()`/`snapshot()` 風格與 `_LOCK` 執行緒安全慣例。

**Test scenarios:**
- Happy path：設定即時狀態後，`get(job_id)` 的快照 `current` 反映最新字串。
- Happy path：連續設定即時狀態，`current` 為最後一次值（覆蓋、非附加）。
- Edge case：新建 job 的 `current` 預設為空字串/None，快照不缺鍵。
- Integration：`report()` 仍只動 `progress`、不影響 `current`；兩者互不污染（沿用既有 `test_progress_reporting` 風格）。
- Edge case：兩個 job 的 `current` 不互相串味（沿用既有 `test_two_jobs_do_not_mix`）。

**Verification:** `tests/test_jobs.py` 綠；快照同時含 `progress`（清單）與 `current`（單行）。

---

- [ ] **Unit 2: 爬蟲子程序即時回報 + 父程序輪詢**

**Goal:** 子程序即時寫進度、父程序邊跑邊讀並回呼，取代裸 `proc.join()`，且完全保住 stdout 純 NDJSON 契約。

**Requirements:** R1, R2, R5

**Dependencies:** 無（可與 Unit 1 並行）

**Files:**
- Modify: `src/crawl_posts.py`
- Test: `tests/test_crawl_posts.py`
- Modify（必要時擴充多頁 fixture）: `tests/fixtures/site/`

**Approach:**
- 子程序 `_crawl_worker`：在 `parse` 回呼中記錄 `last_url`、`last_title`（既有已累計 `responses`/`items`），於每次 response（節流，例如 ~0.3s 或每 N 次）以**原子寫入**（temp + 置換）更新進度檔。Scrapy 在子程序內為單執行緒 reactor，更新共享 `status` 與寫檔無真並行風險。
- 父程序 `crawl_items` 新增選配 `progress_cb`：以 `while proc.is_alive()` 輪詢（~0.5s）讀進度檔（**容錯**：缺檔/半截 JSON 則略過該次），有更新則呼叫 `progress_cb(快照)`；迴圈結束後 `proc.join()` + 最終讀檔，既有錯誤/退出碼判定（無回應→`ExternalError`）原封不動。
- `progress_cb=None` 時行為與現狀完全一致。
- **硬契約**：進度只寫側通道檔；stdout 仍只承載 NDJSON，Scrapy log 仍走 stderr。

**Execution note:** characterization-first——此處有 stdout 純度硬契約與 Scrapy reactor 隔離；先以既有 fixture 測試固定「stdout 純 NDJSON」與「無回應→退出碼 4」的現有行為，再加即時回報，確保契約不回歸。

**Patterns to follow:** `tests/test_crawl_posts.py` 的本地 `http.server` fixture + 子程序跑真 Scrapy；既有 `status.json` 容錯讀取（`json.JSONDecodeError` 略過）。

**Test scenarios:**
- Happy path：對本地 fixture 站以 `progress_cb` 呼叫 `crawl_items`，回呼至少被觸發一次，且回報的 `responses`/`items` 隨爬取遞增；最終回傳的 items 與不帶 cb 時一致。
- Integration：回報的 `last_url`/`last_title` 對應 fixture 實際爬到的頁面。
- Contract：既有「stdout 為純 NDJSON」測試維持綠（進度未污染 stdout）。
- Edge case：`progress_cb=None` → 無回呼、回傳與現狀完全相同。
- Edge case：進度檔半截/暫缺 → 父程序容錯，不崩、不誤報。
- Error path：目標主機不可達 → 仍 `ExternalError`/退出碼 4，輪詢迴圈乾淨退出，stdout 為空、stderr 非空（沿用既有 `test_unreachable_host_exits_4`）。

**Verification:** `tests/test_crawl_posts.py` 全綠（含新即時回報案例與既有契約案例）；手動對真站爬取時父程序能持續取得遞增進度。

---

- [ ] **Unit 3: pipeline 轉接 progress_cb 到爬取階段**

**Goal:** 讓 `pipeline.crawl_items` 能把進度回呼透傳給爬蟲層，建包階段回報不受影響。

**Requirements:** R1

**Dependencies:** Unit 2

**Files:**
- Modify: `core/pipeline.py`
- Test: `tests/test_pipeline.py`、`tests/test_pipeline_public_api.py`

**Approach:**
- `pipeline.crawl_items(webui_cfg, progress_cb=None)`：將 `progress_cb` 透傳給 `crawl_posts.crawl_items`，其餘設定組裝不變。
- `run_pipeline` 不動（其 `progress_cb` 行為維持）。

**Patterns to follow:** `core/pipeline.py` 既有把設定字典轉成 `crawl_posts` opts 的薄轉接風格。

**Test scenarios:**
- Happy path：`crawl_items(cfg, progress_cb=cb)` 會把 `cb` 傳抵 `crawl_posts.crawl_items`（以 monkeypatch 攔截斷言）。
- Edge case：未傳 `progress_cb` → 預設 None，行為同現狀。
- Integration：`run_pipeline` 的逐階段/逐項回報不受本改動影響（既有 pipeline 測試維持綠）。

**Verification:** `tests/test_pipeline*.py` 全綠；公開 API 簽章變更（新增選配參數）已被測試涵蓋。

---

- [ ] **Unit 4: WebUI 接線——把爬取心跳灌進即時狀態列**

**Goal:** `/crawl` 的背景工作在爬取階段持續更新 `job.current`，階段轉換與建包成果寫入 `job.progress`。

**Requirements:** R1, R2, R3, R6

**Dependencies:** Unit 1、Unit 3

**Files:**
- Modify: `webui/app.py`
- Test: `tests/test_webui_crawl.py`

**Approach:**
- `_work`：開始先設 `job.current = "準備爬取…"`；呼叫 `pipeline.crawl_items(cfg, progress_cb=…)`，回呼把爬取快照格式化為單行（`已爬 N 頁 · 擷取 M 篇 · 目前：<url>「<title>」`）寫入 `job.current`（用 Unit 1 的設定函式）。
- 爬取結束：以 `report()` 附加里程碑「爬取完成：N 頁，擷取 M 篇」，`job.current` 切為「建包中…」。
- 建包階段：沿用既有 `progress_cb=lambda m: jobs.report(job, m)`（每篇建包附加到 `progress`，受 `limit` 約束有界）；可選擇同步更新 `current` 為「建包中…」。
- 不改動 dedupe/發布路徑（[[dedupe-readonly-constraint]]）。
- **測試接縫**：既有 `fake_crawl(cfg)` 需改為接受 `progress_cb`（關鍵字參數），確保 mock 與新簽章相容。

**Patterns to follow:** `webui/app.py` 既有 `_work` 與 `jobs.report` 用法；`tests/test_webui_crawl.py` 的 `monkeypatch.setattr(pipeline, "crawl_items", fake_crawl)` 注入法。

**Test scenarios:**
- Happy path：`POST /crawl` 回 200 並含 job id；輪詢至完成時，畫面含既有建包結果清單（沿用 `test_crawl_builds_packages`）。
- Integration：以一個會主動呼叫 `progress_cb`（含遞增頁/篇數與某網址）的 `fake_crawl`，在 job 執行期間 `GET /jobs/{id}` 的 HTML 反映即時狀態行內容（為避免時序不穩，mock 同步呼叫回呼後再返回，使快照確定帶有 `current`）。
- Contract：`fake_crawl` 以新增的 `progress_cb` 關鍵字被呼叫而不報錯（簽章相容）。
- Edge case：`start_url` 為空 → 400（沿用 `test_crawl_without_start_url_400`）。
- Error path：爬取拋 `ExternalError` → job 轉 failed、`_job_status.html` 顯示錯誤（沿用既有失敗渲染）。

**Verification:** `tests/test_webui_crawl.py` 全綠；執行期 `/jobs/{id}` 可見即時狀態行，完成後見建包清單。

---

- [ ] **Unit 5: 樣板與樣式——呈現即時狀態列**

**Goal:** `_job_status.html` 於進行中醒目呈現 `job.current`（含「運作中」視覺），下方保留里程碑清單；每秒輪詢不變。

**Requirements:** R2, R3, R4

**Dependencies:** Unit 4

**Files:**
- Modify: `webui/templates/_job_status.html`
- Modify: `webui/static/app.css`

**Approach:**
- 進行中（pending/running）：在既有輪詢容器內，於清單上方新增一行 `job.current`，配「運作中」指示（用既有 `--pending`/`--pending-weak` 做脈動/圓點）；下方續渲染 `job.progress`。
- 完成/失敗視圖維持既有（建包清單、錯誤訊息）。
- `app.css` 新增小幅 `.live`/脈動樣式，沿用既有設計變數，不引入框架。

**Patterns to follow:** `_job_status.html` 既有 `hx-get/every 1s/outerHTML` 結構；`app.css` 既有色票與卡片風格。

**Test scenarios:**
- 由 Unit 4 的整合測試涵蓋：執行期 `/jobs/{id}` 的 HTML 含即時狀態行文字。
- Test expectation: none（樣式與純渲染樣板，無行為邏輯；功能性由 Unit 4 整合測試驗證）。

**Verification:** 瀏覽器實測：爬取期間可見即時狀態列原地更新頁/篇/目前網址，伴隨「運作中」視覺；完成後轉為建包清單。

## System-Wide Impact

- **Interaction graph：** `settings.html` 按鈕 → `/crawl` → `jobs.submit(_work)` → `pipeline.crawl_items(progress_cb)` → `crawl_posts.crawl_items(progress_cb)` → 子程序進度檔；前端 `_job_status.html` 每秒輪詢 `/jobs/{id}` 渲染 `current`＋`progress`。
- **API surface parity：** `crawl_posts.crawl_items` 與 `pipeline.crawl_items` 新增**選配** `progress_cb`。既有呼叫者：CLI `crawl_posts._run` 不帶 cb（預設 None，行為不變）；測試中對 `pipeline.crawl_items` 的 mock 需接受該關鍵字（見 Unit 4）。
- **Error propagation：** 爬取無回應仍 `ExternalError` → job failed → `_job_status.html` 既有錯誤視圖。輪詢迴圈須在子程序結束/例外時乾淨退出。
- **State lifecycle risks：** 進度檔位於既有 `mkdtemp` 暫存目錄；採原子寫入避免半截讀取。暫存目錄未清理屬既有行為，非本計畫引入（可選配清理）。
- **Unchanged invariants：**
  - `crawl-posts` stdout 純 NDJSON 硬契約 — **不變**（進度走側通道）。
  - dedupe 唯讀、發布人工 CLI 閘門 — **不變**（[[dedupe-readonly-constraint]]）。
  - job 記憶體內、重啟即失 — **不變**（僅新增 `current` 欄位）。
- **Integration coverage：** 跨程序即時回報（子寫檔→父輪詢→job→前端輪詢）以 Unit 2（爬蟲層）＋ Unit 4（WebUI 層）的整合案例共同涵蓋，非僅單元 mock。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 跨程序讀到半截 JSON 進度檔 | 子程序原子寫入（temp + 置換）；父程序容錯讀取，失敗則略過該次輪詢 |
| 進度污染 stdout、破壞 NDJSON 契約 | 進度只寫側通道檔；characterization 測試先固定 stdout 純度，改動後維持綠 |
| 父程序輪詢/子程序寫檔過於頻繁耗資源 | 父輪詢 ~0.5s、子寫檔節流（~0.3s 或每 N 次 response）；數值實作時微調 |
| `job.progress` 在大量爬取下暴漲 | 爬取心跳走 `job.current`（原地更新、不附加）；`progress` 僅承載里程碑與建包（受 `limit`≤30 約束） |
| 即時行的測試時序不穩（job 執行中斷言） | 整合測試用同步呼叫 `progress_cb` 的 mock crawl，使快照確定帶 `current` |
| 改動 `crawl_items` 簽章影響既有 mock | 新參數為選配（預設 None）；明確更新 `tests/test_webui_crawl.py` 的 `fake_crawl` 簽章 |

## Documentation / Operational Notes

- 可於 `README.md` 的 WebUI 段落補一句：爬取時畫面會顯示即時狀態列（頁/篇/目前網址）。非阻塞、選配。
- 無遷移、無排程、無監控變更。

## Sources & References

- 根因相關程式碼：
  - `webui/app.py`（`/crawl` `_work`，約 `app.py:70`）
  - `core/pipeline.py:25`（`crawl_items`）、`core/pipeline.py:40`（`run_pipeline`）
  - `src/crawl_posts.py:59`（`_crawl_worker`）、`src/crawl_posts.py:277`（`crawl_items` 的 `proc.join()` 阻塞）
  - `core/jobs.py`（job 模型／`report`）
  - `webui/templates/_job_status.html`（每秒輪詢）、`webui/templates/settings.html:34`（觸發鈕）
- 測試慣例：`tests/test_crawl_posts.py`（fixture 站＋子程序）、`tests/test_jobs.py`、`tests/test_webui_crawl.py`（mock 注入）
- 相關記憶：[[dedupe-readonly-constraint]]
