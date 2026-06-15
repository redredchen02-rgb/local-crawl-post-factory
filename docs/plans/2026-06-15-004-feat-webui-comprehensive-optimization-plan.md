---
title: "feat: WEBUI 全面优化（修复 + 视觉 + 功能 + 可维护性）"
type: feat
status: completed
date: 2026-06-15
---

# feat: WEBUI 全面优化

## Overview

`webui/`（FastAPI + HTMX，localhost-only）是「爬取 → 建包 → 审核 → 发布」工作台。
本计划在**不改变其安全边界**（localhost 单人、发布仍走 R6 三重闸门）的前提下，分四个维度优化：

1. **修复功能** — 当前 `webui/static/htmx.min.js` 为 0 字节，导致所有 `hx-*` 交互（登入态轮询、设定表单、爬取建包、任务进度轮询、建草稿/验证/发布）实际失效。这是最高优先级。
2. **视觉 / UX 重设计** — 将内联 `<style>` 抽出为独立样式表，改进排版、配色、层次、空状态与移动端体验。
3. **新增功能** — 上膛清单的搜索/筛选、删除包、实时日志/审计自动刷新等贴近单人本地工作流的能力。
4. **代码质量** — 拆分 310 行的 `app.py`、模板片段复用、前端资源管理、补齐测试。

## Problem Frame

无上游 requirements 文档，需求来自用户直述「帮我全面优化我的 WEBUI」。
经澄清：四个维度全要，场景为**仅本地单人使用**——因此可明确排除鉴权、多用户、对外部署等复杂度。

根因发现：`base.html` 用 `<script src="/static/htmx.min.js">` 加载 HTMX，但该文件 0 字节，整个动态 UI 处于半瘫痪状态。所有「优化」都应建立在先恢复可用之上。

## Requirements Trace

- R1. 恢复所有 HTMX 驱动的交互，使界面真正可用（修复维度）
- R2. 提升视觉质量与易用性，去除简陋内联样式，支持移动端（视觉/UX 维度）
- R3. 增加贴合单人本地工作流的实用功能（新功能维度）
- R4. 降低 `app.py` 复杂度、提升模板复用与测试覆盖（可维护性维度）
- R5. 全程保持现有 8 个 `tests/test_webui_*.py` 绿，且不破坏 R6 发布三重闸门与路径穿越防护

## Scope Boundaries

- **非目标**：鉴权 / 登录系统、多用户、生产部署、HTTPS、对外暴露（场景为 localhost 单人）
- **非目标**：改动 `core/`、`src/`、`browser/` 的业务逻辑（仅 `webui/` 层及其测试）
- **非目标**：引入前端构建工具链（Webpack/Vite/npm）——保持「零构建、静态 vendored 资源」的现有哲学
- **非目标**：改变发布安全模型（R6 三重闸门、`_safe_pkg_dir` 路径校验保持不变）
- **非目标**：替换 HTMX 为 SPA 框架

## Context & Research

### Relevant Code and Patterns

- `webui/app.py:26` `create_app(config_path)` 工厂模式 — 测试以 `TestClient(create_app(tmp_cfg))` 注入配置，新代码必须维持此可注入性
- `webui/app.py:271` `_safe_pkg_dir` — 路径穿越防护，删除等新功能必须复用，不可绕过
- `webui/app.py:186` `action_publish` — R6 三重闸门（已审核 / `draft_verified` / 标题匹配），不可削弱
- `webui/templates/base.html` — 内联 `<style>` 与 `<script src="/static/htmx.min.js">`，是视觉与修复的共同入口
- `core/jobs.py` — 进程内线程任务注册表（单人本地，无需 Celery/Redis）；进度轮询依赖 `_job_status.html` 的 `hx-trigger="every 1s"`
- `core/runs.py` / `core/webui_config.py` — history 与配置读写，新功能（筛选/删除后刷新）在其上读

### Institutional Learnings

- `docs/plans/2026-06-15-002-feat-webui-settings-crawl-stage-plan.md` — webui 初始设计与安全约束来源，复审以免回退既有决策
- 既有注释明示设计哲学：单人本地、零外部依赖、发布永远人工 —— 优化须沿此哲学

### External References

- 不需要外部研究：HTMX 用法在仓库内已有完整模式（`hx-post`/`hx-get`/`hx-trigger`/`hx-swap`/`hx-target`），本地模式充分。

## Key Technical Decisions

- **HTMX 以真实文件 vendored 到 `webui/static/`**：与现有「零构建、静态托管」一致，离线可用。固定版本号注释便于追踪。理由：避免引入 CDN 外网依赖与构建工具。
- **CSS 抽到 `webui/static/app.css`**：消除每页内联样式重复，集中改进视觉，由 `StaticFiles` 既有挂载托管。
- **`app.py` 不引入大型分层（routers/services 拆包）**：仅做函数级抽取与模板片段复用。理由：515 行总量、单人工具，过度分层违反 YAGNI（见 Scope）。
- **新功能严格挑选「单人本地高频」项**：搜索/筛选、删除包、日志自动刷新；不做批量发布等会触碰 R6 安全面的功能。

## Open Questions

### Resolved During Planning

- 优化范围？→ 四维度全做，但受 localhost 单人场景约束（已与用户确认）
- 是否引入前端框架/构建？→ 否，保持零构建静态资源
- HTMX 用 CDN 还是 vendored？→ vendored，沿用现有静态托管

### Deferred to Implementation

- HTMX 具体 pin 版本号 → 实现时取最新稳定 1.x 并在文件头注释
- 删除包是「移到 trash 子目录」还是「真删」 → 实现时决定，倾向移到 `out/.trash/` 以可逆（符合不可逆操作谨慎原则）
- `app.css` 的具体设计语言细节 → 实现时迭代，本计划只定方向（系统字体、克制配色、清晰层次、移动端）

## Implementation Units

- [ ] **Unit 1: 修复 HTMX 加载，恢复全部动态交互**

**Goal:** 补回真实 `htmx.min.js`，使登入态轮询、设定表单、爬取、任务轮询、建草稿/验证/发布按钮全部恢复工作。

**Requirements:** R1, R5

**Dependencies:** 无（最高优先，其余单元的可视验证都依赖它）

**Files:**
- Modify/Replace: `webui/static/htmx.min.js`（当前 0 字节 → 真实 vendored 文件，文件头注释版本号）
- Modify: `webui/templates/base.html`（确认 `<script>` 引用路径正确；如有需要加 `defer`）
- Test: `tests/test_webui_app.py`（新增静态资源可达性断言）

**Approach:**
- 取最新稳定 HTMX 1.x 压缩版写入 static 文件
- 验证 `GET /static/htmx.min.js` 返回非空 200、`content-type` 为 JS
- 不改任何 `hx-*` 属性（属性本身正确，只是脚本缺失）

**Patterns to follow:** `app.mount("/static", StaticFiles(...))`（`app.py:28`）已存在

**Test scenarios:**
- Happy path：`GET /static/htmx.min.js` → 200 且 body 非空、包含 `htmx` 标识
- Happy path：`GET /settings` 返回的 HTML 中 `<script src="/static/htmx.min.js">` 存在
- Edge case：文件存在但若被误清空 → 测试对长度 > 0 断言可捕获回退

**Verification:** 启动 `crawl-post-webui` 后，浏览器点「爬取建包」「建草稿」按钮有反馈，登入态每 10s 刷新；8 个既有测试全绿。

- [ ] **Unit 2: 视觉 / UX 重设计（抽出样式表 + 改进界面）**

**Goal:** 将内联 `<style>` 抽为 `webui/static/app.css`，改进排版、配色、层次、空状态、错误提示与移动端体验。

**Requirements:** R2, R5

**Dependencies:** Unit 1（需 HTMX 正常以验证交互态视觉）

**Files:**
- Create: `webui/static/app.css`
- Modify: `webui/templates/base.html`（移除内联 `<style>`，改 `<link rel="stylesheet" href="/static/app.css">`；导航布局优化）
- Modify: `webui/templates/settings.html` / `detail.html` / `packages.html` / `history.html` / `audit.html` / `_job_status.html`（必要的 class 标注）
- Test: `tests/test_webui_app.py`（断言 `app.css` 可达且 base 引用它）

**Approach:**
- 设计语言：系统字体、克制配色（一个主色 + 中性灰）、清晰标题层次、卡片/表格留白、状态色（ok/error/pending）统一
- 移动端：导航换行友好、表格在窄屏可横向滚动、按钮触摸区域足够
- 保持语义化与可读性，不引入 UI 框架（无 Bootstrap/Tailwind 构建）

**Patterns to follow:** 现有 `.error` / `.ok` / `.hint` class 命名延续

**Test scenarios:**
- Happy path：`GET /static/app.css` → 200 非空
- Happy path：`GET /settings` HTML 含 `<link ... app.css>` 且不再含大段内联 `<style>` 规则
- Test expectation：视觉效果本身靠人工/截图验证，自动测试只覆盖资源接线

**Verification:** 各页面在桌面与窄屏下排版正常、层次清晰；既有测试全绿。

- [ ] **Unit 3: 新增实用功能（搜索/筛选 + 删除包 + 日志自动刷新）**

**Goal:** 为单人本地工作流补三个高频能力：上膛清单按状态/关键词筛选、删除（归档）贴文包、audit/history 自动刷新。

**Requirements:** R3, R5

**Dependencies:** Unit 1（新增交互依赖 HTMX）

**Files:**
- Modify: `webui/app.py`（新增 `GET /packages?q=&status=` 筛选参数；新增 `POST /packages/{post_id}/delete`，复用 `_safe_pkg_dir`，移动到 `out/.trash/`；audit/history 片段化以支持 `hx-trigger`）
- Modify: `webui/templates/packages.html`（搜索框 + 状态下拉，`hx-get` 实时筛选；每行删除按钮带确认）
- Modify: `webui/templates/audit.html` / `history.html`（可选自动刷新）
- Create: `webui/templates/_packages_table.html`（抽出表格片段，供整页与 HTMX 局部刷新复用）
- Test: `tests/test_webui_packages.py`（筛选与删除）

**Approach:**
- 筛选在 `_scan_packages` 结果上做内存过滤（数据量小，单人本地）
- 删除为**可逆**操作：移动到 `out/.trash/<post_id>/` 而非真删（符合谨慎原则）；删除前必须通过 `_safe_pkg_dir` 校验
- 删除不触碰已发布状态的安全语义；仅操作文件系统包目录

**Execution note:** 删除是有副作用操作，先写失败测试覆盖「路径穿越被拒」「删除后清单不再出现」再实现。

**Patterns to follow:** `_safe_pkg_dir`（`app.py:271`）、`_scan_packages`（`app.py:282`）、`_job_status.html` 的 `hx-trigger` 轮询

**Test scenarios:**
- Happy path：`GET /packages?status=draft_verified` 只返回该状态行
- Happy path：`GET /packages?q=<title 片段>` 命中标题/post_id
- Happy path：`POST /packages/{id}/delete` → 包移入 `out/.trash/`，清单不再列出
- Edge case：`q` 为空 → 返回全部
- Error path：`POST /packages/../etc/delete` 等穿越尝试 → 404，文件系统未被改动
- Integration：删除后 `GET /packages` 反映最新列表（读路径与写路径一致）

**Verification:** 筛选即时生效；删除后包进入 trash 且清单更新；穿越攻击被拒；既有测试全绿。

- [ ] **Unit 4: 代码质量 / 可维护性**

**Goal:** 降低 `app.py` 复杂度、消除模板重复、整理前端资源引用，补齐回归测试。

**Requirements:** R4, R5

**Dependencies:** Unit 1–3（在功能稳定后收尾重构，避免边改边重构）

**Files:**
- Modify: `webui/app.py`（将路由处理体中重复的 `cfg = webui_config.load(...)` 与 `_safe_pkg_dir` 取包逻辑收敛为小辅助函数；保持 `create_app` 工厂可注入性不变）
- Modify: 模板（确保 `_packages_table.html` 等片段被整页与局部刷新共用，消除重复）
- Test: `tests/test_webui_app.py` 等（补充资源接线与重构后回归断言）

**Approach:**
- 仅做函数级抽取，不拆分多文件 router/service（YAGNI，见 Key Decisions）
- 重构以「行为不变 + 测试绿」为准绳，逐函数小步替换
- 不改动路由 URL 与响应契约（避免破坏既有测试与浏览器书签）

**Execution note:** 纯重构，以既有测试为安全网，先确认全绿再动。

**Patterns to follow:** 现有工厂 + 闭包路由风格；`create_app` 内嵌路由的既有结构

**Test scenarios:**
- Happy path：重构后 8 个既有 `test_webui_*` 全部仍绿
- Edge case：`create_app(custom_cfg)` 仍可注入临时配置（测试已依赖此点）
- Test expectation：本单元为内部重构，无新对外行为，断言聚焦「行为不变」

**Verification:** `app.py` 行数与重复下降、模板无重复表格；`pytest tests/test_webui_*.py` 全绿。

## System-Wide Impact

- **Interaction graph:** 仅影响 `webui/` 层；`core/`、`src/`、`browser/` 不变。删除功能新增对 `out/` 文件系统的写（移动到 `.trash/`）。
- **Error propagation:** 沿用现有「返回 HTMLResponse + 状态码」模式；任务错误经 `jobs` 捕获，不崩服务。
- **State lifecycle risks:** 删除采用移动到 `.trash/` 而非真删，可逆；不触碰 `published.sqlite` 与已发布去重真相。
- **API surface parity:** 新增路由（筛选参数、delete）需同时有页面入口与（本地工具，无独立 agent 接口）。
- **Unchanged invariants:** R6 发布三重闸门、`_safe_pkg_dir` 路径校验、localhost 绑定、`create_app` 工厂可注入性——均显式保持不变。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| vendored htmx 版本与现有 `hx-*` 属性不兼容 | 选稳定 1.x；既有属性均为基础用法，兼容性风险低；以浏览器点测验证 |
| 删除功能引入路径穿越或误删 | 复用 `_safe_pkg_dir`；改为移动到 `.trash/`（可逆）；先写穿越拒绝测试 |
| 重构破坏既有 8 个测试 | 以测试为安全网，小步抽取，每步跑 `pytest tests/test_webui_*.py` |
| 视觉重构遗漏某页 class | 样式表集中 + 逐页人工/截图核对 |

## Documentation / Operational Notes

- 启动方式不变：`crawl-post-webui`（`webui.app:run`，绑定 `127.0.0.1:8000`）
- 如需记录新增的 `.trash/` 行为，更新 `README.md` 的 webui 段落（可选）

## Sources & References

- 现有 webui 计划：`docs/plans/2026-06-15-002-feat-webui-settings-crawl-stage-plan.md`
- 核心代码：`webui/app.py`、`webui/templates/*`、`core/jobs.py`、`core/runs.py`、`core/webui_config.py`
- 既有测试：`tests/test_webui_*.py`（8 个）
