---
date: 2026-06-15
topic: quality-uplift
---

# 質量提升：自動守護 → 正確性收口 → 可維護性（分三階段）

## Problem Frame

`local-crawl-post-factory` 的功能性優化已落地（基礎管線、日常營運硬化、WebUI 全控制台，前兩輪 R1–R10 大致完成）。代碼本身乾淨、契約嚴格、註解到位。**本輪不是補功能，而是把這個功能成熟的項目「推進成質量更高的版本」**——做法分兩個層次：

1. **把質量自動守住**：目前 144+ 測試只靠開發者本機記得跑 `pytest`，沒有任何自動護欄（無 CI、無 lint 鎖定、無型別檢查、無覆蓋率）。一旦人忘了跑，回歸會靜默溜進來。
2. **收口代碼裡幾個便宜但會靜默出錯的小缺口**：讀碼時抓到 4 個——其中去重的 `content_hash` 已經存了卻沒接進判定（前輪 R4 只做一半）、發布階段的生命週期記錄斷鏈、批量寫入每筆重開 SQLite 連線、發布閘門的「已審核」狀態一重啟就失。

三簇全做、分三階段、每階段獨立可發布、向後相容、既有測試維持綠。

## 三簇定位（按性價比與依賴排序）

| 階段 | 簇 | 解決什麼 | 成本 | 風險 | 為何這個順序 |
|---|---|---|---|---|---|
| Phase 1 | A 自動守護 | 質量從「靠人記得」變「機器自動擋」 | 低（多為配置檔） | 低 | 最便宜、最快見效，且裝好後 Phase 2/3 的每次改動都自動被驗證 |
| Phase 2 | B 正確性收口 | 修掉會靜默出錯的 4 個缺口 | 低–中 | 低 | 在 CI 護欄下修，每個 fix 都有紅綠燈背書 |
| Phase 3 | C 可維護性 | 降低日後改動成本 | 中 | 中 | 在完整安全網下重構，最後做 |

## Requirements

**Phase 1 — 自動守護（簇 A：質量基礎設施）**
- Q1. **CI**：新增 GitHub Actions workflow，在每次 push / PR 自動跑 `pytest`（Python 3.11+），測試失敗即紅燈。**測試基線須先界定清楚**（見 Outstanding 的 Q1 決策）：有 3 個測試直接 `chromium.launch()`，無瀏覽器時是 FAIL 不是 skip——須決定 CI 是裝 Playwright 跑全套，或拆成「核心+webui job（恆跑）＋ browser E2E job（裝 chromium，可標記）」。**憑證安全**：CI 絕不持久化/上傳憑證級資料——測試一律用合成的 `tmp_path` storage_state（repo/runner 無真實 `auth/` 檔）；coverage/log artifact 範圍排除 storage_state、state DB、audit log；browser job 若需真實登入態僅經遮罩 secret 注入、不入 log。
- Q2. **Lint 鎖定**：在 `pyproject.toml` 加 `[tool.ruff]` 配置，CI 跑 `ruff check`，本機加 `make lint`。具體基線：line-length≈100、規則族至少 `E,F,I,B`（既有程式已依賴 `BLE001`）；以 `ruff check --statistics` 對 main 跑出現存違規，用 per-rule ignore 收編，**不為了過 lint 改既有程式**。
- Q3. **型別檢查**：導入 mypy，以現有 type hints 為基線、**漸進式**（允許 `ignore_missing_imports`、可先對 `core/` 嚴格、其餘寬鬆）。**CI 政策（比照 Q4）**：mypy 以**非阻斷**（continue-on-error）方式跑，記錄基線錯誤數、**不擋 build**；未來階段可把 `core/` ratchet 成阻斷。目標是建立機制與基線，不是一次全覆蓋。
- Q4. **覆蓋率量測**：CI 跑 `pytest --cov` 輸出覆蓋率。**先量測、建立基線，不設硬性門檻**（避免一上來就卡 merge）。
- Q5.（可選、低成本）**pre-commit hook**：commit 前本機跑 ruff，規則與 CI 一致，減少紅燈往返。

**Phase 2 — 正確性收口（簇 B：代碼缺口）**
- Q6. **修正去重的「標題誤跳」（決定：甲案——去重只認 `canonical_url`）**：`is_processed` / `skip_reason` 的跳過條件改為**只比對已發布的 `canonical_url`**，移除單獨的 `title_hash` 跳過——這是「標題相同但 URL 不同的不同文章被靜默跳過」的根因。`content_hash` **不進去重判定**（公式含 URL、無鑑別力；續留作 watermark 檔名決定性即可）。`skip_reason` 簡化為只回 `'url'` 或 `None`。必須**更新鎖定舊行為的既有測試** `tests/test_dedupe_posts.py::test_same_title_hash_different_url_skipped`（改為斷言「同標題不同 URL 必須放行」），並新增放行路徑測試。
- Q7. **補 publish 階段的生命週期關聯**：`publish_post._run` 的 `runs.record_run` 帶上 `run_id` 與 `severity`（目前缺）。**注意**：publish 是獨立進程、自己生 run_id，與 pipeline 的 run_id 無關——若要兌現「用一個 run_id 撈出含發布的整條記錄」，必須把 build 當下的 run_id **寫進 manifest、publish 時讀回**（join key 待規劃）。否則 Q7 退而求其次只給發布一個自有 run_id+severity（不跨進程關聯）。另：WebUI 發布走 `_submit_job` 會另生 run_id，須避免一次發布寫出兩筆。
- Q8.（**效益待確認，可能降為非目標**）**批量 runs 寫入不再每筆開連線**：`run_pipeline` 的 dedupe-skip 迴圈（每筆 skip +1 連線）與 build 迴圈（每筆成功/失敗 +1 連線）各自呼叫 `runs.record_run`，各自開連線 + 跑一次 schema migration。改為共用單一連線批次寫入（或新增 `record_runs` 批次 API）。**保留意見**：在「數十筆」單機規模、且 migration 是近乎 no-op 的 `CREATE TABLE IF NOT EXISTS`，效益可能不顯著；且 WebUI 批量路徑在背景執行緒呼叫 record_run，跨執行緒共用連線受 sqlite `check_same_thread` 約束。規劃時先量一筆基準再決定是否做。
- Q9. **發布閘門①「已審核」持久化**：`app.state.reviewed` 從記憶體 `set` 改為可跨重啟的持久標記，避免 WebUI 重啟後所有貼文都得重開審核頁才能發布。閘門語意不得放鬆（仍須三重確認）。**安全不變量（必含）**：① 持久標記須記錄被審核當下的內容識別（content_hash 或 manifest mtime）；② 發布閘門須在「當前內容 ≠ 已審核內容」時**拒絕**（測試：已審核→內容被 re-render 改寫→發布 400）；③ 持久化不得讓過期審核滿足閘門①；④ 標記須存在**營運者側**（state 表 / runs），**不寫進會被 re-render 覆寫的 `manifest.json`**（自我認證、最弱）。

**Phase 3 — 可維護性（簇 C：降低改動成本）**
- Q10. **拆 `webui/app.py`**：目前是近 300 行的 `create_app` 閉包（全檔 417 行）、20+ 路由內嵌函數，難單獨單測。依關注點拆成 FastAPI APIRouter（建議分組：packages / backend-actions / history-audit / settings-auth），`create_app` 只負責組裝。**注意**：所有路由共享 `app.state`（`config_path` / `reviewed` / `session_expired_mtime`）與 `_cfg()` / `jobs` 等閉包——拆分須先決定共享機制（FastAPI dependency vs 模組級），「可獨立單測」仍需 app wiring，並非零成本機械拆分。**安全不變量**：每個解析貼文路徑的處理器搬移後仍須呼叫 `_safe_pkg_dir`（不得自行拼 `out_dir/post_id`）；發布處理器三道閘門順序不變（reviewed→draft_verified→title）；既有路徑穿越與發布閘門測試須對拆分後的 router **原封通過**。

## Success Criteria

- **自動守護生效**：開一個故意讓測試紅燈的 PR，CI 會擋下；`ruff check` 與 mypy 在 CI 跑得起來；覆蓋率數字看得到（Q1–Q5）。
- **去重正確**：發布過一篇標題為 X 的文章後，另一篇 URL 不同但標題也是 X 的文章**不會被誤跳**（有測試證明）；同一 URL 仍正確跳過（Q6）。
- **生命週期不斷鏈**：發布記錄帶 `run_id`/`severity`；**若採 manifest 串接 run_id**，則用一個 `run_id` 能撈出含發布在內的整條記錄（Q7；join 方式見 Deferred）。
- **批量寫入收斂**：一次數十筆的批次，runs 寫入只用一條連線（Q8）。
- **重啟後可發布**：WebUI 重啟後，先前已審核過的貼文仍滿足閘門①，無需重開審核頁（Q9）。
- **可單測路由**：`webui` 的路由處理器可在不啟整個 app 的情況下被單獨測試（Q10）。
- 每階段交付後既有測試維持綠，新增行為皆有測試（含去重誤跳的拒絕路徑）。

## Scope Boundaries

- **不改 CLI I/O 契約與退出碼語意**：本輪以新增（CI/工具）+ 內部重構 + 小修為主，向後相容。
- **不把 `src/` 改名為正規命名空間套件**：雖然頂層 `src/` 套件名是已知反模式，但改名會動到所有 import、`console_scripts`、全部測試，carrying cost 高，而本工具是 local-first、不發佈 PyPI，無實際收益 → **本輪刻意不做**（若未來要對外發佈再議）。
- **覆蓋率不設硬門檻**：本輪只建立量測與基線，不以覆蓋率數字卡 merge。
- **型別檢查不要求全覆蓋**：漸進式導入，不為了通過 mypy 而大改既有邏輯。
- 沿用既有單站單後台、localhost-only、外部 cron、人工 `auth-login`、人工 `--approve` 等所有既有邊界。

## Key Decisions

- **A 與 B 的順序有依賴，非單純 A 先**：CI/lint 是會「複利」的投資（裝好後每次改動自動被驗證）。但審查指出一個依賴：**Q6 會修改一條既有測試**（它鎖定了舊的去重行為），若 CI 先就位，會把「舊的錯誤行為」當成綠燈基線鎖死，之後 Q6 還得回頭打掉它。因此 **Q6（去重修正）應與 CI 同階段、或在 CI 之前落地**，其餘 B 項可在 CI 護欄下進行。
- **去重修正方向：消除標題誤跳本身（Q6＝甲案，去重只認 `canonical_url`）**：沿用前輪「寧可重複處理（人工可攔），不可靜默漏發」。審查證實原訂「title AND content_hash」因 content_hash 公式含 URL 而等同 URL-only、無鑑別力。**決定採甲案**：直接移除 title 跳過規則，去重只認 canonical_url；最簡、零 schema 變動、徹底消除標題誤跳。「同文換 URL 重貼」交由既有人工審核攔截（每篇本就過審）。
- **Phase 1 採全套一次到位（CI+ruff+mypy+coverage+pre-commit）**：scope-guardian 質疑 mypy/coverage 對單人本機工具的性價比；經權衡仍採全套——mypy 非阻斷基線與 coverage 量測皆屬低持續成本，且為「質量更高的版本」一次打底，省去日後分批導入的協調成本。
- **`src/` 改名列為非目標**：YAGNI——高 carrying cost、零實際收益（不發佈）。
- **重構不破契約（Q10）**：路由拆分以「不改 URL 與行為」為硬約束，純內部重構 + 測試護欄。

## Dependencies / Assumptions

- 假設 CI 平台為 **GitHub Actions**（本專案為 git repo、營運者慣用 `gh`）。若實際用其他平台（如自架 runner）需於規劃調整。
- 沿用既有 `core/`、`browser/`、`webui/`、CLI 與 `configs/*.yaml`，以擴充/小修/重構為主。
- 假設既有測試目前全綠作為 CI 基線——但有兩個但書：① Q6 會使既有 `test_same_title_hash_different_url_skipped` 失敗（它鎖定舊行為），該測試須隨 Q6 一併更新；② browser/webui 中 3 個直接 `chromium.launch()` 的測試需先 `playwright install` 才綠（見 Q1 決策）。CI 落地前須先確認「乾淨環境下的真實綠燈子集」。

## Outstanding Questions

_（兩個原本阻擋規劃的決策已拍板，移入 Key Decisions：Q6＝甲案只認 `canonical_url`；Phase 1＝全套一次到位。）_

### Deferred to Planning
- [Q1][Technical] CI 測試基線子集與 Playwright 策略：裝 Playwright 跑全套 vs 拆「核心+webui job（恆跑）＋ browser E2E job（裝 chromium、可標記）」；是否多 Python 版本矩陣。**建議**：拆 job、核心恆跑、browser 獨立。
- [Q3][Technical] mypy 分層 strict 範圍與 `ignore_missing_imports` 對無 stub 依賴（Scrapy/Playwright）的處理（CI 政策已定為非阻斷）。
- [Q7][Technical] run_id 的 join key：是否把 build 當下 run_id 寫進 manifest、publish 讀回以跨進程關聯；以及 WebUI 發布避免重複寫 record 的做法。
- [Q8][Technical]（若決定做）批次寫入介面形狀：`record_runs(path, rows)` vs 持有單一連線；與背景執行緒 `check_same_thread` 的互動。
- [Q9][Technical] 已審核持久化的具體儲存（`state` 表新欄 vs `runs` 推導）與內容識別欄（content_hash vs mtime）的選擇；與多分頁/重整的互動。
- [Q10][Technical] APIRouter 分組邊界與共用 `app.state` / `_cfg()` / `jobs` 的機制（FastAPI dependency vs 模組級）。

## Next Steps
阻擋決策已全部拍板（Q6＝甲案只認 `canonical_url`；Phase 1＝全套）。→ `/ce:plan`（建議第一批把 **Q6 與 CI 放同一階段**落地，以免 CI 先把舊去重行為鎖成綠燈基線）。
