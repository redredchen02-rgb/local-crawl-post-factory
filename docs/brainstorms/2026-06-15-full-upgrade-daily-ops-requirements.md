---
date: 2026-06-15
topic: full-upgrade-daily-ops
---

# 全面升級：日常營運硬化 + WebUI 全控制台

## Problem Frame

現有系統（CLI 管線 + Playwright 後台自動化 + WebUI 審核頁，99 測試）對「單人、單站、本機、一次性試跑」已功能完整。但要**投入真實日常營運**，三個缺口會痛：

1. **脆弱**：瀏覽器層遇真實站台的暫時性失敗（逾時、慢渲染、session 過期）會直接整批失敗且難查，無重試、無失敗現場。
2. **看不見**：背景 job 重啟即丟，沒有運行/發布歷史，營運者無法回答「昨天那批怎麼了」。
3. **流程斷裂**：審核完仍要切到終端機敲 CLI 才能發布，日常高頻操作不順手。

升級目標：讓系統能**每天穩定跑真實站台**，並把 WebUI 從「只審核」進化成「爬取→審核→發布」的**全控制台**，同時把「發布」這個不可逆動作鎖在最強的人工閘門後面。

## 角色與範圍前提（已定）

- 重心＝**日常營運硬化**；規模維持**單站單後台**（不加 site/backend profile）。
- 自動排程**維持外部 cron**（沿用 `examples/scheduling.md`），app 不跑長連線排程。
- WebUI 進化為**全控制台**，**新增**從 UI 發布的能力——這刻意改寫原「發布只能 CLI」邊界，改用更強的人工閘門取代之。

## User Flow（全控制台 + 發布閘門）

```text
設定頁(URL/regex/limit/限速) ──一鍵──> 背景爬取建包(有重試/進度/歷史)
        │
        ▼
   上膛清單 ──點入──> 審核頁(文案/封面/來源)
                          │ 此頁開啟即記錄「已審核」
                          ▼
              [建草稿] ─▶ [驗證] ─▶ [發布] (此鈕僅在: 已審核 + 狀態=draft_verified 時出現)
                                        │
                                  輸入「貼文標題」二次確認
                                        ▼
                          發布(--approve 語意) → 寫歷史 + 標 published
        │
   任一後台動作偵測到 session 過期 → 明確提示「請重跑 auth-login」+ UI 狀態燈
```

## Requirements

**瀏覽器韌性與登入態（Resilience）**
- R1. `draft/verify/publish` 對暫時性失敗（逾時、selector 暫時找不到、導航失敗）自動重試有限次數（可設定），重試耗盡才判失敗。
- R2. 任一後台動作失敗時，於該貼文包目錄存下失敗現場：截圖 + 當下 URL +（可選）頁面 HTML 片段，供事後查問題。
- R3. 偵測「登入態過期 / 被導回登入頁」此特定情況，回報為可辨識的明確狀態（與一般 selector 逾時區分），並在 CLI/UI 給出「請重跑 `auth-login`」的明確引導。
- R4. UI 顯示登入態狀態燈（有效 / 過期 / 未設定），讓營運者一眼判斷是否需要重新登入。

**WebUI 全控制台與發布閘門（Control Center）**
- R5. 審核頁新增三個動作鈕：建草稿、驗證、發布；各自觸發既有後台邏輯（帶 storage-state），以背景動作執行並回報結果。
- R6. 發布鈕為三重閘門：① 必須先開啟過該包審核頁（系統記錄已審核）② 狀態須為 `draft_verified` ③ 須在發布前輸入正確「貼文標題」字串才解鎖送出。三者皆滿足才執行，且仍走既有 `--approve` 語意。
- R7. 任一動作的成功/失敗即時回饋於 UI；session 過期等可辨識錯誤給對應引導而非空泛報錯。
- R8. 控制台動作不繞過任何既有閘門：未驗證不可發布、無 storage-state 給明確提示、發布為不可逆且需上述三重確認。

**可觀測與運行歷史（Observability）**
- R9. 爬取/建包/建草稿/驗證/發布的每次運行與結果持久化（跨 app 重啟保留），含時間、動作、post_id、結果、錯誤。
- R10. UI 提供運行歷史檢視與既有 `audit.jsonl` 的瀏覽，營運者可回查「某篇/某天發生什麼」。
- R11. 已發布歷史可查（post_id、published_url、發布時間），與去重狀態一致。

**爬取禮貌（Politeness）**
- R12. 可設定每站爬取限速 / 下載延遲 / 並發上限（沿用既有 crawler 設定欄位），並可在設定頁調整，避免壓垮自有站或被擋。

## Success Criteria

- 對一個會間歇性逾時的真實站台，連跑一週每日批次，暫時性失敗能由重試吸收；真失敗有截圖可查、不靜默。
- session 過期時，營運者在 UI 一眼看到狀態燈為「過期」並依引導重登，無需翻 log。
- 整個「爬取 → 審核 → 發布」可全程在 WebUI 完成，且發布前一定經過「看過審核頁 + 輸入標題」兩道人工確認。
- app 重啟後，昨天的運行/發布歷史仍查得到。
- 既有 99 測試維持綠，新增行為皆有測試（含發布閘門的拒絕路徑）。

## Scope Boundaries

- 不做多來源站 / 多後台 / profile 管理（維持單站單後台）。
- 不在 app 內跑長連線排程器（排程維持外部 cron）。
- 不依賴付費 LLM；不改動固定模板文案生成方式。
- 不做帳號系統 / 對外服務 / 多使用者（維持 localhost-only、無 auth 面）。
- 不自動繞過或自動處理 CAPTCHA / 反爬 / 登入（登入仍是人工 `auth-login`）。
- 不改既有 CLI 命令的 I/O 契約與退出碼語意（硬化以新增為主，向後相容）。

## Key Decisions

- **以最強閘門換取 UI 發布便利（R6）**：原「發布只能 CLI」邊界改為「UI 可發布，但鎖在『看過審核頁 + 輸入標題 + 狀態已驗證 + --approve 語意』之後」。理由：日常高頻操作順手，同時把不可逆動作的人為確認強度拉到最高。
- **失敗要留現場而非只記訊息（R2）**：瀏覽器層最脆弱，截圖+URL 是最低成本、最高價值的可查性投資。
- **歷史持久化（R9）**：日常營運的核心是「可回查」；in-memory job 不夠，運行/發布歷史落地儲存。
- **session 過期是一等公民錯誤（R3/R4）**：與一般逾時區分，因為它的處置方式不同（要人重登，不是重試）。
- **維持外部 cron**：app 不背長連線排程的營運負擔；硬化聚焦在「每次跑得穩、查得到」。

## Dependencies / Assumptions

- 沿用既有 `core/`、`browser/`、`webui/`、CLI 與 `configs/*.yaml`；升級以擴充為主、不破壞契約。
- 假設營運者已用 `auth-login` 取得 storage-state；過期後願意人工重登。
- 真實後台的 selector 仍需由營運者在 `configs/backend.yaml` 校準（既有前提，不在本次範圍）。

## Outstanding Questions

### Deferred to Planning
- [R1][Technical] 重試的退避策略與「暫時性 vs 永久性」失敗如何分類（哪些錯誤該重試、上限/間隔）。
- [R2][Technical] 失敗截圖的存放與命名、是否含 HTML 片段、隱私（截圖可能含後台內容）的處理。
- [R3][Needs research] 「被導回登入頁 / session 過期」在真實自家後台的可靠偵測訊號（URL 變化？特定元素？）——需有真實後台時驗證。
- [R5][Technical] UI 觸發 `draft/verify/publish` 是走程序內呼叫 Playwright 還是子行程；以及 headed/headless 取捨。
- [R3][Technical] UI 偵測到 session 過期後，重登是引導營運者到終端跑 `auth-login`，還是由 localhost 後端開 headed 瀏覽器；mechanism 待定。
- [R9][Technical] 運行/發布歷史的儲存形式（沿用 `state/*.sqlite` 加表 vs 新 DB）與和既有 `items` 表/`audit.jsonl` 的關係，避免重複真相來源。
- [R6][Technical] 「已審核」狀態如何記錄（server 端 session/旗標 vs 寫進 manifest），與多分頁/重整的互動。

## Next Steps
→ `/ce:plan` for structured implementation planning
