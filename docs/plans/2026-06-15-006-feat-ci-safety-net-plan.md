---
title: "feat: CI 自動安全網（pytest + ruff + 覆蓋率）"
type: feat
status: completed
date: 2026-06-15
origin: docs/brainstorms/2026-06-15-ci-safety-net-requirements.md
---

# feat: CI 自動安全網（pytest + ruff + 覆蓋率）

## Overview

`local-crawl-post-factory` 有 129 個測試卻**零自動執行**——每次改動都賭「人記得跑 `pytest`」。本計畫裝上 GitHub Actions CI，在每次 push / PR 自動跑 lint + 全測試 + 覆蓋率量測，把「靠人記得」變「機器自動擋」。這是複利投資：裝好後，後續每個質量修復（Q7–Q10）都自動受驗證。

本輪刻意只做 CI 核心三項；mypy 與 pre-commit 留到下一輪（見 origin 範圍邊界）。

## Problem Frame

經對照代碼的全面 ROI 盤點，最高槓桿缺口是缺乏自動護欄（origin 已確認：無 `.github/workflows`、`pyproject` 只有 setuptools/pytest 兩段 `[tool.*]`）。功能本身成熟（R1–R10 多已落地），所以這不是補功能，而是把「看起來健康」鎖成「持續健康」。

## Requirements Trace

- R1. GitHub Actions workflow，push / PR 觸發跑全 `pytest`（含 4 個 Playwright 端到端檔），不得讓主測試靜默不跑，紅燈即 CI 紅燈。（see origin: R1）
- R2. `[tool.ruff]` 鎖定 + `make lint` + CI 跑 `ruff check`。（see origin: R2）
- R3. CI 跑 `pytest --cov` 輸出覆蓋率，只建基線、不設硬門檻。（see origin: R3）

## Scope Boundaries

- 只做 CI 核心三項；**mypy（Q3）、pre-commit（Q5）不在本輪**。
- 不動 CLI I/O 契約與退出碼語意——純新增 workflow + 配置，向後相容。
- 覆蓋率不設硬門檻、不卡 merge。
- **去重接 content_hash（Q6）不在本輪**（origin 已降為待議）。
- `src/` 不改名；沿用單站單後台、localhost-only、外部 cron、人工 `auth-login` / `--approve` 邊界。

## Context & Research

### Relevant Code and Patterns

- `pyproject.toml`：`requires-python = ">=3.11"`；依賴 Scrapy/Pillow/PyYAML；extras：`browser`(playwright)、`webui`(fastapi…)、`dev`(pytest, httpx)；現有 `[tool.setuptools]`、`[tool.pytest.ini_options] testpaths=["tests"]`。CI 安裝面與 extras 直接對應。
- `Makefile`：已有 `install`(`.[dev]`)、`install-browser`(`.[browser,dev]` + `playwright install chromium`)、`install-webui`、`test`(`python3 -m pytest -q`)。新增 `lint` / `cov` target 與此風格對齊；CI 的安裝步驟可直接複用 `install-browser` 的路徑。
- **測試布局（已驗證）**：31 個測試檔 / 183 測試（README 的 129 已隨近期重構增長）。4 個檔需 Playwright：`tests/test_browser_flow.py`、`tests/test_webui_actions.py`、`tests/test_auth_login.py`、`tests/test_backend_driver_resilience.py`。
- **關鍵：Playwright 測試自帶守衛**——每個檔頭 `pytest.importorskip("playwright.sync_api")`，且 `test_browser_flow` / `test_webui_actions` / `test_auth_login` 還有「chromium 啟動失敗就 `pytest.skip(allow_module_level=True)`」。端到端用本地 `tests/mock_admin.py`（`MockAdmin`），**不需外網**，只需 chromium 二進位。
  - 推論：CI 若**不裝 playwright 套件**，4 檔皆 `importorskip` 跳過、CI 照樣綠 → 正中 origin R1「靜默不跑」警告。故 CI **必須**裝 playwright + chromium 讓它們真的執行。
  - **精度修正（feasibility）**：4 檔中只有 `test_browser_flow` / `test_webui_actions` / `test_auth_login` 帶 chromium-launch 守衛；`test_backend_driver_resilience` **僅** `importorskip`。故「裝了 playwright 套件但 chromium 啟動失敗」時，前 3 檔 skip、第 4 檔**報錯轉紅**（fail-loud，非靜默）——對護欄而言可接受，但診斷紅燈時須知此差異。

### Institutional Learnings

- `docs/solutions/` 不存在（已查）——無既有機構知識可援引。
- 專案記憶（`memory/`）：dedupe READ-ONLY 約束、stacked-PR merge gotcha——與本 CI 計畫無直接衝突，但 CI 啟用後 PR 合併流程會被自動護欄覆蓋，對 stacked-PR 操作是淨增益。

### External References

- 未做外部研究：GitHub Actions + pytest + ruff 是高度標準化的 CI 形態，且本地已驗證可行，外部資料邊際價值低。

## Key Technical Decisions

- **CI 跑全套含 Playwright（裝 chromium）**：因測試自帶 `importorskip`/`skip` 守衛，不裝 chromium 會靜默跳過 4 個端到端檔。CI 安裝 `.[browser,webui,dev]` + `playwright install --with-deps chromium`，讓守衛在 CI 不觸發、測試真的跑。這是兌現 R1「不得靜默不跑」最直接的作法。
- **lint 與 test 拆兩個並行 job**：`lint` 不需瀏覽器、秒級回饋；`test` 需裝 chromium、較慢。拆開讓 lint 快速失敗、不被 chromium 安裝拖住，是標準慣例而非過度設計。
- **ruff 鎖「現況」零摩擦**：已驗證 `ruff check .`（0.15.10）對當前樹回報 **All checks passed!**，故 gate 第一天不會紅。`[tool.ruff]` 以鎖定現有通過行為為原則——鎖 ruff 預設 select（`E4/E7/E9/F`）。**實作結果**：原擬補 `I`(isort)，但實測 `--select I` 會讓現樹紅 31 處 import 排序 → 為守零摩擦本輪**不納入 `I`**，降為 deferred 調優（`ruff check --select I --fix` 可一鍵補）。**注意**：預設 select 不含 `E501`，故現有最長 125 字元的行目前不被線長檢查；line-length 是否強制留作調優（見 Deferred）。
- **覆蓋率只量測不設門檻**：`pytest-cov`（本地已裝 7.1.0）寫進 dev extra 讓 CI 也有；`--cov-report=term-missing` 印出數字建基線，不卡 merge——避免一上來就因覆蓋率擋住合併。
- **Python 單版 3.11 起步**：對齊 `requires-python` 下限與本地 3.11.15。多版本矩陣（+3.12）是廉價後續，非本輪必需（見 Deferred）。
- **plan type = feat**：新增「自動安全網」這項能力；非重構、非修 bug。

## Open Questions

### Resolved During Planning

- Playwright 在 CI 怎麼處理？→ **裝 chromium 跑全套**（單一 `test` job），理由見 Key Decisions（守衛會靜默跳過）。
- ruff 啟用會不會立刻紅？→ **不會**，已驗證當前樹通過 ruff 0.15.10 預設規則。
- pytest-cov 從哪來？→ 寫進 `dev` extra（本地已裝，CI 需顯式聲明才可重現）。
- CI 平台？→ GitHub Actions（repo 已走 PR 流程、營運者慣用 `gh`）。

### Deferred to Implementation

- **（純調優，不阻擋 Unit 1 完成）** `[tool.ruff]` 的精確 `select`/`ignore` 與 line-length：設好配置後跑 `ruff check` 確認仍 All passed，再決定是否納入 `E501`（納入則需把 line-length 設到 ≥125 或修那 1 行）/ 是否啟用 `ruff format`。Unit 1 以「鎖定現有通過行為」即算完成，此調優可後續迭代。
- 是否新增 `browser` pytest marker：本輪已用 fail-closed `-rs` skip 偵測達成「e2e skip 即紅」的硬保證；marker 是 round-2 更乾淨的等價形式（可用 `-m browser` 精準選取、讓斷言不靠檔名字串比對），非必需的升級。
- Python 版本矩陣是否加 3.12。
- 覆蓋率報告形式是否超出 terminal（XML / artifact / PR 註解 / Codecov）。
- chromium 安裝是否加快取以縮短 CI 時長。
- **依賴浮動姿態（本輪明示接受，feasibility）**：除 `ruff ~=0.15.10` 外，`pytest`/`httpx`/`playwright` 維持開放下限、無 lockfile/快取，chromium 版本隨 playwright 浮動；「與 CI 同版本」目標本輪僅對 ruff 成立。若日後要可重現，再加 pip 快取（以 pyproject 為 key）或釘 playwright minor。
- **CI 安全基線（實作 workflow 時落實，security-lens）**：
  - 設頂層最小權限 `permissions: contents: read`（本 CI 只需 checkout + 跑測試，無寫權限需求）。
  - 觸發維持 `pull_request`（**非** `pull_request_target`）：`test` job 會執行不可信 PR 代碼（任意 `pip install` + PR 測試檔）；`pull_request_target` 會把 repo secrets 暴露給該代碼。保持 CI **不放任何 secret**。
  - 第三方 action（`actions/checkout`、`actions/setup-python`）至少釘到驗證過的版本標籤，最好釘 commit SHA。
  - 認知：`playwright install --with-deps chromium` 會跑特權 `apt`，加上任意 pip 依賴 + PR 測試碼，使 `test` job 成為主要不可信執行面；以 ephemeral runner + 最小權限 token + 無 secret 框限。
  - 若日後採 Codecov／PR 註解需 token，須確保該 token 不落入不可信 PR 代碼可達範圍。

## Implementation Units

依賴順序：Unit 1、Unit 2 各自獨立（都只改 `pyproject.toml` + `Makefile`，可並行）；Unit 3（CI workflow）依賴 1、2 的 target 與 dev extra 就位後才能在 CI 調用它們。

- [ ] **Unit 1: ruff 鎖定 + `make lint`**

**Goal:** 把目前「手動跑 ruff」鎖成可重現的配置與本機指令。

**Requirements:** R2

**Dependencies:** 無

**Files:**
- Modify: `pyproject.toml`（新增 `[tool.ruff]`；把 `ruff` 釘版加入 `[project.optional-dependencies].dev`）
- Modify: `Makefile`（新增 `.PHONY` 的 `lint` target，跑 `python3 -m ruff check .`）

**Approach:**
- `[tool.ruff]`：`target-version` 對齊 py311；`line-length` 設一個涵蓋現有程式碼的值（現存最長 125、次長 105/104）；`select` 以「鎖定現有通過行為」為準（預設 `E4/E7/E9/F` + `I`），不擴增到會讓現樹變紅的規則。
- `ruff` 釘到本地線（`~=0.15.10`）寫進 dev extra，讓 `make install` 後 `make lint` 可用、與 CI 同版本。

**Patterns to follow:** `Makefile` 既有 target 風格（`python3 -m …`、`.PHONY` 列表）；`pyproject` 既有 `[tool.*]` 段。

**Test scenarios:**
- Test expectation: none — 純 lint 配置，無執行期產品行為變更。
- **Verification（非 pytest）**：`make lint` 對當前樹回報 All checks passed（已預驗證為真）。

**Verification:**
- `make lint` 退出 0；`ruff` 出現在 `.[dev]` 安裝清單。

- [ ] **Unit 2: 覆蓋率量測接入**

**Goal:** 讓 `pytest --cov` 可重現地印出覆蓋率基線。

**Requirements:** R3

**Dependencies:** 無

**Files:**
- Modify: `pyproject.toml`（`pytest-cov` 加入 `[project.optional-dependencies].dev`；可選 `[tool.coverage.run] source` 限定到 `core,src,browser,webui`）
- Modify: `Makefile`（新增 `cov` target：`python3 -m pytest --cov --cov-report=term-missing`）

**Approach:**
- 覆蓋率用**顯式 per-package** `--cov=core --cov=src --cov=browser --cov=webui`（而非僅靠 `[tool.coverage.run] source`），既避免把 tests/第三方算進去，也避免漏給 `--cov` 目標時量不到東西（feasibility）。
- **本輪覆蓋率不得使 build 轉紅**：不設 `--cov-fail-under`；某套件零執行資料時的 no-data 警告可容忍——先在乾淨樹確認 `make cov` 退出 0 再接 CI。
- `pytest-cov` 本地已裝（7.1.0），此處是把它顯式聲明進 dev extra 以利 CI 重現。

**Patterns to follow:** 既有 `test` target（`python3 -m pytest -q`）。

**Test scenarios:**
- Test expectation: none — 覆蓋率工具接線，無產品行為變更。
- **Verification（非 pytest）**：`make cov` 跑完印出覆蓋率百分比與 missing 行；無因覆蓋率導致的非零退出。

**Verification:**
- `make cov` 印出覆蓋率摘要且退出 0；`pytest-cov` 在 `.[dev]` 清單。

- [ ] **Unit 3: GitHub Actions CI workflow**

**Goal:** push / PR 自動跑 lint + 全測試（含真正執行的瀏覽器端到端）+ 覆蓋率，紅燈即擋。

**Requirements:** R1（並在 CI 中執行 R2、R3）

**Dependencies:** Unit 1、Unit 2（CI 調用 `ruff check` 與 `pytest --cov`，且依賴 dev/browser extra 內含 ruff/pytest-cov）

**Files:**
- Create: `.github/workflows/ci.yml`

**Approach:**
- 觸發：`push` 與 `pull_request`。
- Job `lint`（ubuntu-latest）：checkout → `actions/setup-python@v5`（3.11）→ `pip install -e '.[dev]'` → `ruff check .`。
- Job `test`（ubuntu-latest，與 lint 並行）：checkout → setup-python 3.11 → `pip install -e '.[browser,webui,dev]'` → `python -m playwright install --with-deps chromium` → `pytest --cov=core --cov=src --cov=browser --cov=webui --cov-report=term-missing -rs`。
- chromium 安裝是兌現「瀏覽器測試真的跑、不靜默跳過」的關鍵步驟。
- **fail-closed skip 偵測（本輪硬保證 R1）**：`test` job 在 pytest 後加一步斷言——掃 `-rs` 的 skip 摘要，若 4 個 e2e 檔（`test_browser_flow`/`test_webui_actions`/`test_auth_login`/`test_backend_driver_resilience`）任一被 skip，即讓 job 失敗。把「裝了 chromium 就不會 skip」的隱式保證轉成機器強制，不必等 round-2 的 `browser` marker。

**Execution note:** 護欄的「測試」是執行期行為——交付後需以一個故意改紅某測試的分支/PR 觀察 CI 轉紅、再還原，確認 gate 真的會擋（屬執行期驗證，非可在計畫內預跑的單元測試）。

**Technical design（directional，非實作規格）：**
```
on: [push, pull_request]
permissions:            # 最小權限（security-lens）：本 CI 無寫權限需求
  contents: read
jobs:
  lint:   setup-py3.11 → pip install .[dev]                → ruff check .
  test:   setup-py3.11 → pip install .[browser,webui,dev]
                       → playwright install --with-deps chromium
                       → pytest --cov=core --cov=src --cov=browser --cov=webui --cov-report=term-missing -rs
                       → assert 4 個 e2e 檔皆未 skip（掃 -rs 摘要，有 skip 即 job 紅）
```
> 上述為審查用方向性示意，非照抄的最終 YAML。實作期補齊 action 版本、快取、權限等細節。

**Patterns to follow:** `Makefile` 的 `install-browser`（已是「裝 browser extra + `playwright install chromium`」的權威路徑）。

**Test scenarios:**
- Test expectation: none — CI 配置檔，無應用層行為；其「測試」是 CI 自身行為。
- **Integration（執行期驗證）**：① 一個讓某既有測試失敗的 PR → `test` job 紅、PR 被擋；② 一個含 lint 違規（如刻意留未用 import）的 PR → `lint` job 紅；③ 乾淨 PR → 兩 job 綠，且 skip 偵測步驟確認 4 個 Playwright 端到端**實際執行**（非僅靠人工讀 log）；④ 覆蓋率數字出現在 `test` 日誌；⑤（護欄自身）一次性模擬某 e2e 檔被 skip（如故意不裝 chromium）→ skip 偵測讓 `test` job 紅，證明 fail-closed 有效。

**Verification:**
- push/PR 觸發兩 job；乾淨樹全綠；**skip 偵測步驟強制** 4 個瀏覽器 e2e 實際執行（任一 skip → job 紅）；覆蓋率可見；任一測試或 lint 失敗都使對應 job 紅。

## System-Wide Impact

- **Interaction graph:** 不動任何執行期程式碼路徑（CLI / pipeline / webui / browser driver 全未改）。影響面僅止於 repo 的 PR / push 工作流——合併前多一道自動 gate。
- **Error propagation:** 不變。CI 失敗只阻擋合併，不改變應用的退出碼或錯誤語意。
- **State lifecycle risks:** 無——不碰 SQLite state / runs / out 產物。
- **API surface parity:** 不變。CLI 12 個 `console_scripts`、I/O 契約、退出碼全部原樣。
- **Integration coverage:** CI 首次讓瀏覽器端到端在「乾淨容器」中實際執行並**量測覆蓋率基線**（本機可能因沒裝 chromium 而長期 skip）。
- **Unchanged invariants:** `pyproject` 的 `[tool.setuptools]`/`[tool.pytest.ini_options]`、所有 extras 既有內容、`requires-python`、CLI 行為——本計畫只**新增**段落與檔案，不改既有語意。本機開發者沒裝 chromium 仍可開發（測試在本機照舊優雅 skip，只有 CI 強制跑）。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 啟用 ruff gate 後第一次 CI 立刻紅（既有違規） | 已預驗證 `ruff check .` 對當前樹 All passed；`[tool.ruff]` 以鎖定現況為準、不擴增會變紅的規則 |
| CI 把 chromium 裝了卻仍 skip（隱式保證失效，端到端靜默不測） | **本輪以 fail-closed skip 偵測（`-rs` 摘要掃描，任一 e2e skip 即 job 紅）強制**；round-2 可再升級為 `browser` marker |
| Playwright 安裝拖慢 CI / 偶發 flaky | 釘 playwright 版本、用 `--with-deps`；必要時加快取或拆 browser job（Deferred）|
| 覆蓋率數字製造噪音或誤導 | 不設門檻、只 term 報告建基線；source 限定到專案套件 |
| `pytest-cov` 本機已裝但未入 extra，CI 缺它而報錯 | Unit 2 顯式寫進 dev extra，確保 CI 可重現 |
| 覆蓋率 `source` 列了零執行資料的套件 → 可能非零退出（feasibility） | 用顯式 per-package `--cov=`；本輪不設 fail-under、容忍 no-data 警告、先確認乾淨樹 `make cov` 退出 0 |
| 預設 `GITHUB_TOKEN` 範圍過寬，不可信 PR 可用 token 改 repo（security-lens） | 設 `permissions: contents: read` 最小權限 |
| 不可信 fork PR 代碼執行：pip install + 測試碼 + 特權 apt（security-lens） | 維持 `pull_request`（非 `pull_request_target`）、CI 不放 secret、靠 ephemeral runner 框限 |
| 第三方 action tag 被劫持跑任意代碼（security-lens） | action 釘驗證版本／commit SHA |

**Dependencies:** GitHub Actions（假設；repo 已走 PR 流程）。Python 3.11。ruff 0.15.x。既有 183 測試本機全綠作為初始基線。

## Documentation / Operational Notes

- README「測試」段可補一行「CI 於 push/PR 自動跑全套 + lint + 覆蓋率」（可選，低優先）。
- 營運上：CI 綠燈成為合併前的事實前提；對既有 stacked-PR 流程是淨增益（每層 PR 自動受驗）。

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-15-ci-safety-net-requirements.md](docs/brainstorms/2026-06-15-ci-safety-net-requirements.md)
- 父 backlog：[docs/brainstorms/2026-06-15-quality-uplift-requirements.md](docs/brainstorms/2026-06-15-quality-uplift-requirements.md)（Q1–Q10）
- 關鍵代碼：`Makefile`（`install-browser`）、`pyproject.toml`（extras）、`tests/test_browser_flow.py:15-24`（importorskip/skip 守衛）、`tests/mock_admin.py`（端到端不需外網）
- 預驗證事實：`ruff check .` → All checks passed（0.15.10）；Python 3.11.15；pytest-cov 7.1.0 已裝
