---
date: 2026-06-16
topic: auto-pipeline
---

# 全自動管線（Auto-Pipeline）

## Problem Frame

目前 WebUI 的爬取→草稿→驗證→發布流程需要四次手動觸發，每次都要等待完成後再操作下一步。用戶每天需重複執行這套流程，希望透過一個「自動模式」開關，讓系統在爬取完成後自動完成後續所有步驟，並在 WebUI 執行歷史裡留下完整記錄。

**現有安全閘門（保留結構，自動模式下繞過 review gate）：**

```
crawl → build-manifest → [①reviewed gate] → draft → verify → [②publish_gates] → publish
                              ↑
                        自動模式繞過此門
```

## Requirements

**自動模式開關**
- R1. WebUI 設定頁增加「自動模式」布林開關，預設關閉（OFF）。
- R2. 開關狀態持久化至 `configs/webui.yaml`（與現有欄位一致）。
- R3. 自動模式開啟時，設定頁顯示醒目提示說明 reviewed gate 已繞過。

**全自動管線執行**
- R4. 自動模式開啟時，`/crawl` 完成後自動依序執行：draft → verify → publish（全部批次）。
- R5. 自動模式繞過 reviewed gate（R1 of 現有三重閘門），其他兩道閘門（狀態必須是 `package_built`/`draft_verified`、`--approve` 等效邏輯）繼續生效。
- R6. 每個階段（draft/verify/publish）均在同一 job 串流裡用現有 `jobs.report()` 輸出進度訊息，WebUI 可即時看到當前處理哪一筆。
- R7. 整輪執行完成後，總結訊息顯示：成功幾篇 / 失敗幾篇 / 跳過幾篇。

**失敗重試**
- R8. 每篇文章在 draft/verify/publish 任一步驟失敗時，自動重試最多 3 次（含首次，共 3 次嘗試）。
- R9. 3 次嘗試均失敗，記錄失敗原因至現有 runs history，繼續處理下一篇（不中斷整批）。
- R10. 重試間隔固定 1 秒（避免單篇佔用過長時間，不需退避策略）。

**手動模式（預設）不變**
- R11. 自動模式關閉時，現有流程完全不受影響（reviewed gate、逐篇手動確認）。

## Success Criteria

- 在自動模式開啟狀態下，按下「開始爬取」後，無需任何額外操作，所有新增文章最終出現在 `published` 狀態。
- 失敗文章不阻斷整批，WebUI 歷史頁可追溯每篇的重試紀錄。
- 自動模式預設 OFF，不影響現有手動用戶體驗。

## Scope Boundaries

- **不在此輪範圍**：排程定時自動執行（cron/schedule），此輪只做「按下後全自動跑完」。
- **不在此輪範圍**：macOS 通知或 Telegram 推播（WebUI 歷史記錄已足夠）。
- **不在此輪範圍**：多站點管理。
- reviewed gate 繞過只在自動模式下生效，不修改或刪除 gate 邏輯本身。

## Key Decisions

- **繞過 reviewed gate 而非刪除**：保留手動模式的安全設計，自動模式視為用戶主動降低安全係數的選擇，設定頁醒目提示。
- **重試上限 3 次**：平衡穩定性與效能，不讓單篇卡住整批超過 ~10 秒。
- **同一 job 串流**：不為自動模式建立新的 job 類型，沿用現有 `jobs` + `jobs.report()` 機制，最小化改動範圍。

## Dependencies / Assumptions

- 現有 `batch_action` 邏輯（`/batch/{stage}`）已支援批次 draft/verify/publish，自動管線可複用此邏輯或直接呼叫底層函式。
- `check_publish_gates` 已抽為純函式（可單測），可傳入 `skip_reviewed=True` 參數。

## Outstanding Questions

### Resolve Before Planning

（無）

### Deferred to Planning

- [Affects R5][Technical] reviewed gate 繞過在 caller 層處理（自動模式下直接跳過 `check_publish_gates` 中的 reviewed 判斷分支），不修改 `check_publish_gates` 函式簽名。具體條件判斷位置需看 `action_publish` 的呼叫路徑確認。
- [Affects R4][Technical] 自動管線是在 `/crawl` 的 `_work()` 函式末尾串接，還是另建 `/auto-run` endpoint？建議前者（最少新增 API surface）。

## Next Steps

→ `/ce:plan` for structured implementation planning
