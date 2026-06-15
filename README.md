# local-crawl-post-factory

本地優先、CLI 優先的「爬取 → 打包 → 後台建草稿 → 發布」內容管線，給**沒有 API、只能用後台表單**的自有/私有網站使用。每個階段都是獨立命令、以 NDJSON 串接、無狀態、可進 cron / agent / shell pipeline。

> 安全原則：只操作自有或授權站台；不繞過登入 / CAPTCHA / 反爬；爬完**絕不**自動發布；發布必須帶 `--approve`。

## 本版範圍

- **Phase 1-3 資料/媒體管線**：`crawl-posts`、`normalize-items`、`dedupe-posts`、`render-caption`、`select-cover`、`watermark-cover`、`build-manifest`。
- **Phase 4-5 後台自動化（Playwright，已實作）**：`draft-post`、`verify-draft`、`publish-post`。選擇器全來自 `backend.yaml`（零硬編碼），登入態用 Playwright `storage_state`（不存密碼），`publish-post` 雙重閘門（`--approve` + 狀態 `draft_verified`）。以本地 mock admin 做端到端測試。

安裝瀏覽器（首次）：`python3 -m playwright install chromium`

## 安裝

```bash
python3 -m pip install -e .          # 核心 (Scrapy / Pillow / PyYAML)
python3 -m pip install -e '.[dev]'   # 加 pytest
```

## I/O 契約（所有命令一致）

| | stdout | stderr | exit |
|---|---|---|---|
| 成功 | 結構化 JSON / NDJSON | 空 | 0 |
| 失敗 | 空 | 一行診斷 | 1–5 |

退出碼：`0` 成功、`1` 用法錯、`2` 輸入/驗證錯、`3` 依賴缺失、`4` 外部服務錯、`5` 未預期內部錯。

## 端到端範例

```bash
crawl-posts "https://example.com/news" \
  --item-regex "/news/|/article/|/post/" \
  --deny-regex "login|admin|tag|category|search|page/[0-9]+" \
  --max-pages 300 --limit 30 \
| normalize-items \
| dedupe-posts --state ./state/published.sqlite \
| render-caption --template ./templates/fixed-format.zh.yaml \
| select-cover --download-dir ./out/assets \
| watermark-cover --config ./configs/watermark.yaml \
| build-manifest --out ./out
```

產出 `out/<post_id>/`：`manifest.json`、`caption.txt`、`cover.jpg`、`watermarked_cover.jpg`、`preview.html`。

先產生登入態（手動登入一次，偵測到登入成功 URL 後存檔；不存密碼）：

```bash
auth-login \
  --login-url "https://example.com/admin/login" \
  --until-url-contains "/admin/posts" \
  --storage-state ./auth/storage-state.json
```

後台階段（自家 admin，選擇器全來自 `configs/backend.yaml`，登入態用 `--storage-state`）：

```bash
draft-post   --manifest out/<id>/manifest.json --backend configs/backend.yaml --dry-run
draft-post   --manifest out/<id>/manifest.json --backend configs/backend.yaml --storage-state auth/storage-state.json
verify-draft --manifest out/<id>/manifest.json --backend configs/backend.yaml --storage-state auth/storage-state.json
publish-post --manifest out/<id>/manifest.json --backend configs/backend.yaml --storage-state auth/storage-state.json --state ./state/published.sqlite --approve
```

狀態流轉：`package_built → drafted → draft_verified → published`。`publish-post --state` 會把 `canonical_url` 標記為 `published`，下一輪 `dedupe-posts` 即會跳過。

## 狀態與去重

- 狀態存於 SQLite（`--state`）。`crawl-posts` 不寫狀態；`build-manifest` 起寫 `package_built`；`publish-post` 寫 `published`。
- **去重只認 `published`**：只有真正發布過的 `canonical_url` / `title_hash` 會被跳過。首版尚無發布階段，故 dedupe 實質永遠放行 —— 此為預期行為。

## 測試

```bash
python3 -m pytest -q     # 70 passed (含 Playwright 端到端流程)
```

## 快速試跑（離線 demo，不需網路/瀏覽器）

```bash
make install        # 或 make install-browser 連同 Playwright
make demo           # 把 inputs/sample.ndjson 跑成 out/demo/<post_id>/ 包
make test           # 70 passed
```

## 排程 / Agent 自動化

見 [`examples/scheduling.md`](examples/scheduling.md) — cron 建草稿範本、退出碼處理、登入態到期、人工發布。
**自動化只到建草稿；發布永遠是人工 `--approve`。**

## 設計與計畫

- 需求：`docs/brainstorms/2026-06-15-local-crawl-post-factory-requirements.md`
- 技術計畫：`docs/plans/2026-06-15-001-feat-local-crawl-post-factory-plan.md`
