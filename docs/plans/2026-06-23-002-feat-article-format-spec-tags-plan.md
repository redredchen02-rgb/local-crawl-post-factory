---
title: "feat: Upgrade scoop generation to full article format spec (七+八) with tags output"
type: feat
status: active
date: 2026-06-23
deepened: 2026-06-23
---

# feat: Upgrade scoop generation to full article format spec (七+八) with tags output

## Overview

`generate-article` 目前使用 `scoop_prompt.zh.md`（多源合成 prompt），只輸出標題 + 正文。本計畫將 prompt 升級為完整的**七、标题与正文撰写规范 + 八、标签与关键词规范**，同時：

1. prompt 結尾新增結構化 `标签：` footer，供 parser 提取 tags
2. `generate_article.py` 解析 tags 並填入 `PackageInput.tags`
3. `library.py` 為 `generations` 表加 `tags` 欄位（ALTER migration）並更新 put/get API

**觸發點**：爬取後確認的 scoop cluster → `generate-article` → `PackageInput` 含完整文章格式 + tags → 後續 `build-manifest` / `publish` 不動。

## Problem Frame

現有 `scoop_prompt.zh.md` 沒有完整文章規範，`PackageInput.tags` 欄位存在但 scoop track 從不填寫。

**本計畫解決的具體問題：**
- `PackageInput.tags` 永遠為空 → 下游 manifest / SEO 無標籤
- prompt 缺乏結構規範 → LLM 生成格式不一致

**本計畫不保證（best-effort via prompt）：**
- 分節結構（开头简介、快速看懂、事件经过、FAQ、结尾总结）由 prompt 指引 LLM，不做輸出 validation
- 圖片/視頻佔位符（`[IMAGE_1]`、`[VIDEO_1]`）同上，LLM 未輸出時不報錯
- 排版去重同上

## Requirements Trace

- R1. 新 `scoop_prompt.zh.md` 包含七+八完整規範，輸出末尾帶 `标签：` footer（機器可解析）
- R2. `parse_article()` 從 LLM 輸出提取 title / body / tags 三元組；tags 缺失時回退 `[]`
- R3. `generate()` 把 tags 填入 `PackageInput.tags` 並寫入 generations cache
- R4. `library.put_generation()` / `get_generation()` 支援 tags 欄位（migration 保護既有 DB）
- R5. `_PROMPT_VERSION = "scoop-v2"` 使既有 cache 失效，強制用新 prompt 重生
- R6. 現有測試全綠；新增 tag 解析、缺標籤降級、cache round-trip 測試

## Scope Boundaries

- **不改** `build_manifest.py`（已讀 `PackageInput.tags` 並寫入 `manifest.content.tags`）
- **不改** `article_prompt.zh.md`（單篇生成 prompt，另一條 track，本次不動）
- **不改** `publish_post.py` / browser driver
- 圖片/視頻佔位符由 LLM 自行插入正文 body；本 plan 不做媒體替換（留佔位符即可）
- `关键词` 不新增獨立欄位，只把 `标签` 值存入 `PackageInput.tags`

## Context & Research

### Relevant Code and Patterns

- `cpost/cli/generate_article.py` — `split_title_body()` 現在 parser，`generate()` 組裝 `PackageInput`
- `cpost/core/library.py` — `_SCHEMA` / `put_generation` / `get_generation`；`_MIGRATIONS` pattern 見 `runs.py`
- `cpost/core/db.py` — `connect(path, schema, migrations, extra)` 接受 `migrations=[(version, ddl)]`；`_apply_statement` 吃掉 `duplicate column name` 故 idempotent
- `configs/scoop_prompt.zh.md` — 當前多源 prompt（待替換）
- `configs/article_prompt.zh.md` — 單篇 prompt，排版風格參考來源
- `cpost/core/schema.py:154` — `PackageInput.tags: list[str]`（已存在，只是 scoop track 從不填）

### Institutional Learnings

- Cache key 是 `hash(_PROMPT_VERSION + system_prompt_content + model + build_material(members))`——版本 bump **或** prompt 檔案內容改變都會各自獨立使 cache 失效。本計畫兩者都改，版本 bump 是主動失效訊號，prompt 內容改變是附帶的。
- `generations` 表的 `tags` 欄位需 migration（`ALTER TABLE ADD COLUMN`）；新 DB 直接在 `_SCHEMA` 加欄位；既有 DB 走 migration，`duplicate column name` 被 `_apply_statement` 靜默忽略

## Key Technical Decisions

- **Parser 策略（複合錨定）**：用 `re.split(r'\n-{3,}\n', article)` 分割（不用字面量 `\n---\n`，容錯 CRLF、LLM 輸出少前導換行、多破折號等變體）；從後往前掃，找第一個其後緊接的第一個非空行 match `^标签[:：]\s*.+`（相容全形/半形冒號、冒號後空白）的分割點，才視為 tags footer 並切割。若找不到符合條件的分割點（正文中的 Markdown 橫線、無 tags 輸出），body 不截斷，tags 回傳 `[]`。好處：即使 FAQ 節或 section 分隔有 `---`，因其後不是 `标签[：:]`，parser 繼續往後找，最終找到真正的 footer 或放棄，不靜默損壞 body。
- **tags 序列化**：`generations.tags` 用 JSON 字串（`json.dumps(["a","b"])`）存入 TEXT 欄位，避免 SQLite 沒有陣列型別。`get_generation()` 用 `json.loads()` 反序列化；舊 row `tags=NULL` → 回傳 `[]`。
- **`关键词` 不拆欄位**：`PackageInput` 只有 `tags`，將 `标签` 值（3-5 個）存入 tags；`关键词` 包含在正文 SEO 語境，不額外儲存。
- **`_PROMPT_VERSION = "scoop-v2"`**：版本字串包含在 `cache_key` 的 hash 輸入，bump 後所有既有 cache miss，強制重生。

## Open Questions

### Resolved During Planning

- **`article_prompt.zh.md` 要不要一起改？** → 否。單篇 track 由 WebUI `POST /packages/{id}/generate` 使用，本次不動，避免雙軌同時變動。
- **tags 要不要包含 `关键词`？** → 否。只存 `标签`（3-5 詞）；`关键词` 仍可寫在 prompt 規範裡供 SEO 參考，但不進 schema。
- **既有 `split_title_body()` 要不要刪？** → 不刪，改名為 `_split_title_body()` 內部呼叫，`parse_article()` 是新公開 API。向後兼容測試不破。

### Deferred to Implementation

- 圖片/視頻佔位符的替換邏輯（`[IMAGE_1]` → 真實圖片路徑）屬另一 stage，本計畫不處理
- `article_prompt.zh.md` 的同步升級（單篇 track）時機由後續計畫決定

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
LLM output (scoop-v2 prompt)
┌────────────────────────────────────────────────┐
│ 人物A平台X事件Y核心看点Z                         │  ← title (第1行)
│                                                 │
│ 开头简介 80-120字…                              │
│                                                 │
│ [IMAGE_1]                                       │  ← 圖片佔位符
│                                                 │
│ 一分钟快速看懂                                  │
│ - 人物/主体：…                                  │
│ …                                              │
│                                                 │
│ ## 事件经过                                     │
│ 100-200字…                                      │
│                                                 │
│ ## FAQ                                          │
│ Q: …  A: …                                     │
│                                                 │
│ 结尾总结 80字…                                  │
│                                                 │
│ ---                                             │  ← 分隔符
│ 标签：人物A, 平台X, 内容类型Y                    │  ← tags (機器解析)
└────────────────────────────────────────────────┘
         │
         ▼ parse_article()
  (title, body, tags=["人物A","平台X","内容类型Y"])
         │
         ▼ generate()
  PackageInput { title, caption=body, tags=[...] }
         │
         ▼ library.put_generation(tags=json.dumps(...))
  generations table (cache_key, title, body, tags TEXT)
```

## Implementation Units

- [ ] **Unit 1: 升級 `configs/scoop_prompt.zh.md`（七+八完整規範 + tags footer）**

**Goal:** 替換現有 prompt 為完整七+八規範，在末尾加機器可解析的 `标签：` footer。

**Requirements:** R1

**Dependencies:** 無

**Files:**
- Modify: `configs/scoop_prompt.zh.md`

**Approach:**
- 保留現有「多源合成原則」（不復制單一來源、只用素材、標注分歧）作為 Section 一
- 加入七完整排版順序：標題 → 开头简介 → `[IMAGE_1]` 佔位符 → 一分钟快速看懂 → 事件经过 → 圖片展示（`[IMAGE_N]`）→ 視頻（`[VIDEO_N]`）→ FAQ → 結尾總結
- 加入八標籤規範（3-5 個客觀存在的標籤，禁止行銷詞）
- 輸出格式要求最後一節為：`---\n标签：tag1, tag2, tag3`（純文字，逗號分隔）
- 說明圖片/視頻節：素材無圖時省略圖片節；無視頻時省略視頻節
- 防重複要求：每篇小標題需根據素材重新撰寫

**Test scenarios:**
- Test expectation: none — prompt 是 config 文字，無可自動化測試；由 Unit 3 的 parse_article 測試覆蓋輸出格式

**Verification:**
- `configs/scoop_prompt.zh.md` 包含「排版順序」段落、`[IMAGE_1]` 佔位符說明、八標籤規範、末尾 `---\n标签：` footer 指引

---

- [ ] **Unit 2: `library.py` — `generations` 表加 `tags` 欄位（migration）**

**Goal:** 讓 `put_generation()` 可存 tags、`get_generation()` 可返回 tags，不破壞既有 DB。

**Requirements:** R4

**Dependencies:** 無（DB schema 獨立）

**Files:**
- Modify: `cpost/core/library.py`
- Modify: `_SCHEMA` 內 `generations` DDL 新增 `tags TEXT`（新 DB）
- Test: `tests/test_generate_article.py`（tags cache round-trip）

**Approach:**
- 在 `_SCHEMA` 的 `CREATE TABLE IF NOT EXISTS generations` 加 `tags TEXT` 欄位（新建 DB 直接有欄位）
- 加 `_MIGRATIONS = [(1, "ALTER TABLE generations ADD COLUMN tags TEXT;")]`（既有 DB 走 migration）
- **`library.connect()`（第 62 行）**：改為 `_db_connect(path, _SCHEMA, migrations=_MIGRATIONS)`——這是讓 migration 真正執行的關鍵，必須明確修改；不更新此呼叫則既有 DB 第一次寫 tags 時拋 `OperationalError: table generations has no column named tags`
- `put_generation()` 新增 `tags: list[str] | None = None` 參數，`json.dumps(tags or [])` 存入；`ON CONFLICT … DO UPDATE SET` 必須包含 `tags=excluded.tags`（否則 conflict 路徑不更新 tags 值）
- `get_generation()` 在函式內 post-process：`dict(row)` 之後加 `result['tags'] = json.loads(result.get('tags') or '[]')` 再回傳（序列化邊界留在 library.py，caller 拿到 `list[str]` 而非 JSON 字串；NULL 安全，相容 migration 前舊 row）

**Patterns to follow:**
- `cpost/core/runs.py` — `_MIGRATIONS = [(1, ...)]` 用法
- `cpost/core/db.py:_apply_statement` — `duplicate column name` idempotent

**Test scenarios:**
- Happy path: `put_generation(..., tags=["人物A","平台X"])` → `get_generation(key)["tags"] == ["人物A","平台X"]`
- Happy path: `tags=None` → `get_generation(key)["tags"] == []`（NULL 安全）
- Edge case: 既有 DB（無 `tags` 欄位，用 raw SQL 插入舊格式 row）→ `library.connect()` migration 後 `get_generation()["tags"] == []`
- Happy path: migration idempotent — 連續兩次 `library.connect()` 不報錯
- Edge case: 同一 cache_key 寫兩次（conflict 路徑），第二次帶不同 tags → `get_generation()["tags"]` 等於第二次的值（驗證 `tags=excluded.tags` 生效）

**Verification:**
- `pytest tests/test_generate_article.py` 全綠
- 新 DB / 既有 DB 兩種情境下 `get_generation` 回傳 `tags` 欄位（type `list[str]`，非 JSON 字串）
- `put_generation` 的 `ON CONFLICT(cache_key) DO UPDATE SET` 包含 `tags=excluded.tags`（conflict 路徑不更新 tags 是靜默 bug）

---

- [ ] **Unit 3: `generate_article.py` — `parse_article()` + tags 填入 `PackageInput`**

**Goal:** 解析新格式 LLM 輸出（title + body + tags），填入 `PackageInput.tags`，bump `_PROMPT_VERSION`。

**Requirements:** R2, R3, R5

**Dependencies:** Unit 2（`put_generation` 需支援 tags 參數）

**Files:**
- Modify: `cpost/cli/generate_article.py`
- Test: `tests/test_generate_article.py`

**Approach:**
- 新增 `parse_article(article: str, fallback_title: str) -> tuple[str, str, list[str]]` 函式（**複合錨定策略**）：
  1. 用 `re.split(r'\n-{3,}\n', article)` 分割，從後往前找第一個其後緊接行 match `^标签[:：]\s*.+` 的位置；找到則以此為切割點，前為 article_part（含 title），後為 footer
  2. 若無符合條件的分隔符，article_part = 整個 article，footer = ""，tags = `[]`
  3. 從 footer 取 `标签：` 行，split on `[，,]`，strip 每個元素，過濾空字串（含尾部逗號、雙逗號的情況）
  4. 從 article_part 第一非空行提取 title（沿用現有邏輯：title > 80 字 → fallback）
  5. body = article_part 去除第一行後的剩餘內容，strip
- **`generate()` cache-hit branch 必須讀 tags**：`if cached:` 分支需加 `tags = cached.get("tags") or []`（否則 cache hit 時 tags 永遠是 `[]` 且 `PackageInput.tags` 缺失）
- 保留 `split_title_body()` 改為 `_split_title_body()`（內部）
- `generate()` 改呼叫 `parse_article()`；在 **`generate_article.py:137-148` 的 `item: PackageInput = {...}` dict literal** 加 `"tags": tags`（這是 tags 進入 `build_manifest → manifest.content.tags` 的唯一路徑，漏掉則 build_manifest 拿到空 list）
- `library.put_generation(... tags=tags)` 傳入
- `cache_key()` 不變（tags 從輸出解析，不是輸入；prompt 內容改變已使 cache miss）
- `_PROMPT_VERSION = "scoop-v2"`

**Patterns to follow:**
- 現有 `split_title_body()` — 標題長度門檻 80 字、fallback 邏輯
- `cpost/core/schema.py:PackageInput.tags` — list[str]

**Test scenarios:**
- Happy path: LLM 輸出含 `\n---\n标签：人物A, 平台X, 内容Y` → `item["tags"] == ["人物A","平台X","内容Y"]`
- Happy path: 繁/簡逗號混用（`，` 和 `,`）→ tags 正確拆分
- Happy path: body + `---` + 正文繼續 + `\n---\n标签：A,B`（多個 `---`）→ 只切最後一個符合條件的，body 完整
- Edge case: LLM 輸出正文含 `\n---\n`（Markdown 橫線）但末尾無 `标签：` → tags `[]`，**body 完整不截斷**（驗證複合錨定策略）
- Edge case: 無 `---` 分隔符的舊格式輸出 → tags 回傳 `[]`，title/body 照舊解析
- Edge case: `标签：` 行為空 → tags 回傳 `[]`
- Edge case: `标签：人物A, , 平台X,`（尾部逗號、雙逗號）→ tags `["人物A","平台X"]`（空字串被過濾）
- Edge case: title > 80 字 + 有 tags footer → fallback title 生效，tags 仍解析
- Integration: `generate()` 兩次（第二次 cache hit）→ `item1["tags"] == item2["tags"]`，明確比對兩次值相等（驗證 cache-hit branch 讀 `cached["tags"]`）
- Regression: 現有 `test_generate_synthesizes_item` 不破（tags 為 `[]` 即可）

**Verification:**
- `pytest tests/test_generate_article.py` 全綠（含既有 + 新增 tests）
- `item["tags"]` 在有 footer 時非空，無 footer 時為 `[]`

---

- [ ] **Unit 4: 更新現有測試並新增 tag 場景**

**Goal:** 讓現有測試在新 `parse_article()` 下全綠，補充 tag 解析專項測試。

**Requirements:** R6

**Dependencies:** Unit 2, Unit 3

**Files:**
- Modify: `tests/test_generate_article.py`

**Approach:**
- 現有測試呼叫 `_chat=lambda ...: "標題行\n正文內容。"` — 無 `---` 分隔符 → tags 降級為 `[]`；現有 assertions 不涉及 tags → 全綠不需改動
- 新增測試：
  - `test_tags_parsed_from_footer` — 有 `\n---\n标签：A, B, C` → tags `["A","B","C"]`
  - `test_tags_missing_footer_defaults_empty` — 舊格式無 footer → tags `[]`
  - `test_tags_cache_round_trip` — generate 兩次（第二次 cache hit），`item1["tags"] == item2["tags"] == ["A","B"]`（明確比對，不只驗非空）
  - `test_tags_empty_label_defaults_empty` — `\n---\n标签：` 空 → tags `[]`
  - `test_body_intact_when_dash_rule_no_footer` — LLM 輸出含 `\n---\n` mid-body 但末尾無 `标签：` → `item["caption"]` 包含 `---` 後的完整正文內容，tags `[]`（驗證複合錨定不截斷 body）
  - `test_tags_trailing_comma_filtered` — `标签：A, , B,` → tags `["A","B"]`（空字串過濾）
  - `test_migration_legacy_row_tags_empty` — raw SQL 插入無 tags 欄位的舊格式 row → `library.connect()` migration 後 `get_generation()["tags"] == []`
  - `test_tags_flow_to_manifest` — generate 一個有 `\n---\n标签：人物A, 平台X` footer 的 item → 餵入 `build_manifest.build()` → `manifest["content"]["tags"] == ["人物A","平台X"]`（端對端驗 tags 不在 PackageInput dict literal 或 build_manifest 路徑上被丟棄）

**Patterns to follow:**
- 現有 `_seed_cluster()` helper 重用

**Test scenarios:**（同 Unit 3 中 Integration 場景，Unit 4 補充 body-intact 和 migration 場景）

**Verification:**
- `pytest tests/test_generate_article.py -v` 全綠，含新 7 個 tag/migration 測試

## System-Wide Impact

- **不影響的路徑**：`build_manifest.py` 已讀 `PackageInput.tags`（`schema.py:238`），無需改動
- **不影響的路徑**：`publish_post.py`、`draft_post.py`、browser driver 全程不碰 tags
- **`article_prompt.zh.md` / `/packages/{id}/generate`（單篇）**：完全不碰，獨立 track
- **Cache 失效（`scoop-v2`）**：既有 `generations` rows 的 `cache_key` 不匹配新 key → 下次呼叫重生；舊 rows 保留在 DB（不刪），tags 欄位為 NULL → migration 後讀舊 row 返回 `[]`
- **`ManifestContent.tags`**：`build_manifest.py` 已從 `PackageInput.tags` 填入，scoop track 首次會有真實 tags

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| LLM 不輸出 `---\n标签：` footer | `parse_article()` 降級回傳 `[]`，不報錯；文章仍正常生成 |
| `---` 出現在正文中（Markdown 橫線，無 tags）| 複合錨定：`re.split(r'\n-{3,}\n', ...)` 分割，下一非空行 match `^标签[:：]\s*.+` 才切割；否則 body 不截斷，tags `[]` |
| Unit 3（version bump）先 Unit 1（新 prompt 檔案）上線 | cache 失效後 LLM 用舊 prompt 重生成；Unit 1 與 Unit 3 須同 PR 或確保 Unit 1 先 merge |
| Unit 3（`put_generation(tags=...)`）先 Unit 2 上線 | Unit 2 未 merge 時 Unit 3 呼叫 `put_generation(tags=...)` 會 TypeError；Unit 2 與 Unit 3 須同 PR 或確保 Unit 2 先 merge |
| 舊 `generations` 欄位 migration 在生產 DB 失敗 | `_apply_statement` idempotent；`duplicate column name` 被吞；worst case tags 為 NULL → `get_generation()` 返回 `[]` |
| `library.connect()` 未傳 `migrations` → 既有 DB 寫 tags 拋 `OperationalError` | Unit 2 明確要求更新第 62 行 `_db_connect(path, _SCHEMA, migrations=_MIGRATIONS)` |
| cache 版本 bump 造成 LLM 重呼叫費用 | 預期行為；既有 generations 失效是 intentional |

## Sources & References

- 現有 prompt: `configs/scoop_prompt.zh.md`
- 單篇 prompt（風格參考）: `configs/article_prompt.zh.md`
- Parser 現況: `cpost/cli/generate_article.py:91-107 split_title_body()`
- Migration pattern: `cpost/core/runs.py:34-35 _MIGRATIONS`
- DB connect: `cpost/core/db.py:17-56 connect()`
- Schema: `cpost/core/schema.py:122-156 PackageInput`
- Library generations: `cpost/core/library.py:208-229`
- Tests: `tests/test_generate_article.py`
