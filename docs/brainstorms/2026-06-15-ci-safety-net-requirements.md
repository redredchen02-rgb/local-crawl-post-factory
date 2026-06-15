---
date: 2026-06-15
topic: ci-safety-net
---

# CI 自動安全網（本輪最高 ROI · quality-uplift Phase 1 核心切片）

## Problem Frame

`local-crawl-post-factory` 功能已成熟：~3050 行、129 測試綠、CLI 契約嚴、R1–R10 功能項多已落地。經**對照代碼**的全面 ROI 盤點，單一最高槓桿的缺口是——**129 個測試卻零自動執行**。每次改動（包含後續所有質量修復）都賭「人記得跑 `pytest`」；一旦忘記，回歸靜默溜進來。

本輪只做一件事、做到位：**裝上 CI 自動安全網**。這是一次投資、之後每次改動自動受惠的複利項，且讓後續 Q9（reviewed 持久化）/ Q10（拆 app.py）等重構能在安全網下進行。父 backlog 見 [`2026-06-15-quality-uplift-requirements.md`](2026-06-15-quality-uplift-requirements.md)（Q1–Q10）；本輪刻意只取其中性價比最高的 CI 核心三項，把 mypy 與 pre-commit 留到下一輪。

## Requirements

**CI 工作流**
- R1. 新增 GitHub Actions workflow，於每次 `push` / PR 自動跑全測試套件（`pytest`，Python 3.11+），測試紅燈即 CI 紅燈。套件含 4 個 Playwright 端到端檔（`test_browser_flow`、`test_webui_actions`、`test_backend_driver_resilience`、`test_auth_login`）；**不得讓主測試靜默不跑**。因 `make install-browser` 已含 `playwright install chromium`、且測試未用 marker 分離，最簡實作為 CI 安裝 `.[browser,webui,dev]` + `playwright install --with-deps chromium` 後跑全套。

**Lint 鎖定**
- R2. 於 `pyproject.toml` 新增 `[tool.ruff]` 配置（規則集 + line-length **對齊現況**，避免大規模改動）；新增 `make lint`（跑 `ruff check`）；CI 跑 `ruff check`，違規即紅燈。本地已有 `.ruff_cache/0.15.10` 且近期剛清過 lint，代碼預期已大致通過，鎖定成本低。

**覆蓋率量測**
- R3. CI 跑 `pytest --cov`（將 `pytest-cov` 加入 dev extra 或 CI 內安裝），輸出覆蓋率數字/摘要。**只建立基線、不設硬門檻**（不以覆蓋率數字卡 merge），先讓數字看得見。

## Success Criteria

- **安全網生效**：開一個故意改紅一個測試的 PR，CI 會擋下（紅燈），合併前看得到失敗。
- **Lint 就位**：`make lint` 與 CI 的 `ruff check` 都跑得起來，現有代碼通過（或一次性對齊後通過）。
- **覆蓋率可見**：CI log 看得到覆蓋率數字；無因覆蓋率被卡的 merge。
- **基線維持**：既有 129 測試在 CI 維持綠，作為初始基線。

## Scope Boundaries

- **本輪只做 CI 核心三項**（pytest + ruff + 覆蓋率量測）。**mypy（Q3）與 pre-commit（Q5）延到下一輪**——mypy 對無 stub 的 Scrapy/Playwright 摩擦較高，性價比低於核心三項，單獨成輪較穩。
- **不動 CLI I/O 契約與退出碼語意**：本輪純新增（CI workflow + 配置），向後相容。
- **覆蓋率不設硬門檻**：只量測、建基線。
- **去重接 content_hash（Q6）不在本輪**，且建議降為待議（見 Key Decisions）。
- **`src/` 不改名**：沿用 quality-uplift 決策——高 carrying cost、不發佈 PyPI、零實際收益。
- 沿用既有單站單後台、localhost-only、外部 cron、人工 `auth-login` / 人工 `--approve` 等所有邊界。

## Key Decisions

- **CI 先做、單獨成輪**：對照代碼確認「129 測試零自動執行」是「看起來健康」與「持續健康」之間最大的缺口。CI 是複利投資（裝好後每次改動自動受驗證），且是後續 Q9/Q10 重構的安全前提，故最優先且自成一輪最快見效。
- **CI 跑全套含 Playwright**：因 `install-browser` 已備 chromium 安裝、4 個 browser 測試檔無 marker 可分離，「裝瀏覽器跑全套」是最簡且不漏測的作法；是否拆獨立 browser job / 加 marker，留給規劃視 CI 時長決定。
- **ruff 低摩擦**：`.ruff_cache/0.15.10` + 近期 `cleanup-preexisting-lint` 顯示代碼已大致 ruff-clean，故鎖定配置 + 接 CI 成本低。
- **mypy / pre-commit 延後**：摩擦高於核心、非複利關鍵路徑，下一輪再做，避免本輪被型別 stub 問題拖慢。
- **Q6（去重接 content_hash）降為待議**（挑戰 parent backlog）：① 誤跳需先有**已發布**項目的標題哈希碰撞，publish 人工低頻、碰撞極罕見；② 你的項目記憶已記載「content_hash 在 dedupe 時尚不存在」（dedupe 跑在 render-caption 之前）；③ `content_hash = hash(url+title+caption)`，使「title 且 content 都中」只在 url 也中時成立 → 等於**退化成 url-only**，是關掉標題比對而非修好它。屬設計取捨而非明確修復，不放進高 ROI 包。

## Dependencies / Assumptions

- CI 平台假設 **GitHub Actions**（本專案為 git repo、近期走 PR 流程、營運者慣用 `gh`）。若用其他平台需於規劃調整。
- 假設既有 129 測試本機全綠，作為 CI 初始基線。
- Python **3.11+**（`pyproject` requires-python）。
- ruff 沿用本地 **0.15.x** 線。

## Outstanding Questions

### Deferred to Planning
- [R1][Technical] CI 是否多 Python 版本矩陣（3.11 / 3.12）；以及 Playwright 策略——單 job 裝 chromium 跑全套 vs 拆獨立 browser job（後者需先加 pytest marker 才能分離 4 個 browser 檔）。視 CI 時長與穩定度決定。
- [R2][Technical] `[tool.ruff]` 精確 `select`/`ignore` 規則集與 line-length，需對齊現況一次跑通；是否一併啟用 `ruff format`。
- [R3][Technical] `pytest-cov` 放 dev extra vs CI 內單裝；覆蓋率報告形式（terminal summary / XML / 上傳 artifact / PR 註解）。

## Next Steps
→ `/ce:plan`（就本輪 CI 安全網做實作計畫）
