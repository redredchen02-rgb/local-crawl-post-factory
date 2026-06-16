---
title: "perf: Phase 1 效能修補 — Runs 批次連線、Cover 重試、History 快取量測"
type: perf
status: active
date: 2026-06-16
origin: docs/brainstorms/2026-06-16-perf-maintainability-uplift-requirements.md
---

# perf: Phase 1 效能修補 — Runs 批次連線、Cover 重試、History 快取量測

## Overview

修補三個低風險、高性價比的效能缺口：

1. **P1** — `run_pipeline` 的兩個寫入 loop 目前每筆都開一條新 SQLite 連線（含 `_ensure_schema`）；改為共用單一連線，攤銷開啟成本。
2. **P2** — `select_cover` 的圖片下載重試預設值為 0（等同無重試）；改為 3 次指數退避，讓瞬時逾時自動恢復而不中斷 pipeline。
3. **P3** — `/history` 與 `/audit` 每 5 秒刷新時各做一次 SQLite 查詢 + 檔案讀取；先量測實際延遲，若 ≥ 5 ms 再加 30 秒 TTL 快取。

每個 unit 獨立可發布、向後相容、不改 CLI I/O 契約。

## Problem Frame

`local-crawl-post-factory` 是單人本機工具，整批數十篇時 pipeline 的 SQLite 寫入成本非零但可最佳化；`select_cover` 的一次逾時即中止讓真實網路環境下的 cover 選取脆弱；WebUI 的高頻自動刷新在磁碟 I/O 上有不必要的重複成本。

三個改動都是「低風險的正確方向」，但 P1/P3 各有「先量測」的門檻條件，不以工程直覺取代數據。

## Requirements Trace

- P1. Runs 批次連線（origin: R-P1）— `run_pipeline` 十筆批次的連線開啟次數 ≤ 2
- P2. select-cover 重試（origin: R-P2）— 瞬時逾時被重試吸收，不出現 exit 4
- P3. History/audit 快取（origin: R-P3，量測先行）— 若延遲 ≥ 5 ms，5 秒刷新第 2–N 次無額外 I/O

## Scope Boundaries

- 不改 CLI I/O 契約（`stdout / stderr / exit code` 語意不變）
- P1 的連線共享只限 `run_pipeline` 同一背景執行緒，不跨執行緒共享 connection
- P2 不重試 4xx/5xx HTTP 回應（`urllib` 不拋例外，天然不在 retry 分支）
- P3 快取若觸發，只做 in-process TTL dict，不引入 Redis / 外部快取
- 不改 WebUI URL、前端行為、或現有路由

## Context & Research

### Relevant Code and Patterns

**P1 — Runs 連線**
- `core/runs.py` L82–95：`_connect(path)` 是 contextmanager，open → `_ensure_schema` → yield → commit → close
- `core/runs.py` L98–106：`record_run()` 每次呼叫都 `with _connect(path) as conn:`
- `core/pipeline.py` L94–96：dedupe skip loop 每筆 skip 呼叫一次 `record_run`
- `core/pipeline.py` L121–122：build 成功 loop 每筆呼叫一次 `record_run`
- `core/pipeline.py` L127–130：build 失敗 except 分支每筆呼叫一次 `record_run`
- `core/pipeline.py` L69：`run_id = runs.new_run_id()` — run_id 全段共享，已正確

**P2 — select-cover retry**
- `src/select_cover.py` L30–31：`DEFAULT_RETRIES = 0`, `DEFAULT_BACKOFF_SEC = 0.0`（改點）
- `src/select_cover.py` L77–101：`_fetch()` 已有完整 retry loop；ExternalError 重試，ValidationError 不重試
- `src/select_cover.py` L52–74：`_download_once()` — 網路錯誤包成 `ExternalError`（L72-73），非圖片拋 `ValidationError`（L66-67）
- `core/pipeline.py` L65–66：讀 `webui_cfg` 的 `cover_retries` / `cover_backoff_sec`，預設 0/0.0（改點）
- `core/pipeline.py` L102–104：`select_all(..., cover_retries, cover_backoff ...)` 傳入 `_fetch`
- `browser/backend_driver.py` L66–87：全 repo 的 retry 參考範本（per-stage per-error 分類）

**P3 — History/audit**
- `webui/app.py` L534–543：`/history` endpoint，呼叫 `runs.list_runs(cfg["state_path"], limit=200, ...)`
- `webui/app.py` L545–550：`/audit` endpoint，呼叫 `_tail_audit(cfg["audit_log"], 200)`
- `webui/app.py` L581–607：`_tail_audit` — stat → seek(max(0, size-65536)) → read → parse → reverse
- `core/runs.py` L39–43：`idx_runs_ts`, `idx_runs_post`, `idx_runs_run_id` 已建 index

### Institutional Learnings

- **Retry 形狀**：全 repo 重試範本是 `browser/backend_driver._run_with_retry`（L66-87），per-stage、ExternalError 重試 / ValidationError 不重試
- **SQLite WAL 模式**：已啟用，序列化寫入，避免 corruption；schema migration 用冪等 `PRAGMA table_info` pattern
- **`DEFAULT_RETRIES=1` 陷阱**（plan 005 記載）：attempts=1 實際上不會重試；真重試需 count > 1
- `docs/solutions/` 不存在，以上均取自 `docs/plans/`

### External References

- 無需外部研究，本地 pattern 已有直接範本

## Key Technical Decisions

- **P1 backward-compatible `conn` param + 逐筆 commit**：`record_run(path, ..., conn=None)` — `conn is None` 時走原本 `with _connect(path)` 路徑（含 commit），`conn` 不為 None 時直接 INSERT 後立即 `conn.commit()`（**逐筆 commit，不累積到 scope 結束**）；攤銷 open/schema-check 成本，同時保留原本「每筆 record 獨立持久化」的語意。零破壞性變更，其他所有 `record_run` 呼叫不受影響。

- **P1 連線只在 `run_pipeline` 的背景執行緒內共享**：不從主 thread 傳 conn 進去，不跨 WebUI 執行緒。SQLite `check_same_thread` 預設為 True，conn 只在 `jobs.submit` 的 worker thread 上建立並使用，無 thread-safety 問題。

- **P2 改指數退避**：原本 `backoff_sec * attempt`（線性），改為 `backoff_sec * (2 ** (attempt - 1))`（指數）以更好地應對後端限速。預設 `DEFAULT_RETRIES=3`, `DEFAULT_BACKOFF_SEC=1.0` → 間隔 1 / 2 / 4 秒，最壞情況多 7 秒，在批量 cover 下可接受。

- **P2 預設值改在 `select_cover.py`，webui_cfg 跟進**：`DEFAULT_RETRIES/BACKOFF` 改在來源模組，`core/pipeline.py` 的 `webui_cfg` 預設讀取值（L65-66）改為一致，讓 CLI 與 WebUI in-process 都用相同預設。

- **P3 measure-first — 量測先行再決定是否快取**：先在兩個 endpoint 加 `time.perf_counter()` log，收集 5 秒 polling 5 輪的實際延遲。閾值 5 ms（含 SQLite open + schema check + SELECT + file seek + JSON parse），超過才實作 TTL dict。TTL cache key = `(post_id, severity, run_id, bucket_30s)`，bucket_30s = `int(time.time() // 30)`，自然失效。

## Open Questions

### Resolved During Planning

- **P1 連線傳遞方式**：採 `conn=None` 可選參數，而非重構 `record_run` 的呼叫介面 → 零破壞向後相容（見 Key Decisions）
- **P2 backoff 形狀**：採指數（非線性）→ 更符合抵抗限速的業界實踐
- **P2 改預設值的位置**：改在 `select_cover.py` 的 DEFAULT 常數 + `core/pipeline.py` webui_cfg 讀取預設，**不改 `webui/webui.yaml`**（配置優先，常數墊底）
- **P3 快取不使用 `lru_cache`**：`lru_cache` 不支援 TTL，且 filter 參數是動態的 → 改用 module-level `_cache: dict` + timestamp 鍵，TTL 到期自動失效

### Deferred to Implementation

- **P1 量測門檻確認**：十筆批次的基準耗時（改前/改後）應在實作後量測，若 < 10 ms 差異可在 PR 說明中記錄，但不影響正確性
- **P3 是否觸發快取**：endpoint 量測結果在執行期才知道；若 < 5 ms，直接標 "量測完成，不需快取" 並關閉 P3
- **P3 快取 module 擺放**：若觸發，`_cache` dict 和 TTL helper 放在 `webui/app.py` 頂部（module-level）或獨立 `webui/_cache.py`，實作時依可讀性決定

## Implementation Units

- [ ] **Unit 1：runs.py — 加 `open_run_conn()` 與可選 `conn` 參數**

  **Goal:** 讓 `record_run` 可接受外部傳入的連線，消除每次呼叫的 open/schema-check/close 成本

  **Requirements:** P1

  **Dependencies:** None

  **Files:**
  - Modify: `core/runs.py`
  - Test: `tests/test_runs.py`（若無則新建）

  **Approach:**
  - 在 `runs.py` 新增 `open_run_conn(path)` — `@contextmanager`，內部用現有 `_connect(path)` 作實作，yield 出 conn 物件
  - 在 `record_run(path, ..., conn=None)` 加 `conn` 可選參數：`conn is None` → 走原本 `with _connect(path)` 路徑；否則直接 INSERT，不重新 open
  - `open_run_conn` 和修改後的 `record_run` 都是 public API，需加入 `__all__`（若有）

  **Patterns to follow:**
  - `core/runs.py` L82–95 的 `_connect` contextmanager 為實作基礎
  - `_ensure_schema` 在 `_connect` 內已呼叫一次，`open_run_conn` 複用此行為，子呼叫不重複執行

  **Test scenarios:**
  - Happy path：`open_run_conn` 取得 conn，在其 scope 內呼叫 `record_run(conn=conn)` 兩次 → 兩筆 run 寫入，conn 仍開著；scope 結束後 conn commit+close
  - Happy path：`record_run` 不傳 `conn`（`conn=None`）→ 行為與原本完全一致（backward compat）
  - Edge case：`open_run_conn` scope 內發生 exception → 因為每次 `record_run(conn=conn)` 已逐筆 `conn.commit()`，exception 前已 commit 的筆數持久保留；exception 後未 commit 的 INSERT（若有）被 SQLite 隱式 rollback（`close()` 前未 commit → 自動 rollback）；不 corrupt DB
  - Integration：`list_runs` 在 `open_run_conn` scope **外**呼叫 → 能讀到 scope 內 committed 的 runs（WAL 模式下讀一致性）

  **Verification:**
  - 現有 `record_run` 相關測試全數通過
  - 新 test 用 `sqlite3.connect` 直接驗證批次寫入筆數，不依賴 mock

---

- [ ] **Unit 2：pipeline.py — 使用 `open_run_conn` 批次寫入**

  **Goal:** `run_pipeline` 的兩個寫入 loop 共用一條連線，攤銷 open/schema 成本

  **Requirements:** P1

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `core/pipeline.py`
  - Test: `tests/test_pipeline.py`

  **Approach:**
  - 在 `run_pipeline` 函式頂部（取得 `run_id` 後）用 `with runs.open_run_conn(webui_cfg["state_path"]) as conn:` 包住整個 skip loop + build loop
  - 三處 `runs.record_run(...)` 呼叫全數加 `conn=conn` 參數
  - `runs.new_run_id()` 不需修改（只讀，走自己的 `_connect`）
  - `list_runs`（只讀路徑）不在 hot path，不需修改
  - conn 物件不離開 `run_pipeline` 函式（不跨執行緒傳遞）

  **Patterns to follow:**
  - `core/pipeline.py` L69：現有 `run_id = runs.new_run_id()` 作為 `open_run_conn` 呼叫的參考點
  - `browser/backend_driver.py` L66–87：per-stage retry 與 error handling 模式（不直接使用，但對照理解 error boundary）

  **Test scenarios:**
  - Happy path：五筆 item 進 `run_pipeline`，兩筆 skip、三筆 success → DB 有 5 筆 run record，全部 run_id 相同
  - Integration：`run_pipeline` 完成後呼叫 `runs.list_runs` 能拿回所有 5 筆（跨 conn 讀可見）
  - Error path：build loop 中第二筆拋 exception → **第一筆已逐筆 commit 持久保留**（`record_run(conn=conn)` 內即時 commit），第二筆 exception 前無 uncommitted INSERT，exception 正確傳播到呼叫方，conn cleanup 不 hang；語意與原本逐筆持久化一致

  **Verification:**
  - `pytest tests/test_pipeline.py` 全通過
  - 可選：在本地跑十筆批次，比對改前/改後 wall time（記錄到 PR comment）

---

- [ ] **Unit 3：select_cover.py + pipeline.py — 改預設重試值與退避公式**

  **Goal:** 瞬時圖片下載逾時自動重試而非 exit，指數退避避免放大後端負載

  **Requirements:** P2

  **Dependencies:** None（獨立於 Unit 1/2）

  **Files:**
  - Modify: `src/select_cover.py`（DEFAULT 常數 + `_fetch` backoff 公式）
  - Modify: `core/pipeline.py`（webui_cfg 讀取預設值 L65–66）
  - Test: `tests/test_select_cover.py`

  **Approach:**
  - `src/select_cover.py` L30–31：`DEFAULT_RETRIES = 3`, `DEFAULT_BACKOFF_SEC = 1.0`
  - `src/select_cover.py` `_fetch` 的 sleep 行：從 `backoff_sec * attempt` 改為 `backoff_sec * (2 ** (attempt - 1))`（attempt 從 1 開始）
  - `core/pipeline.py` L65–66：webui_cfg 讀 `cover_retries` 和 `cover_backoff_sec` 的 fallback 預設改為 3 / 1.0，與模組常數一致
  - `_fetch` 的錯誤分類（ExternalError / ValidationError）**不改**，確保 4xx/5xx 不觸發重試

  **Patterns to follow:**
  - `src/select_cover.py` L77–101：現有 `_fetch` loop，沿用 `for attempt in range(1, retries + 1)` 結構
  - `browser/backend_driver.py` L66–87：retry 範本，確認 error-type guard 對齊

  **Test scenarios:**
  - Happy path：第一次下載成功 → 不重試，回傳 cover path
  - Happy path（retry）：前兩次拋 `urllib.error.URLError`（ExternalError），第三次成功 → 回傳 cover path，`time.sleep` mock 被呼叫**兩次**（1.0 s、2.0 s）— attempt 1 sleep 後繼續，attempt 2 sleep 後繼續，attempt 3 成功
  - Error path：連續三次 ExternalError（`retries=3`）→ 第三次 attempt 時不 sleep（直接 raise），`time.sleep` mock 共被呼叫**兩次**（[1.0, 2.0]，不是三次）；呼叫方收到 ExternalError
  - Error path：第一次拋 ValidationError（非圖片 content-type）→ 立即拋出，**不重試**（assert `time.sleep` 未呼叫）
  - Edge case：`retries=0`（CLI 手動傳入）→ 行為維持原本無重試，`time.sleep` 未呼叫（backward compat）
  - Integration（`time.sleep` mock）：`retries=3` 下三次全部 ExternalError → sleep call_args_list = [(1.0,), (2.0,)]，確認指數序列且最後一次 attempt 不 sleep

  **Verification:**
  - `pytest tests/test_select_cover.py` 全通過
  - `pytest -k select_cover` 通過，包含舊測試（舊 DEFAULT_RETRIES=0 的測試若存在需更新預期）

---

- [ ] **Unit 4：webui/app.py — `/history` 與 `/audit` 延遲量測（measure-first）**

  **Goal:** 以數據確認 `/history` 與 `/audit` 的 I/O 延遲，決定是否需要 TTL 快取

  **Requirements:** P3

  **Dependencies:** None

  **Files:**
  - Modify: `webui/app.py`（僅加量測，不改邏輯）
  - Test: `tests/test_webui_history.py`（量測 path，不影響現有測試）

  **Approach:**
  - 在 `/history` handler 函式開頭加 `t0 = time.perf_counter()`，在回傳前加 `logger.debug("history latency %.1f ms", (time.perf_counter() - t0) * 1000)`
  - 在 `/audit` handler 同樣加首尾量測
  - 使用 Python `logging` 模組而非 `print`；log level = DEBUG（不汙染正常 stdout/stderr）
  - 量測結果寫到 PR comment 或 `docs/plans/` 的補充說明後，決定 Unit 5 是否執行

  **Patterns to follow:**
  - `webui/app.py` L581：`_tail_audit` 已有 `st_size` 量測邏輯可參考（理解現有效能考量）

  **Test scenarios:**
  - Test expectation: none — 純量測注入，不改行為，現有 `/history` 與 `/audit` 測試應原封通過

  **Verification:**
  - 現有 webui 測試全通過（`pytest tests/test_webui*.py`）
  - 在本機手動觸發 `/history` 5 次（5 秒間隔），讀 debug log，記錄 p50 延遲

---

- [ ] **Unit 5：webui/app.py — TTL 快取（條件執行：Unit 4 量測 ≥ 5 ms 才做）**

  **Goal:** 消除 5 秒刷新的重複 I/O，讓第 2–N 次請求直接讀 in-process 快取

  **Requirements:** P3（條件）

  **Dependencies:** Unit 4 量測結果 ≥ 5 ms

  **Files:**
  - Modify: `webui/app.py`
  - Test: `tests/test_webui_history.py`

  **Approach:**
  - 在 `app.py` 模組頂部新增 `_cache: dict[str, tuple[float, Any]] = {}`（key → (expire_ts, value)）
  - 新增 `_get_cached(key, ttl, fn)` helper：若 key 存在且 `time.time() < expire_ts` 回傳快取值，否則呼叫 `fn()`，存入 cache，回傳值
  - cache key for `/history`：`f"history:{post_id}:{severity}:{run_id}"`（空字串表示無篩選）
  - cache key for `/audit`：`"audit"`（audit log 路徑固定）
  - TTL = 30 秒；不做精確 invalidate，讓快取自然過期（寬鬆失效）
  - 量測 log 可在本 unit 移除，或改為 cache hit/miss 統計 log

  **Patterns to follow:**
  - `webui/app.py` 的 `app.state` dict 存取模式（注意不要誤將 `_cache` 存 `app.state`，應是 module-level 以跨 request 保存）

  **Test scenarios:**
  - Happy path：連續兩次相同參數的 `/history` 請求，第二次命中快取（mock `runs.list_runs`，斷言第二次不再呼叫）
  - Happy path：TTL 過期後（mock `time.time` 跳 31 秒）→ 第三次請求重新呼叫 `list_runs`
  - Edge case：不同 filter 參數（post_id 不同）→ 各自獨立快取，不互相汙染
  - Error path：`runs.list_runs` 拋例外 → 不寫入快取，exception 正常傳播

  **Verification:**
  - `/history` 在 TTL 內的第 2 次請求 mock 計數 = 0（無 DB 呼叫）
  - `/audit` 同樣驗證
  - 現有 webui 測試全通過

## System-Wide Impact

- **Interaction graph**：Unit 1-2 只改 `core/runs.py` 和 `core/pipeline.py` 的寫入路徑，不影響 `src/publish_post.py`、WebUI `_submit_job`（各自有獨立的 `record_run` 呼叫，不傳 conn，走原本路徑）。Unit 3 只改 `select_cover` 和 pipeline 的 webui_cfg 讀預設，不改 CLI 介面。Unit 4-5 只改 `/history`、`/audit` handler，其他路由不受影響。
- **Error propagation**：P1 的 `open_run_conn` 若 conn 操作失敗，exception 正常傳播到 `run_pipeline` → `jobs.submit` 的 worker error handler；P2 的 ExternalError 在耗盡重試後仍向上傳播，不靜默吞掉；P3 快取讀錯 → fallback 呼叫原始函數，不中斷請求。
- **State lifecycle risks**：P1 batch conn 在 `run_pipeline` scope 結束時 commit + close，`open_run_conn` 用 contextmanager 確保 finally 路徑；P3 `_cache` 是 module-level dict，WebUI 重啟後自動清空（無持久化問題）。
- **Unchanged invariants**：`list_runs`（只讀）、`new_run_id`、`publish_post.record_run`（CLI 發布路徑）的行為完全不變；CLI I/O 契約（exit code、stdout/stderr）不變；WebUI URL 不變。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| P1 conn 在 background thread 上建立後，`list_runs`（主 thread）同時讀取 → WAL race | `list_runs` 走獨立 `_connect`，WAL 模式下讀寫不互阻，無 race |
| P2 指數退避讓批量 cover 最壞情況多 7 秒（三次重試）| 相對 exit 4 中斷整批，7 秒是可接受的 trade-off；已在 test scenario 中涵蓋 |
| P3 快取 key 設計錯誤導致不同用戶看到錯誤資料 | 此工具 localhost-only、單人，無多用戶資料隔離風險；key 設計保守即可 |
| P3 Unit 5 條件不滿足（量測 < 5 ms）→ 實作空轉 | Unit 5 明確標為條件執行，量測結果決定是否開始；不做就不做 |
| `select_cover.py` 舊測試若 hard-code `DEFAULT_RETRIES=0` 的行為 → 改預設值後測試紅燈 | Unit 3 明確要求更新對應測試（`retries=0` backward compat 路徑仍需保留）|

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-16-perf-maintainability-uplift-requirements.md](../brainstorms/2026-06-16-perf-maintainability-uplift-requirements.md)
- Related plan (retry pattern): [docs/plans/2026-06-15-005-refactor-project-optimization-phased-plan.md](2026-06-15-005-refactor-project-optimization-phased-plan.md)
- Related plan (auto-pipeline `_retry`): [docs/plans/2026-06-16-001-feat-auto-pipeline-plan.md](2026-06-16-001-feat-auto-pipeline-plan.md)
- Core files: `core/runs.py`, `core/pipeline.py`, `src/select_cover.py`, `webui/app.py`
- Retry reference: `browser/backend_driver.py` L66–87
