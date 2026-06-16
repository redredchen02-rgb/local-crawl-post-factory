---
date: 2026-06-16
topic: perf-maintainability-uplift
---

# 效能與可維護性提升（分三階段）

## Problem Frame

`local-crawl-post-factory` 在 v0.2.0.0 的功能面已完整：CI、去重修正、自動管線、批量操作、垃圾桶、歷史篩選全數落地，262 個測試全綠。但系統在兩個維度上仍有明顯欠缺：

1. **效能**：`webui/app.py` 的 audit 雖已優化尾讀（64 KB），但 SQLite runs 連線仍每筆重開（批量時可能數十次）；WebUI 歷史/audit 頁面無快取，每次請求全掃磁碟；Pipeline 重試機制只覆蓋後台命令，`select-cover` 圖片下載單次超時即 exit。

2. **可維護性**：`webui/app.py` 已從計畫時的 430 行長到 **722 行**，40+ 個函數全部塞在 `create_app()` 閉包內，路由難以獨立單測；CLI 與 WebUI 有各自的 pipeline 入口（`run_pipeline` vs `_submit_job`），邏輯漸漸偏離；mypy 仍有 2 個 `[union-attr]`/`[misc]` 錯誤待修；測試執行時間 ~44 秒，在快速迭代時偏慢。

本輪按「成本低且能立即讓後續改動更安全」的順序分三階段交付，每階段獨立可發布，不破既有 CLI I/O 契約，既有測試維持綠。

## Requirements

**Phase 1 — 效能修補（低風險、快見效）**

- P1. **Runs 連線批次化（Q8 落地）**：`run_pipeline` 的 dedupe-skip 迴圈與 build 迴圈改為共用單一 SQLite 連線（同一背景執行緒持有，不跨執行緒），消除每筆 `_ensure_schema` 的重複 open/migrate 成本。**前提**：先量測十筆批次的基準耗時（`time` + `--tb=short` log），若 < 50 ms 全段可考慮降為非目標；否則依量測結果決定方案。WebUI `_submit_job` 不受影響（各自一條連線，互不干涉）。
- P2. **select-cover 下載重試**：圖片下載逾時（`requests.exceptions.Timeout` / `ConnectionError`）改為帶指數退避的有限次重試（建議 3 次、1/2/4 秒），對應 `core/pipeline.py` 或 `src/select_cover.py` 的下載呼叫處。真實 HTTP 4xx/5xx 不重試（不掩蓋錯誤）。
- P3. **WebUI 歷史/audit 頁輕量快取（量測先行）**：**先以 `time.perf_counter` 量測 `/history` 端到端耗時**；若 < 5 ms 則降為非目標（單人 localhost，SQLite tail-read overhead 可忽略）。若量測確認有感，再加 30 秒 lru_cache（寬鬆失效，下次週期過期即可）。**條件**：快取引入「30 秒內可能看到舊資料」的邏輯複雜度，唯有量測證明必要時才值得這個 tradeoff。

**Phase 2 — app.py 拆分（Q10 完整落地）**

- P4. **APIRouter 拆分**：將 `webui/app.py`（722 行）依關注點拆成獨立模組，建議分組：
  - `webui/routers/packages.py`：packages 列表、detail、cover/image、delete
  - `webui/routers/backend_actions.py`：draft、verify、publish、batch_action
  - `webui/routers/history_audit.py`：history、audit、job_status
  - `webui/routers/settings_auth.py`：settings、save_settings、auth_status
  - `webui/routers/trash.py`：trash list、restore、empty_trash
  - `webui/app.py` 保留：`create_app()`（只做組裝）、共用 helpers（`_safe_pkg_dir`、`check_publish_gates`、`_tail_audit` 等）
- P5. **安全不變量維持**：拆分後所有解析貼文路徑的 handler 仍須呼叫 `_safe_pkg_dir`；三重發布閘門順序（reviewed → draft_verified → title）不得改變；既有路徑穿越與發布閘門測試對拆分後的 router 原封通過。
- P6. **共用狀態遷移**：`app.state`（`reviewed` / `session_expired_mtime` / `config_path`）繼續透過 `request.app.state` 存取；各 router 直接 import `_cfg()` 作為函數（**不改為 FastAPI Depends**——DI 重構是獨立決定，不是拆分必要條件，避免擴大 PR 風險介面）。URL 路徑與行為不變。

**Phase 3 — 可維護性收口**

- P7. **公共 pipeline API（R6）**：`core/pipeline.py` 的 `run_pipeline` 成為統一入口，WebUI 的 `_run_auto_pipeline` 改為呼叫 `run_pipeline` 而非平行實作，消除兩套邏輯偏離風險。**範圍限制**：不改 CLI I/O 契約，只是讓 WebUI 走同一條代碼路徑。
- P8. **mypy 完全清零**：修正 `webui/app.py:166`（`[misc]`）與 `webui/app.py:365`（`[union-attr]`）兩個現存錯誤，讓 mypy baseline = 0，並在 CI 改為**阻斷式**（移除 `continue-on-error`），防止回歸。
- P9. **測試提速**：分析 262 個測試中耗時前 10 名（`pytest --durations=10`），對 Playwright/browser 測試加 `pytest -m "not browser"` 標記，讓「core + webui 單測」可在 < 10 秒完成（快速 feedback loop），browser E2E 仍在 CI 完整跑。

## Success Criteria

- 十筆批次的 pipeline 執行中，runs 連線開啟次數 ≤ 2（非每筆一次）；select-cover 瞬時逾時被重試吸收，不出現 exit 4（P1–P2）。
- `/history` 頁面在 5 秒刷新間隔內的第 2–N 次請求無額外磁碟 I/O（P3）。
- `webui/app.py` 縮減至 < 200 行（只含組裝邏輯），各 router 模組可獨立 import 並單測（P4–P6）。
- `mypy webui/` 零錯誤、CI mypy step 為阻斷式（P8）。
- `pytest -m "not browser"` 在 < 10 秒完成（P9）；所有 262 個測試仍全綠。
- 每階段交付後既有測試維持綠，新增行為皆有對應測試。

## Scope Boundaries

- 不改 CLI I/O 契約（stdin/stdout/exit code 語意不變）。
- 不做 WebUI URL 路徑變更（純內部拆分，對外介面不動）。
- 不引入外部 cache 系統（Redis / memcached）——lru_cache 已足夠 localhost-only 使用情境。
- 不做 `src/` 命名空間套件重構（前輪已決定 YAGNI）。
- 不設覆蓋率硬門檻（P9 只做測速，不要求特定覆蓋率數字）。
- 沿用既有單站單後台、localhost-only、人工 auth-login 等所有邊界。

## Key Decisions

- **Q8 先量測再落地（P1）**：前輪計畫標「measure-first」，本輪確認落地——但若量測顯示數十筆批次 < 50 ms 全段，可降為非目標，不強行實作。
- **Q10 本輪完整做（P4–P6）**：app.py 已從 430 行長到 722 行，前輪「YAGNI」判斷已過時，拆分收益顯著提高。
- **mypy 改阻斷（P8）**：目前只剩 2 個錯誤，清零成本低，且改阻斷後能自動防回歸，性價比高。
- **測試速度優先於覆蓋率（P9）**：快速 feedback loop 對日常迭代價值更高，不以數字為目標。

## Dependencies / Assumptions

- P4–P6（router 拆分）依賴現有測試覆蓋 `_safe_pkg_dir` 與三重閘門——已有 characterization 錨（`test_webui_traversal.py`），可安全重構。
- P7（公共 pipeline API）假設 `run_pipeline` 的 I/O 契約（回傳 `PipelineResult`）已穩定，WebUI 的 `_run_auto_pipeline` 調整只是換呼叫點。
- P3 的快取失效策略：30 秒 TTL 寬鬆失效即可，不需精確 invalidate（WebUI 每 5 秒刷新，30 秒內最多多看 6 次舊資料是可接受的）。

## Outstanding Questions

### Resolve Before Planning
- [P1][User decision] Runs 批次化的「不做門檻」：量測後若耗時 < 50 ms（我建議的門檻），是否同意降為非目標？還是無論如何都要做？

### Deferred to Planning
- [P3][Technical] lru_cache 封裝策略：直接 wrap `_tail_audit`/runs query，還是在 router handler 層做 response cache？需考慮 app.state 的 mutable 參數傳遞。
- [P4][Technical] `_cfg()` dependency 的具體 FastAPI Depends 寫法，以及各 router 如何拿到 `out_dir` 等衍生值。
- [P7][Technical] `_run_auto_pipeline` 與 `run_pipeline` 的介面對齊：確認回傳型別、callback 參數是否完全相容，或需要加薄薄的 adapter。
- [P9][Technical] browser test 標記策略：用 `pytest.mark.browser` 還是 `pytest.mark.slow`？需確認哪些測試實際啟動 Playwright（`chromium.launch`）。

## Next Steps
→ `/ce:plan` for structured implementation planning（建議先就 Phase 1 P1–P3 做計畫）
