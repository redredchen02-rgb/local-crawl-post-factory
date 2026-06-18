#!/bin/bash
#
# 啟動本地服務（local-crawl-post-factory WebUI）
# 雙擊此檔即可啟動 → http://127.0.0.1:8000
#
# 專案：local-crawl-post-factory
# 說明：本地優先的「爬取 → 打包 → 後台建草稿 → 發布」內容管線
#

# ── 讓 pyenv 安裝的指令在 Finder 雙擊情境下也能找到（無 pyenv 則略過） ──
[ -d "$HOME/.pyenv/shims" ] && export PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:$PATH"

# ── 設定 ──
# 專案目錄 = 本腳本所在目錄（自我定位；搬到任何路徑/機器皆正確）
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
URL="http://127.0.0.1:8000"
SERVICE_CMD="crawl-post-webui"

# ── 切換到專案目錄 ──
cd "$PROJECT_DIR" || {
    echo "❌ 找不到專案資料夾：$PROJECT_DIR"
    echo "（按 Enter 鍵關閉視窗）"
    read -r _
    exit 1
}

# ── 確認指令存在 ──
if ! command -v "$SERVICE_CMD" &>/dev/null; then
    echo "❌ 找不到指令：$SERVICE_CMD"
    echo "   請先安裝：python3 -m pip install -e '.[webui]'"
    echo "（按 Enter 鍵關閉視窗）"
    read -r _
    exit 1
fi

# ── 標頭 ──
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   📡 local-crawl-post-factory WebUI          ║"
echo "  ║   🌐  $URL               ║"
echo "  ║                                              ║"
echo "  ║   ⏹  按 Control + C 停止服務                 ║"
echo "  ║      或直接關閉此視窗                         ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""

# ── 若服務已在執行，直接開瀏覽器 ──
if curl -s -o /dev/null --max-time 2 "$URL"; then
    echo "✅ 服務已在執行，開啟瀏覽器中…"
    open "$URL"
    echo ""
    echo "（此視窗可關閉，背景服務不受影響；按 Enter 關閉）"
    read -r _
    exit 0
fi

# ── 背景等服務就緒後自動開瀏覽器（最多等 30 秒） ──
(
    for _ in $(seq 1 30); do
        if curl -s -o /dev/null --max-time 1 "$URL" 2>/dev/null; then
            open "$URL"
            break
        fi
        sleep 1
    done
) &

# ── 前台啟動服務（顯示日誌；Ctrl+C 或關閉視窗即停止） ──
$SERVICE_CMD

# ── 服務停止後的提示 ──
echo ""
echo "================================================"
echo "  ❖ 服務已停止"
echo "  （按 Enter 鍵關閉視窗）"
echo "================================================"
read -r _
