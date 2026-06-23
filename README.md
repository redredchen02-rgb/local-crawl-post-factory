# local-crawl-post-factory

本地優先、CLI 優先的多來源內容聚合管線：**爬取多個站台 → 入庫 → 聚瓜（同事件叢集）→ 打分 → 生成原創文章 → 後台建草稿 → 發布**，給**沒有 API、只能用後台表單**的自有/私有網站使用。每個階段都是獨立命令、以 NDJSON 串接、無狀態、可進 cron / agent / shell pipeline。

> 安全原則：只操作自有或授權站台；不繞過登入 / CAPTCHA / 反爬。預設手動模式只建包不上架；發布必須經審核、驗證、標題確認與 `--approve`。自動發布模式是明確 opt-in，會預先滿足審核門，但仍保留驗證與 `--approve` 語意。

## 主流程

**多來源 → 庫 → 聚瓜 → 生成 → 發布**

```bash
# 1. 爬取多個來源（逐源跑，source_id 隔離）
for source_id in site_a site_b site_c; do
  crawl-posts "https://$source_id.example.com" --source-id "$source_id" \
    | normalize-items | library-ingest --state ./state/library.sqlite
done

# 2. 聚瓜、打分
cluster-scoops --state ./state/library.sqlite --config ./configs/scoring.yaml
score-scoops   --state ./state/library.sqlite --config ./configs/scoring.yaml

# 3. 生成原創文章（以多源 members 為素材）
generate-article --state ./state/library.sqlite --cluster-id <id> \
  --llm-config ./configs/llm.yaml --prompt ./configs/scoop_prompt.zh.md

# 4. 建草稿 → 驗證 → 發布
draft-post   --manifest out/<id>/manifest.json --backend configs/backend.yaml --storage-state auth/storage-state.json
verify-draft --manifest out/<id>/manifest.json --backend configs/backend.yaml --storage-state auth/storage-state.json
publish-post --manifest out/<id>/manifest.json --backend configs/backend.yaml --storage-state auth/storage-state.json --state ./state/published.sqlite --approve
```

也可從 WebUI 操作：`/today` 工作台一站式跑聚瓜 → 生成 → 人工審核 → 發布。

> **CLI 多源 = shell 逐源迴圈**：`crawl-posts` 不直接讀 `sources` 設定（那只活在 WebUI/in-process `crawl_all_sources`），這是刻意的 N=1 對等而非缺陷。

安裝瀏覽器（首次）：`python3 -m playwright install chromium`

## 安裝

```bash
python3 -m pip install -e .          # 核心 (Scrapy / PyYAML)
python3 -m pip install -e '.[dev]'   # 加 pytest / ruff / mypy / pre-commit
```

## 品質檢查

```bash
make lint           # ruff check（CI 硬性閘門）
make typecheck      # mypy 阻斷式型別檢查（0 error 才通過）
make test           # 全部測試（即時數字見 make test-full）
make test-fast      # 快速測試（不含 slow/browser/integration/subprocess）
make test-full      # 全部測試 + 覆蓋率報告
pre-commit install  # 首次：提交前自動跑 ruff（與 CI 同一份 ruff 設定）
```

## I/O 契約（所有命令一致）

| | stdout | stderr | exit |
|---|---|---|---|
| 成功 | 結構化 JSON / NDJSON | 空 | 0 |
| 失敗 | 空 | 一行診斷 | 1–5 |

退出碼：`0` 成功、`1` 用法錯、`2` 輸入/驗證錯、`3` 依賴缺失、`4` 外部服務錯、`5` 未預期內部錯。

## 登入態

```bash
auth-login \
  --login-url "https://example.com/admin/login" \
  --until-url-contains "/admin/posts" \
  --storage-state ./auth/storage-state.json
```

手動登入一次，偵測到登入成功 URL 後存檔（不存密碼）。

## 快速路徑：單來源打包發布

不需聚瓜時，可直接從爬取建包：

```bash
crawl-posts "https://example.com/news" --max-pages 300 --limit 30 \
| normalize-items | dedupe-posts --state ./state/published.sqlite \
| render-caption --template ./templates/fixed-format.zh.yaml \
| build-manifest --out ./out
```

產出 `out/<post_id>/`：`manifest.json`、`caption.txt`、`preview.html`。
狀態流轉：`package_built → drafted → draft_verified → published`。

## 來源探索

`discover-sources` 從現有 YAML sources 的首頁與常見友鏈頁爬取外部域名，自動寫入站台名冊（roster）為 candidate，供後續監控與啟用：

```bash
discover-sources \
  --sources-yaml configs/webui.yaml \
  --roster-path  state/roster.db \
  --max-candidates-per-seed 20 \
  --max-total-candidates 50
# 加 --dry-run 只印候選，不寫入
```

內建 SSRF 防護（過濾 RFC-1918/loopback/link-local）、HTTP HEAD 存活檢查，以及 politeness sleep（每頁 0.5 s）。

## 多源聚合細節

- **可信度只在真獨立媒體成立**：`confidence` 看的是該瓜的獨立來源數；鏡像站共用 canonical 會塌縮成單一來源，故不計入。
- **生稿快取**：以 members 內容 + 模型 + prompt 雜湊為 key，membership 或 prompt 變動才重新生成。
- **WebUI `/today`**：把聚瓜 → 打分 → 生稿收進單頁工作台，與 CLI 共用 `cpost.core.scoop_pipeline`，邏輯不重複；全庫仍是單一來源時會在 UI 標明「可信度尚無意義」。

## 狀態與去重

- 狀態存於 SQLite（`--state`）。`crawl-posts` 不寫狀態；`build-manifest` 起寫 `package_built`；`publish-post` 寫 `published`。
- **去重只認 `published`**：只有真正發布過的 `canonical_url` / `title_hash` 會被跳過。首版尚無發布階段，故 dedupe 實質永遠放行 —— 此為預期行為。
- **跳過皆可見**：經 WebUI/pipeline 跑時，每筆被跳過的項目都會記入運行歷史（`runs`，`stage=dedupe`、`status=skipped`，並標明命中 `url` 還是 `title`），不再靜默丟棄；`dedupe-posts` CLI 維持 READ-ONLY。

## 測試

```bash
python3 -m pytest -q     # 含 Playwright 端到端 + 控制台閘門；即時通過數見 make test-full
make test-fast           # ~10 秒快速迭代
make test-full           # 全部測試 + 覆蓋率
```

## 快速試跑（離線 demo，不需網路/瀏覽器）

```bash
make install        # 或 make install-browser 連同 Playwright
make demo           # 把 inputs/sample.ndjson 跑成 out/demo/<post_id>/ 包
make test-fast      # 快速測試（不含 slow/browser/integration）
```

## WebUI（本機）

FastAPI + HTMX 的本機介面：儀表板第一屏顯示 Crawl、Packages、Recent History 概覽。
**只綁 `127.0.0.1`、勿暴露公網；預設 WebUI 只建包上膛，啟用「自動發布模式」後才會在同一個 job 中串接建草稿 → 驗證 → 發布。**

```bash
make install-webui      # 安裝 web 依賴
make vendor-htmx        # 下載 htmx（首次，UI 互動需要）
make webui              # 啟動 → http://127.0.0.1:8000
# 或直接：crawl-post-webui
```

`make webui` 底層呼叫的是 console script `crawl-post-webui`（`pip install -e '.[webui]'` 後可直接執行）；要自訂 host/port 或不經 make 時可直接跑 `crawl-post-webui`。

設定存 `configs/webui.yaml`（與 CLI 共用爬蟲/模板的既有 yaml）。WebUI 與 CLI 跑的是**同一條** `cpost.core.pipeline` orchestrator，邏輯不重複。

## 日常營運（硬化）

- **瀏覽器韌性**：`draft/verify/publish` 對暫時性失敗自動重試（`backend.yaml` 的 `retry` 或 `--retries`）；失敗時於該包目錄存 `failure_<stage>_<ts>.png` + `failure.json`。
- **登入態**：偵測到被導回登入頁（`backend.yaml` 的 `login_required_url_contains`）會回 `SessionExpiredError`（exit 4，訊息提示重跑 `auth-login`）；WebUI 導覽列有登入態狀態燈（綠有效／紅過期／灰未設定），過期/未設定時直接顯示重登的 `auth-login` 指令（狀態判斷只讀檔案 metadata，不讀登入態內容）。
- **全控制台**：WebUI 審核頁可直接「建草稿 / 驗證 / 發布」。**發布三重閘門**：① 必須先開啟審核頁 ② 狀態須 `draft_verified` ③ 輸入正確標題；皆過才以 `--approve` 語意發布。上膛清單支援多選後「批量建草稿 / 批量驗證」（逐項隔離、共用一個 `run_id`）；**發布不批量化**（閘門逐篇人工）。
- **自動發布模式（opt-in）**：設定頁可開啟 `auto_pipeline`。開啟後按「立即爬取」會自動執行爬取 → 建包 → 建草稿 → 驗證 → 發布；系統會以目前 manifest 內容預先標記審核門，驗證狀態與 `--approve` 語意仍由後台命令保護。執行期間不要同時手動審閱同一批稿件。
- **運行歷史**：`/history` 查 `runs` 表、`/audit` 查 `audit.jsonl`，跨重啟保留；`runs` 帶 `run_id`（同一次運行的關聯）與 `severity`，`/history` 可按 `post_id`/`severity` 篩選，便於回查整條生命週期。
- **爬取禮貌**：設定頁可調 `download_delay` 與 `concurrency`。
- **配置可移植**：輸出路徑（state/out/download/audit/storage_state）相對設定檔目錄解析；可用 `CPOST_STATE_PATH`/`CPOST_OUT_DIR`/`CPOST_DOWNLOAD_DIR` 環境變數覆蓋。

## 可跑範例 / 自動化排程

完整的可跑範例（cron 多來源匯整 + 建草稿、退出碼處理、登入態到期、人工發布、opt-in 自動發布）見 [`examples/scheduling.md`](examples/scheduling.md)。所有命令都是上面 `[project.scripts]` 裝出的 console script（`pip install -e .` 後即可用）。
預設排程仍建議停在建草稿或驗證；若要排程觸發自動發布，必須先明確開啟 `auto_pipeline`，並接受它會預先滿足審核門的風險。

## 私有安裝（D2）

本套件為**專有授權**，不發布至 PyPI。三種私有交付方式：

### 方式 A：wheel（推薦，可重現）

```bash
# 1. 在開發機 build
make build          # 產出 dist/local_crawl_post_factory-0.3.0-py3-none-any.whl

# 2. 以鎖定依賴安裝（需先 make lock 生成 requirements.lock）
pip install --require-hashes -r requirements.lock
pip install dist/local_crawl_post_factory-0.3.0-py3-none-any.whl --no-deps
```

### 方式 B：git+ssh 釘 SHA（不經 build 步驟）

```bash
# 必須釘 40-char commit SHA，禁止釘 branch/tag（不可重現）
pip install "git+ssh://git@github.com/<你>/cpost@<40-char-SHA>#egg=local-crawl-post-factory"
```

### 方式 C：本地 editable install

```bash
pip install -e '.[dev]'   # 開發用
```

---

## 設計與計畫

- 需求：`docs/brainstorms/2026-06-15-local-crawl-post-factory-requirements.md`
- 技術計畫：`docs/plans/2026-06-15-001-feat-local-crawl-post-factory-plan.md`
