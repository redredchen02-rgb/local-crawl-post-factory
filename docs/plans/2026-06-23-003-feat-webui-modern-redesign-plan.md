---
title: "feat: WebUI Modern Redesign — Sidebar Layout + Design System"
type: feat
status: completed
date: 2026-06-23
---

# feat: WebUI Modern Redesign — Sidebar Layout + Design System

## Overview

重新設計 WebUI 的視覺層——從平面頂部導航升級為側邊欄佈局，建立更完整的設計語言（設計令牌、組件、排版），讓這個每日使用的內容流水線工具達到「工具級」視覺品質。所有後端邏輯、HTMX 互動、路由保持不變；唯一改動是 CSS 和 Jinja2 模板的視覺層。⚠️ `test_webui_history.py` 和 `test_webui_settings.py` 的兩組斷言需在對應 unit 合入前依計劃更新（詳見 Context & Research 和各 unit 說明）。

## Problem Frame

現有 WebUI 功能完整，但視覺上是「早期原型」感：
- 頂部導航欄無活躍頁面標示，7 個鏈接緊排，空間感弱
- 側邊欄式佈局更適合這類操作型工具（參考：Linear、Notion、GitHub Codespaces）
- 設計令牌已有良好基礎（`--accent`, `--ink` 等），但缺少 sidebar 色彩、spacing scale、surface 層次
- 所有頁面共用同一個寬度上限（860px），缺少 main area 與 sidebar 的空間切分
- 狀態徽章、步驟進度條、表格行都功能正確，但視覺品質有提升空間
- 無暗色模式支援

技術約束：零構建工具（no Webpack/Vite/npm）、純 CSS + HTMX + Jinja2。

## Requirements Trace

- R1. 側邊欄佈局替代頂部導航欄，帶活躍頁面高亮
- R2. 擴充設計令牌系統（sidebar 色彩、spacing scale、surface 層次）
- R3. 暗色模式支援（`prefers-color-scheme: dark`，純 CSS）
- R4. 所有現有 HTMX 互動完整保留（targets、swap mode、partials 結構不變）
- R5. 零構建約束保留（單一 `app.css` 文件，無編譯步驟）
- R6. 移動端友好（側邊欄可收合，content 維持可用）
- R7. Dashboard、套件清單、詳情頁、今日備稿、設定頁的信息層次改善
- R8. 狀態徽章、步驟進度條統一使用 design system，視覺更精緻

## Scope Boundaries

- 不新增路由或後端 API
- 不改動任何 Python 業務邏輯（`core/`, `browser/`, `cli/`）
- 不引入前端框架或構建工具
- 不改變發布安全模型（三重確認閘門、路徑校驗）
- CSS 維持單一 `app.css` 文件（不拆分）
- HTMX 版本不升級（保留現有 `htmx.min.js`）
- 不增加新 JS 庫（最多 30 行內聯 JS：~15 行 active nav 檢測 + ~10 行 mobile sidebar toggle 含 backdrop 關閉 + backdrop `overflow:hidden` 控制 ≈ 25 行，在 30 行上限內）

## Context & Research

### Relevant Code and Patterns

- `cpost/webui/static/app.css` — 現有設計令牌（CSS 變量）基礎良好，直接擴充
- `cpost/webui/templates/base.html` — 所有頁面的基礎骨架，sidebar 在此實作
- `cpost/webui/routers/_ctx.py:25` — `templates.env.globals["app_version"]` — 可在此注入全域 nav 數據（`nav_items` 列表），避免每個 router 傳參
- `cpost/webui/templates/_dashboard_stats.html` — HTMX 局部刷新：`hx-get="/_dashboard_stats"` 以 `outerHTML` swap，其 `<div class="stat-cards">` wrapper 必須保留
- `cpost/webui/templates/_packages_table.html` — HTMX target `#pkg-list`（innerHTML swap）
- `cpost/webui/templates/_scoop_list.html` — HTMX target `#scoop-list`（outerHTML swap）
- `cpost/webui/templates/_history_table.html` — HTMX target `#history-table`（innerHTML swap），每 5 秒自動刷新
- `cpost/webui/templates/_job_status.html` — HTMX target `#action-area`（innerHTML swap）
- `tests/test_webui_*.py` — 62 個測試文件，全部使用 `TestClient(create_app(...))` 注入配置。⚠️ 有兩組測試**會因模板結構改動而斷言失敗**，必須在對應 unit 合入前修正：
  1. `tests/test_webui_history.py` 第 69-78 行：以 `'<nav>' in full.text` 和 `'<nav>' not in frag.text` 區分完整頁面 vs. HTMX 局部響應 — Unit 2 把 `<nav>` 替換為 `<aside class="sidebar">` 後這兩條斷言會失敗
  2. `tests/test_webui_settings.py` 第 30, 54-55, 68, 82-84, 94 行：斷言完全匹配的 `<span class="pill ok">啟用</span>` 字符串 — Unit 6 若對 `_sources_list.html` 的 pill HTML 做任何結構改動（加 icon、改 class）這些斷言會失敗

### Existing Design Tokens

現有 `--radius: 10px`, `--shadow`, `--accent: #2563eb`, `--bg: #f7f8fa`, `--card: #ffffff` — 直接保留並擴充。

### Institutional Learnings

- 之前 `2026-06-15-004` 計劃完成了 HTMX 修復、CSS 從內聯移到外部文件，本計劃在其基礎上進行視覺升級
- 所有 HTMX swap 都使用 CSS class 選擇器（`#pkg-list`, `#scoop-list` 等），設計改動不能移除或重命名這些 ID

## Key Technical Decisions

- **側邊欄 vs. 頂部導航**：選擇側邊欄。理由：工具有 7 個一級頁面，側邊欄給持久空間上下文；頂部導航在窄寬度下排列擁擠，且缺乏 active 狀態表達能力。移動端 sidebar 默認隱藏（漢堡菜單切換）。
- **Active nav 狀態檢測**：使用 15 行內聯 JS（`window.location.pathname`）為匹配的 nav link 添加 `.active` class。根路徑 `/` 僅做精確匹配；非根路徑用 `startsWith` 處理子路由（如 `/packages/abc123` 匹配 `/packages`）。`href !== '/'` 的條件守衛確保根路徑不會錯誤匹配所有頁面（如果對兩邊都做 `.replace(/\/$/, '')` 正規化，`'/' → ''`，而 `''.startsWith('') === true` 恆成立，會使總覽 nav item 在所有頁面都顯示為 active）。理由：避免修改所有 router（每個都要傳 `page` 參數），且對 HTMX 局部刷新不敏感（sidebar 不會被 swap）。注意：`.active` class 由 client-side JS 注入，不出現在 server-rendered HTML 中，因此無法用 `TestClient` 斷言驗證。
- **暗色模式實作**：純 CSS `@media (prefers-color-scheme: dark)` 覆蓋 `--bg`, `--card`, `--ink` 等 main area surface 令牌。側邊欄保持常暗色（`--sidebar-bg: #1e293b`）在明暗兩種模式下不變，`@media (prefers-color-scheme: dark)` 不覆蓋 `--sidebar-bg`。理由：無 JS 閃爍、尊重 OS 偏好、零運行時開銷；常暗 sidebar 是工具型 WebUI 的常見設計選擇，與明暗主調切換不衝突。
- **CSS 組織結構**：保持單一 `app.css`，按區塊組織：① 設計令牌 → ② 重置 → ③ 應用骨架（app-shell/sidebar） → ④ 組件（button/badge/table/card/form） → ⑤ 頁面特定 → ⑥ 媒體查詢。
- **向下兼容**：不刪除任何現有 CSS class（`.stat-cards`, `.pill`, `.step`, `.batch-bar` 等），HTMX partials 的 HTML 結構只做視覺增強，不改變 class 名稱或 ID。
- **Typography**：system fonts 維持不變（已是最佳選擇），調整 spacing scale 和 font-size hierarchy。

## High-Level Technical Design

> *以下為設計方向指引，供審查確認，不是可複製的實作代碼。*

### App Shell 佈局結構

```
<body>
  <div class="app-shell">
    ┌─ sidebar ────────────────┐  ┌─ main ──────────────────────────┐
    │ .sidebar-brand           │  │ #toast-container                │
    │ ────────────────         │  │ .page-header > h1               │
    │ .sidebar-nav             │  │ .page-body                      │
    │  ├ .nav-item [.active]   │  │   {% block content %}           │
    │  ├ .nav-item             │  │                                 │
    │  └ ...                   │  │ footer                          │
    │ ────────────────         │  └─────────────────────────────────┘
    │ .sidebar-footer (ver.)   │
    └──────────────────────────┘

Mobile (< 768px):
  [☰ nav-toggle] → sidebar 以 .sidebar-open 滑入覆蓋
```

### CSS Layout 關鍵規則（方向性）

```
.app-shell { display: grid; grid-template-columns: var(--sidebar-w) 1fr; min-height: 100vh }
.sidebar   { width: var(--sidebar-w); background: var(--sidebar-bg); }
.main      { overflow-y: auto; padding: 2rem; max-width: none; }

/* Mobile override */
@media (max-width: 768px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { position: fixed; transform: translateX(-100%); }
  .sidebar.open { transform: none; }
}
```

### 新增設計令牌（擴充現有）

```css
/* 新增 —— sidebar */
--sidebar-w: 220px;
--sidebar-bg: #1e293b;    /* dark blue-gray */
--sidebar-fg: #cbd5e1;
--sidebar-active: #2563eb;

/* 新增 —— surface 層次 */
--surface-0: var(--bg);
--surface-1: var(--card);
--surface-2: #f0f2f5;

/* 新增 —— spacing scale */
--sp-1: 0.25rem; --sp-2: 0.5rem; --sp-3: 0.75rem;
--sp-4: 1rem;    --sp-6: 1.5rem; --sp-8: 2rem;
```

## Implementation Units

- [ ] **Unit 1: Design System CSS Overhaul**

**Goal:** 建立完整的 CSS 設計令牌系統 + App Shell 骨架 + 組件更新，同時向下兼容所有現有 class。

**Requirements:** R2, R3, R5, R8

**Dependencies:** 無（先做，後續 unit 依賴此 CSS）

**Files:**
- Modify: `cpost/webui/static/app.css`

**Approach:**
- **必須首先** 重置 `body`：`max-width: none; padding: 0; margin: 0;` — 現有 `body { max-width: 860px; margin: 0 auto; }` 會阻止 `.app-shell` Grid 全寬展開
- 在 `:root` 添加新令牌（sidebar, surface, spacing），不刪除現有令牌
- 添加 `.app-shell`, `.sidebar`, `.sidebar-brand`, `.sidebar-nav`, `.nav-item`, `.main`, `.page-header`, `.page-body` 的 CSS
- 重寫 `nav` 樣式（原有的保留備份，新 `.sidebar-nav` 是主樣式）
- 暗色模式：`@media (prefers-color-scheme: dark)` 在文件末尾，只覆蓋令牌值
- 刷新組件：`button`（更好的 hover 過渡）、`.pill`（更精緻）、`table`（更好的 row hover）、`.stat-card`（elevation 提升）
- mobile sidebar：`.sidebar { position: fixed; ... }` + `.sidebar.open` class；sidebar 開啟時對 `document.body` 同步加 `overflow: hidden`（關閉時移除），防止背景內容在 sidebar 後方捲動（iOS Safari / Android Chrome 行為）
- `.main` 在移動端不加 `margin-left`——sidebar 以 overlay 形式覆蓋，不推移 main 內容；`.main` 保持 `grid-column: 1 / -1` 佔滿單欄
- 保留所有現有 class（`.batch-bar`, `.step`, `.toast` 等）

**Test scenarios:**
- Happy path: CSS 文件可被瀏覽器正確解析（無語法錯誤）
- Edge case: `prefers-color-scheme: dark` 時 `--sidebar-bg` 被覆蓋
- Edge case: sidebar 在 max-width 768px 時 `position: fixed`
- Integration: 現有 `.pill.published`、`.pill.error` 等 class 組合仍有正確顏色
- Integration: `.stat-cards` wrapper 的 HTMX swap 後布局不崩潰

**Verification:**
- `ruff` / `mypy` 不受影響（純 CSS 改動）
- 瀏覽器開啟 webui，所有頁面可正常顯示，無 CSS 报错

---

- [ ] **Unit 2: Base Template — Sidebar Navigation**

**Goal:** `base.html` 改為 sidebar + main 佈局，加入 nav active 狀態 JS。

**Requirements:** R1, R6

**Dependencies:** Unit 1（依賴新 CSS class）

**Files:**
- Modify: `cpost/webui/templates/base.html`

**Approach:**
- 將 `<body>` 內容包進 `<div class="app-shell">`
- `<aside class="sidebar">` 內放品牌名稱、nav 鏈接（新增 `/scoops` 和 `/roster` 到 nav）、版本號
- `<div class="main">` 內放 `#toast-container`、`<div class="page-header"><h1>{% block heading %}{% endblock %}</h1></div>`、`{% block content %}`（保留現有 `{% block heading %}` block 名稱，子模板如 `scoops.html` 已使用此名稱；base.html 提供空預設值，h1 在無子模板覆寫時為空）
- Active 狀態 JS（15 行以內）：
  ```js
  document.querySelectorAll('.nav-item').forEach(a => {
    if (a.getAttribute('href') === window.location.pathname ||
        (a.getAttribute('href') !== '/' && window.location.pathname.startsWith(a.getAttribute('href')))) {
      a.classList.add('active');
    }
  });
  ```
- 移動端 hamburger toggle：`<button id="nav-toggle">☰</button>`，點擊切換 `.sidebar.open`；同時切換 `<div id="sidebar-backdrop">` 的 `display`（`none ↔ block`），backdrop CSS：`position:fixed; inset:0; background:rgba(0,0,0,.35); z-index: calc(var(--sidebar-z) - 1)`；點擊 backdrop 關閉 sidebar
- 保留現有 toast JS 邏輯不變
- `<footer>` 移至 `.sidebar-footer` 或 `.main` 底部

**Patterns to follow:**
- 現有 `templates.env.globals["app_version"]` 注入 — `{{ app_version }}` 在 sidebar footer 使用
- 現有 toast container JS 邏輯直接保留

**Tests that MUST be updated before this unit merges:**
- `tests/test_webui_history.py:69-78` — 把 `assert '<nav>' in full.text` 改為 `assert 'href="/packages"' in full.text`（測意圖不測 tag 名稱）；把 `assert '<nav>' not in frag.text` 改為 `assert 'href="/packages"' not in frag.text`

**Test scenarios:**
- Happy path: GET `/` 響應含 `href="/packages"`, `href="/scoops"`, `href="/roster"` — 驗證 nav links 已納入 sidebar
- Happy path: GET `/` 響應含 `id="nav-toggle"` — 驗證 mobile hamburger 存在於 HTML
- Edge case: `.active` class 由 client-side JS 注入，`response.text` 中不含此 class — 這是預期行為，不需斷言
- Edge case: `test_stylesheet_served_and_linked` 斷言 `'<style>' not in page.text` — Unit 2 的 sidebar toggle JS 必須用 `<script>` 而非 `<style>` 標籤，且不能加任何 inline `<style>` 塊
- Integration: HTMX 局部刷新（`/_dashboard_stats`）後 sidebar 不受影響（sidebar 不在 HTMX swap 範圍內）

**Verification:**
- `tests/test_webui_history.py` 更新後全部通過
- 訪問每個頁面，sidebar 正確顯示；瀏覽器切換頁面時對應 nav item 有活躍樣式

---

- [ ] **Unit 3: Dashboard Redesign**

**Goal:** Dashboard 頁面視覺升級：更豐富的 stat cards、更清晰的 recent runs 表格、突出的快速動作按鈕。

**Requirements:** R7

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `cpost/webui/templates/dashboard.html`
- Modify: `cpost/webui/templates/_dashboard_stats.html`

**Approach:**
- stat cards：加入顏色條（左側 border accent）和輔助描述文字，`stat-num` 字號提升
- 每個 stat card 加入 icon（純 Unicode/emoji，不引入圖標庫）
- crawl 按鈕改為更突出的 CTA 樣式（full-width 在特定寬度下）
- recent runs 表格：加入 row hover 高亮、時間列格式更清晰、status pill 更精緻
- HTMX 刷新邏輯保持不變：`hx-get="/_dashboard_stats" hx-trigger="every 10s" hx-swap="outerHTML"`
- `_dashboard_stats.html` 保留 `<div class="stat-cards">` 外層 wrapper（HTMX swap target）
- ⚠️ **outerHTML 自替換約束**：`_dashboard_stats.html` 的文件根元素（document root）必須攜帶 `hx-get`/`hx-trigger`/`hx-swap` 屬性，不可在其外再包一層 `<div>` 或 `<section>`。outerHTML swap 將整個元素替換為 partial 響應——若 partial 根元素不攜帶 HTMX 屬性，新元素插入後輪詢停止，且不報任何錯誤

**Test scenarios:**
- Happy path: dashboard 頁面含 `.stat-cards`, `.stat-num`, `.stat-label` — 現有測試繼續通過
- Happy path: recent_runs 為空時顯示提示文字
- **Must-add integration test**: `GET /_dashboard_stats` 的響應 body strip 後必須以 `<div class="stat-cards"` 開頭（直接加入 `test_webui_app.py::test_dashboard_stats_partial`）— 現有斷言只檢查 status_code 和文字內容，不驗證 wrapper 完整性，outerHTML swap 依賴此結構
- Edge case: `/_dashboard_stats` 不應被 base.html 包裹（不含 sidebar HTML）

**Verification:**
- `tests/test_webui_app.py::test_dashboard` 通過
- 視覺上 3 個 stat cards 清晰可辨，crawl 按鈕突出

---

- [ ] **Unit 4: Package List + Detail Page**

**Goal:** 套件清單表格現代化，詳情頁步驟條和信息表更精緻。

**Requirements:** R7, R8

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `cpost/webui/templates/packages.html`
- Modify: `cpost/webui/templates/detail.html`
- Modify: `cpost/webui/templates/_packages_table.html`
- Modify: `cpost/webui/templates/_job_status.html` — 確認視覺相容性（此 partial inject 進 `#action-area`，detail.html 改版後需確認 injection 後排版無崩潰）

**Approach:**
- `_packages_table.html`：
  - status pill 列改為更寬的 badge 帶 icon（✓ 已驗證 / ✗ 錯誤 等）
  - 保留所有 HTMX target ID：`#pkg-list`、`#row-action-{post_id}`、`#batch-result`
  - batch 操作欄樣式改善（`.batch-bar` 更緊湊）
- `packages.html`：
  - toolbar 搜索框 + select 排列更整齊
  - 提示文字位置調整
- `detail.html`：
  - step-bar 改為視覺更豐富的進度指示（帶 ✓ 已完成 / → 進行中 / ○ 待辦）
  - **status → step 映射表**：
    | Step | 標籤 | package_status 值 |
    |------|------|------------------|
    | 1 | Crawled | `package_built` |
    | 2 | Drafted | `drafted` |
    | 3 | Verified | `draft_verified` |
    | 4 | Published | `published` |
    `broken` 狀態：step 1 顯示為 error 變體（紅色），其餘 step 維持 ○ pending；不進入 done/current 狀態
    CSS class 規範：`.step.done`（✓）、`.step.current`（→）、`.step.pending`（○）、`.step.error`（✗，新增）
  - 信息表（post_id, 狀態, 來源 ID, 來源）改為更清晰的鍵值對卡片
  - failure-box 設計更突出（左紅色 border）
  - published 成功框設計更清晰
  - 保留所有 HTMX target：`#action-area`、`#generate-area`

**Test scenarios:**
- Happy path: `/packages` 頁面含表格和 batch 工具列，測試通過
- Happy path: `/packages/{id}` 詳情頁含步驟條、動作按鈕
- Edge case: status=`published` 時不顯示草稿/驗證按鈕，只顯示回退按鈕
- Edge case: `broken` 包不顯示 checkbox 和 action 按鈕
- Integration: batch draft/verify HTMX POST 後 `#batch-result` 被更新，checkbox 被清空

**Verification:**
- `tests/test_webui_packages.py`, `tests/test_webui_actions.py`, `tests/test_webui_batch.py` 全部通過
- 詳情頁步驟條在 4 個狀態（package_built/drafted/draft_verified/published）下視覺正確

---

- [ ] **Unit 5: Today Page + Scoops**

**Goal:** 今日備稿頁工作流更清晰，瓜清單的選擇體驗改善。

**Requirements:** R7

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `cpost/webui/templates/today.html`
- Modify: `cpost/webui/templates/scoops.html`
- Modify: `cpost/webui/templates/_scoop_list.html`

**Approach:**
- `today.html`：
  - prep 區塊（開始備稿按鈕）改為卡片式，視覺更突出
  - filter 表單（min_confidence, min_score）改為行內並排，標籤更清晰
  - 保留 HTMX target：`#prep-status`、`#scoop-list`
- `_scoop_list.html`：
  - 多站（🔥）badge 改為使用統一的 `.pill` 系統
  - checkbox 選擇列更寬（方便點擊）
  - 分數列使用顏色區分高/中/低（CSS class 根據分值範圍）：`.score-high`（≥ 0.7）、`.score-mid`（0.4–0.69）、`.score-low`（< 0.4）——對應 scoring.yaml 的 0–1 分值範圍
  - 保留 HTMX target：`#gen-status`，form action `/today/generate`
- `scoops.html`：
  - 篩選按鈕（只看多站/顯示全部）改為 segmented control 樣式
  - tier badge 改用統一 `.pill` class

**Test scenarios:**
- Happy path: `/today` 頁面含 prep form、filter form、scoop list
- Happy path: `/scoops` 頁面含篩選控件和表格
- Edge case: `rows` 為空時顯示空狀態提示
- Integration: filter 變更後 HTMX GET `/today/list` 刷新 `#scoop-list`

**Verification:**
- `tests/test_webui_crawl.py`, `tests/test_webui_scoops.py` 通過
- 瓜清單多站 badge 顯示正確，生成按鈕可操作

---

- [ ] **Unit 6a: Settings + Sources Polish**（高測試風險，先做）

**Goal:** 將設計系統應用到設定頁和來源列表，同步更新已知會斷言失敗的測試。

**Requirements:** R7, R8

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `cpost/webui/templates/settings.html`
- Modify: `cpost/webui/templates/_sources_list.html`
- Modify: `cpost/webui/templates/_auth_status.html`
- Modify: `tests/test_webui_settings.py`（必須同 PR）

**Approach:**
- `settings.html`：
  - 表單改為分 section 卡片（基本爬取設定 / 自動化 / 診斷）
  - auto_pipeline warning box 設計更醒目（紅色左 border）
  - 診斷表格改為使用 `.diag-table` 統一樣式
  - 保留 HTMX POST `/settings` 和 `/crawl` 互動
- `_sources_list.html`：多來源列表改善，enabled/disabled toggle 視覺更清晰
- `_auth_status.html`：auth state 顯示改為 badge 樣式

**Tests that MUST be updated before this unit merges:**
- `tests/test_webui_settings.py:30, 54-55, 68, 82-84, 94` — 7 條斷言完全匹配 `<span class="pill ok">啟用</span>` 等 exact HTML 字符串。應拆分為：(a) 斷言 source_id 文字出現在響應中；(b) 斷言 `'啟用' in r.text` 和 `'停用' in r.text`，不鎖定 span 結構。

**Test scenarios:**
- Happy path: settings 頁正常渲染，各 section 卡片存在
- Edge case: auto_pipeline=true 時 warning box 顯示
- Integration: settings POST 後 form 以 `outerHTML` swap 顯示 `<p class="ok">已儲存 ✓</p>`
- Integration: sources list 顯示正確的啟用/停用狀態文字

**Verification:**
- `tests/test_webui_settings.py` 更新後全部通過
- 視覺：settings 頁分 section 顯示，sources pill 樣式正確

---

- [ ] **Unit 6b: Remaining Pages Polish**（純視覺，可獨立先 merge）

**Goal:** 將設計系統一致應用到名冊、歷史、Audit、垃圾桶頁面，無測試斷言需改寫。

**Requirements:** R7, R8

**Dependencies:** Unit 1, Unit 2（可與 6a 並行，不依賴 6a）

**Files:**
- Modify: `cpost/webui/templates/roster.html`
- Modify: `cpost/webui/templates/history.html`
- Modify: `cpost/webui/templates/audit.html`
- Modify: `cpost/webui/templates/trash.html`
- Modify: `cpost/webui/templates/_history_table.html`
- Modify: `cpost/webui/templates/_audit_table.html`
- Modify: `cpost/webui/templates/_trash_table.html`

**Approach:**
- `roster.html`：
  - tier badge 改用 `.pill.{tier}` class（在 Unit 1 CSS 中新增 tier pill 變體）
  - 表格改善密度
- `history.html` + `_history_table.html`：
  - toolbar filter 排列優化（filter 欄位改為行內並排）
  - 每列 severity 顏色區分（info=muted, warning=pending, error=error）
  - 保留 5 秒 HTMX auto-refresh 邏輯
  - ⚠️ **約束**：`id="history-table"` 必須留在 `history.html` 的 host page element 上，禁止移入 `_history_table.html` partial 內；toolbar filter inputs 必須保持在 `<form>` 元素以外（hx-include 使用 `[name=...]` CSS 選擇器，form 包裹會改變 HTMX include 行為）
- `audit.html` + `_audit_table.html`：改善表格密度，severity 顏色區分
- `trash.html` + `_trash_table.html`：恢復按鈕更突出

**Test scenarios:**
- Happy path: 所有頁面（roster/history/audit/trash）正常渲染
- Edge case: history 過濾無結果時顯示空狀態
- Integration: trash 恢復按鈕 HTMX POST 後列表刷新

**Verification:**
- `tests/test_webui_history.py`, `tests/test_webui_roster.py` 全部通過（無需改測試）
- 所有頁面無 CSS 錯誤，視覺風格統一

---

## System-Wide Impact

- **HTMX targets:** 所有 HTMX swap target ID（`#pkg-list`, `#scoop-list`, `#action-area`, `#batch-result`, `#history-table`, `#gen-status`, `#prep-status`）不可改動。HTML 結構改變僅限 class 名稱和新增標籤，不移除或重命名 ID。
- **CSS class 合約:** `.stat-cards`, `.pill.{status}`, `.step.done/.current`, `.batch-bar`, `.toast.ok/.err`, `.htmx-indicator`, `.htmx-request` 等現有 class 在 Unit 1 中必須保留，不得刪除。
- **HTMX partials 獨立性:** `_dashboard_stats.html`, `_packages_table.html`, `_scoop_list.html`, `_history_table.html` 等局部模板可能不繼承 `base.html`，它們的 HTML 結構改動必須確保在 swap 後 parent 布局不崩潰。
- **Tests:** 62 個 `test_webui_*.py` 測試使用 `httpx.TestClient` 測試響應 HTML（用 `assert 'text' in response.text` 等），對 CSS class 不測試，只測試功能性 HTML 元素和文字。模板改動應保留所有被測試的文字、form 元素、鏈接。
- **Unchanged invariants:** 路由 URL 不變；`create_app(config_path)` 工廠函數不變；所有 router handler 的 template context 變量不變（`packages`, `cfg`, `recent_runs` 等）。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **[HIGH]** `body { max-width: 860px }` 阻止 sidebar Grid 全寬展開 | Unit 1 必須首先重置 `body` 為 `max-width: none; padding: 0; margin: 0`（見 Unit 1 Approach 第一條）|
| **[HIGH]** `test_webui_history.py:69-78` 以 `<nav>` 斷言頁面完整性，Unit 2 後會失敗 | Unit 2 合入前必須更新測試：用 `href="/packages"` 替代 `<nav>` 作為存在性判斷 |
| **[HIGH]** `test_webui_settings.py` 7 條 exact pill HTML 斷言，Unit 6 修改後會失敗 | Unit 6 合入前必須鬆綁斷言：改為 `'啟用' in r.text` 而非鎖定 `<span class="pill ok">` 結構 |
| **[MEDIUM]** `/_dashboard_stats` outerHTML swap 依賴 `<div class="stat-cards">` 為根元素，現有測試不驗證此約束 | Unit 3 加入 wrapper 完整性斷言（`r.text.strip().startswith('<div class="stat-cards"')`）|
| **[MEDIUM]** `test_webui_app.py::test_base_references_htmx` 斷言精確字符串 `<script src="/static/htmx.min.js">` | Unit 2 不可對此 script 標籤加 `defer`/`async` 屬性，否則測試失敗 |
| **[MEDIUM]** `#history-table` ID 必須留在 `history.html` host page 的元素上，不可移入 `_history_table.html` partial | `_history_table.html` 以 innerHTML swap 更新 `#history-table` 的內容；若 ID 移入 partial，outerHTML swap 後找不到 target 導致 5 秒 auto-refresh 靜默失效（Unit 6 的 toolbar 排列優化不可移動此 ID）|
| sidebar 寬度佔去太多 main area 空間，表格/表單顯示擁擠 | `--sidebar-w: 220px`；main area 設 `max-width: none`；表格保持橫向滾動 `.table-wrap` |
| 移動端 sidebar 遮擋內容 | sidebar 在移動端 `position: fixed`，overlay 後有 backdrop，點擊 backdrop 關閉 |
| `_job_status.html` 注入 `#action-area` 後視覺不合 | Unit 4 包含 `_job_status.html` 視覺相容性確認（已列入 Files）|

## Documentation / Operational Notes

- 改動純為視覺層，無需更新 CLI 文檔或部署說明
- `啟動本地服務.command` 和 `crawl-post-webui` 命令入口不受影響
- 完成後可在本地啟動 webui 截圖確認各頁面效果

## Sources & References

- Related code: `cpost/webui/static/app.css`, `cpost/webui/templates/base.html`
- Related code: `cpost/webui/routers/_ctx.py:25` (templates globals)
- Prior plan: `docs/plans/2026-06-15-004-feat-webui-comprehensive-optimization-plan.md` (已完成的 HTMX 修復和初始 CSS 建立)
- Prior plan: `docs/plans/2026-06-16-003-refactor-webui-app-router-split-plan.md` (router 拆分，建立現有 routers/ 結構)
