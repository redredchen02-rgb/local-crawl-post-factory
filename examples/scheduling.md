# 排程 / Agent 自動化範例

所有命令都是無狀態、stdin→stdout NDJSON、退出碼穩定，因此可安全進 cron 或 coding agent。
**設計原則：自動化只到「建草稿」為止；發布永遠是人工 `--approve` 的獨立動作。**

## 1. 每日建包 + 建草稿（cron，安全）

爬取 → 管線 → 建草稿。發布**不**進排程。

```cron
# 每天 02:00 跑：爬最新內容、建包、建草稿（不發布）
0 2 * * *  cd /path/to/local-crawl-post-factory && /usr/bin/env bash scripts/cron_draft.sh >> logs/cron.log 2>&1
```

`scripts/cron_draft.sh`（自行建立）大致如下：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

crawl-posts "https://example.com/news" \
    --item-regex "/news/|/article/" \
    --deny-regex "login|admin|tag|category|search|page/[0-9]+" \
    --limit 30 \
  | normalize-items \
  | dedupe-posts --state ./state/published.sqlite \
  | render-caption --template ./templates/fixed-format.zh.yaml \
  | select-cover --download-dir ./out/assets \
  | watermark-cover --config ./configs/watermark.yaml \
  | build-manifest --out ./out

# 對每個新包建草稿（需事先 auth-login 產生 storage-state）
find ./out -name manifest.json -print0 \
  | xargs -0 -n 1 -I{} draft-post \
      --manifest {} \
      --backend ./configs/backend.yaml \
      --storage-state ./auth/storage-state.json \
      --headless
```

## 2. 退出碼處理（讓排程器/agent 能判斷）

| 碼 | 意義 | 排程建議 |
|---|---|---|
| 0 | 成功 | 繼續 |
| 1 | 用法錯 | 修指令，不重試 |
| 2 | 輸入/驗證錯 | 修資料，不重試 |
| 3 | 依賴缺失 | 安裝依賴（如 `playwright install chromium`） |
| 4 | 外部服務錯（站台/後台不可達、逾時） | 可退避重試 |
| 5 | 未預期內部錯 | 報警、查 log |

因為失敗時 stdout 為空、stderr 只有一行診斷，排程器可直接把 stderr 當告警內容。

## 3. 登入態到期

`storage-state.json` 內的 session 會過期。排程偵測到 `draft-post` 連續回 4（後台要求重新登入）時，需人工重跑一次：

```bash
auth-login \
  --login-url "https://example.com/admin/login" \
  --until-url-contains "/admin/posts" \
  --storage-state ./auth/storage-state.json
```

## 4. 發布（永遠人工）

排程只負責把草稿準備好。確認內容後，人工逐一核可：

```bash
verify-draft  --manifest ./out/<id>/manifest.json --backend ./configs/backend.yaml --storage-state ./auth/storage-state.json --headless
publish-post  --manifest ./out/<id>/manifest.json --backend ./configs/backend.yaml --storage-state ./auth/storage-state.json --state ./state/published.sqlite --approve --headless
```

`publish-post --state` 會把該 `canonical_url` 標為 `published`，下一輪 `dedupe-posts` 自動跳過，避免重複建包。
