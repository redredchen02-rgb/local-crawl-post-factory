# 排程 / Agent 自動化範例

> 先決條件：先 `pip install -e .`，確認以下 console script 都在 PATH（`auth-login`、`crawl-posts`、`normalize-items`、`library-ingest`、`cluster-scoops`、`score-scoops`、`generate-article`、`build-manifest`、`draft-post`、`verify-draft`、`publish-post`、`dedupe-posts`；WebUI 為 `crawl-post-webui`）。

所有命令都是無狀態、stdin→stdout NDJSON、退出碼穩定，因此可安全進 cron 或 coding agent。
**預設安全原則：排程只到「建草稿」或「驗證」為止；發布維持人工 `--approve`。** 若已在 WebUI 明確啟用 `auto_pipeline`，可由本機 WebUI job 串接發布，但這是 opt-in 風險模式，會預先滿足審核門。

## 1. 每日多來源匯整 + 建草稿（cron，安全）

多來源匯整流：**逐來源爬取 → 匯進同一個共用庫 → 跨源聚瓜 → 打分 → 人工挑稿生成 → 建包 → 建草稿**。發布**不**進排程。

> CLI 的 `crawl-posts` 是單一 URL 入口（一次一個來源），沒有「讀 `sources` 設定清單」的旗標——那個逐源 enabled 過濾／隔離邏輯只活在 in-process 的 `crawl_all_sources`（WebUI／`run_pipeline` 路徑）。所以在 cron／agent 場景，**逐來源迴圈寫在 shell**：每個來源各自一行 `crawl-posts`，各帶**自己的** `--source-id`，再 `library-ingest --state` 累加進**同一個共用庫**。匯整就是這樣靠 `library-ingest` 把多源沉澱成一個庫（以 `canonical_url` 去重、保留 `source_id` 出處）。

```cron
# 每天 02:00 跑：逐來源匯整、聚瓜、打分（不發布）
0 2 * * *  cd /path/to/local-crawl-post-factory && /usr/bin/env bash scripts/cron_aggregate.sh >> logs/cron.log 2>&1
```

`scripts/cron_aggregate.sh`（自行建立）大致如下：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

STATE="./state/library.sqlite"

# 逐來源爬取 → 匯進同一個共用庫。
# 每次迭代帶該來源自己的 --source-id（crawl-posts 的 --source-id 是套到全部
# items 的單一標量；library-ingest 會原樣保留它作為出處）。
# 想加來源就在這個迴圈加一行 source_id|url。
while IFS='|' read -r source_id url; do
  crawl-posts "$url" \
      --source-id "$source_id" \
      --item-regex "/news/|/article/" \
      --deny-regex "login|admin|tag|category|search|page/[0-9]+" \
      --limit 30 \
    | normalize-items \
    | library-ingest --state "$STATE"
done <<'SOURCES'
51cg|https://example.com/news
51ph|https://another.example.org/posts
SOURCES

# 全部來源匯入後，跨源聚瓜 + 打分（都讀同一個共用庫）。
cluster-scoops --state "$STATE"
score-scoops   --state "$STATE"
```

接著由人工挑稿：開 WebUI 的 `/today` 工作台、或直接 `generate-article` 把選定的瓜生成一篇，再 `build-manifest` 建包：

```bash
# 人工從打分後的庫挑一篇生成（取代舊的逐站轉貼）
generate-article --state ./state/library.sqlite --cluster-id <id> \
  | build-manifest --out ./out

# 對每個新包建草稿（需事先 auth-login 產生 storage-state）
find ./out -name manifest.json -print0 \
  | xargs -0 -n 1 -I{} draft-post \
      --manifest {} \
      --backend ./configs/backend.yaml \
      --storage-state ./auth/storage-state.json \
      --headless
```

建草稿後的驗證／發布維持人工逐一核可（見 §4）。

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

## 4. 發布（預設人工）

排程只負責把草稿準備好。確認內容後，人工逐一核可：

```bash
verify-draft  --manifest ./out/<id>/manifest.json --backend ./configs/backend.yaml --storage-state ./auth/storage-state.json --headless
publish-post  --manifest ./out/<id>/manifest.json --backend ./configs/backend.yaml --storage-state ./auth/storage-state.json --state ./state/published.sqlite --approve --headless
```

`publish-post --state` 會把該 `canonical_url` 標為 `published`，下一輪 `dedupe-posts` 自動跳過，避免重複建包。

## 5. 自動發布模式（opt-in）

如果日常操作已接受全自動發布風險，可在 WebUI 設定頁開啟 `auto_pipeline`，再由 WebUI 的「立即爬取」按鈕啟動同一個 job。該模式會執行：爬取 → 建包 → 建草稿 → 驗證 → 發布。

注意事項：

- 自動模式會以 manifest 當下內容預先標記審核門；不要在同一批 job 執行期間手動審閱同一批稿件。
- 驗證狀態與 `--approve` 語意仍由 backend 命令保護。
- 失敗與跳過結果應從 WebUI job 面板、`/history` 和 package detail 回查，不要只看 cron stdout。
