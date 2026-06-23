"""health-check-sources: 對 roster 中的 candidate/monitored 站點進行健康評估。

流程：
1. 讀 roster，篩出指定 tier 的站點
2. 對每個站點以 crawl_items 取樣爬取（limit=10）
3. 評估：item_count、freshness、鏡像偵測（canonical URL 與 library 的重疊率）
4. Tier 狀態機轉換：
   - candidate 通過 → MONITORED；candidate 鏡像 → MIRROR；失敗 3 次 → FAILED
   - monitored 通過（1 次即晉升）→ ACTIVE；失敗 3 次 → FAILED
   - active 失敗 3 次 → INACTIVE
5. 寫回 roster（--dry-run 時僅列印結果）
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from cpost.cli import crawl_posts
from cpost.core import library, site_roster
from cpost.core.scoring_config import DEFAULTS as _SCORING_DEFAULTS
from cpost.core.url_utils import normalize_url

logger = logging.getLogger(__name__)

# ─── 預設值 ────────────────────────────────────────────────────────────────
_DEFAULT_LIBRARY_DB = "./state/published.sqlite"
_FRESHNESS_WINDOW_HOURS = 72
_SAMPLE_LIMIT = 10
_FAIL_THRESHOLD = 3


# ─── 時間工具 ───────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _is_fresh(published_at: str | None, item_count: int) -> bool:
    """判斷是否有新鮮內容。

    有 published_at → 距今 < 72h 視為新鮮。
    無 published_at → item_count ≥ 3 視為新鮮（降級判斷）。
    """
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (_utcnow() - dt) < timedelta(hours=_FRESHNESS_WINDOW_HOURS)
        except (ValueError, OverflowError):
            pass
    return item_count >= 3


# ─── 鏡像偵測 ───────────────────────────────────────────────────────────────

def _load_library_canonical_urls(library_db: str) -> set[str]:
    """讀取 library DB 中的 canonical URL 集合；失敗時回傳空集合。"""
    try:
        with library.connect(library_db) as conn:
            rows = conn.execute(
                "SELECT canonical_url FROM library_items"
            ).fetchall()
        return {row[0] for row in rows if row[0]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("無法讀取 library DB %r，跳過鏡像偵測：%s", library_db, exc)
        return set()


def _compute_mirror_overlap(
    sample_items: list[dict[str, Any]],
    library_urls: set[str],
) -> float:
    """計算 sample canonical URL 與 library existing URL 的重疊率。

    overlap = |intersection| / max(|sample_canonicals|, 1)
    """
    if not library_urls:
        return 0.0
    sample_canonicals: set[str] = set()
    for item in sample_items:
        url = item.get("url") or item.get("canonical_url") or ""
        if url:
            try:
                sample_canonicals.add(normalize_url(url))
            except Exception:  # noqa: BLE001
                pass
    if not sample_canonicals:
        return 0.0
    intersection = sample_canonicals & library_urls
    return len(intersection) / max(len(sample_canonicals), 1)


# ─── 單站評估 ───────────────────────────────────────────────────────────────

def _assess_site(
    site: dict[str, Any],
    library_urls: set[str],
    mirror_overlap_threshold: float,
) -> dict[str, Any]:
    """對一個站點進行健康評估，回傳評估結果 dict。

    Keys:
        domain, tier, item_count, is_fresh, is_mirror, overlap,
        crawl_ok, error (optional)
    """
    domain = str(site["domain"])
    start_url = str(site.get("start_url") or "")
    source_id = str(site.get("source_id") or domain)

    result: dict[str, Any] = {
        "domain": domain,
        "tier": site.get("tier"),
        "item_count": 0,
        "is_fresh": False,
        "is_mirror": False,
        "overlap": 0.0,
        "crawl_ok": False,
    }

    try:
        opts = dict(crawl_posts.CONFIG_DEFAULTS)
        opts["start_urls"] = [start_url]
        opts["source_id"] = source_id
        opts["limit"] = _SAMPLE_LIMIT

        items: list[dict[str, Any]] = crawl_posts.crawl_items(opts, max_runtime_sec=60)

        # 過濾掉 error 標記的 item
        items = [it for it in items if isinstance(it, dict) and "error" not in it]

        item_count = len(items)
        result["item_count"] = item_count
        result["crawl_ok"] = item_count > 0

        # Freshness：取最新 item 的 published_at
        latest_published: str | None = None
        for it in items:
            pub = it.get("published_at")
            if pub:
                if latest_published is None or pub > latest_published:
                    latest_published = pub
        result["is_fresh"] = _is_fresh(latest_published, item_count)

        # 鏡像偵測
        if library_urls:
            overlap = _compute_mirror_overlap(items, library_urls)
            result["overlap"] = overlap
            result["is_mirror"] = overlap > mirror_overlap_threshold

    except Exception as exc:  # noqa: BLE001 - per-site isolation，繼續評估其他站
        logger.warning("站點 %r 爬取失敗：%s", domain, exc)
        result["error"] = str(exc)
        result["crawl_ok"] = False

    return result


# ─── Tier 狀態機 ────────────────────────────────────────────────────────────

def _apply_tier_transition(
    site: dict[str, Any],
    assessment: dict[str, Any],
    roster_path: str,
    dry_run: bool,
) -> str:
    """根據評估結果和目前 tier 執行狀態機轉換，回傳描述字串。

    版本說明：monitored 站點只需 1 次通過即晉升為 ACTIVE。
    """
    domain = str(site["domain"])
    current_tier = str(site.get("tier") or site_roster.CANDIDATE)
    fail_count = int(site.get("fail_count") or 0)
    monitored_ok_count = int(site.get("monitored_ok_count") or 0)
    now_iso = _utcnow_iso()

    item_count = int(assessment.get("item_count", 0))
    is_fresh = bool(assessment.get("is_fresh", False))
    is_mirror = bool(assessment.get("is_mirror", False))
    crawl_ok = bool(assessment.get("crawl_ok", False))

    # 通過條件：item_count ≥ 2 + fresh + 非鏡像
    passed = crawl_ok and item_count >= 2 and is_fresh and not is_mirror

    new_tier = current_tier
    action = ""

    if current_tier == site_roster.CANDIDATE:
        if is_mirror:
            new_tier = site_roster.MIRROR
            action = f"MIRROR（重疊率={assessment.get('overlap', 0):.2f}）"
            fail_count = 0
            monitored_ok_count = 0
            if not dry_run:
                # 更新 is_mirror flag
                with site_roster.connect(roster_path) as conn:
                    conn.execute(
                        "UPDATE sites SET is_mirror = 1 WHERE domain = ?",
                        (domain,),
                    )
                site_roster.set_tier(roster_path, domain, new_tier)
        elif passed:
            new_tier = site_roster.MONITORED
            action = f"candidate → MONITORED（items={item_count}）"
            fail_count = 0
            monitored_ok_count = 0
            if not dry_run:
                site_roster.set_tier(roster_path, domain, new_tier)
        else:
            fail_count += 1
            action = f"失敗（fail_count={fail_count}）"
            if fail_count >= _FAIL_THRESHOLD:
                new_tier = site_roster.FAILED
                action += f" → FAILED"
                if not dry_run:
                    site_roster.set_tier(roster_path, domain, new_tier)

    elif current_tier == site_roster.MONITORED:
        if passed:
            monitored_ok_count += 1
            new_tier = site_roster.ACTIVE
            action = f"monitored → ACTIVE（1 次通過）"
            fail_count = 0
            if not dry_run:
                site_roster.set_tier(roster_path, domain, new_tier)
        else:
            fail_count += 1
            action = f"失敗（fail_count={fail_count}）"
            if fail_count >= _FAIL_THRESHOLD:
                new_tier = site_roster.FAILED
                action += f" → FAILED"
                if not dry_run:
                    site_roster.set_tier(roster_path, domain, new_tier)

    elif current_tier == site_roster.ACTIVE:
        if not passed:
            fail_count += 1
            action = f"失敗（fail_count={fail_count}）"
            if fail_count >= _FAIL_THRESHOLD:
                new_tier = site_roster.INACTIVE
                action += f" → INACTIVE"
                if not dry_run:
                    site_roster.set_tier(roster_path, domain, new_tier)
        else:
            action = "健康（active，保持）"

    else:
        # 其他 tier（mirror / failed / inactive）不在本次評估範圍
        action = f"跳過（tier={current_tier}）"

    # 寫回健康計數（非 dry-run）
    if not dry_run and action != f"跳過（tier={current_tier}）":
        site_roster.update_health(
            roster_path,
            domain,
            fail_count=fail_count,
            monitored_ok_count=monitored_ok_count,
            last_checked_at=now_iso,
        )

    return f"[{domain}] {action}"


# ─── 主流程 ─────────────────────────────────────────────────────────────────

def run(
    roster_path: str,
    library_db: str,
    tiers: list[str],
    dry_run: bool = False,
    mirror_overlap_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """執行健康評估，回傳所有站點的評估結果清單。

    Args:
        roster_path: roster SQLite 路徑
        library_db: library SQLite 路徑（用於鏡像偵測）
        tiers: 要評估的 tier 清單（預設 candidate + monitored）
        dry_run: True 時不寫入 roster
        mirror_overlap_threshold: 覆蓋 scoring.yaml 設定的鏡像閾值
    """
    import os

    threshold = (
        mirror_overlap_threshold
        if mirror_overlap_threshold is not None
        else float(_SCORING_DEFAULTS["mirror_overlap_threshold"])
    )

    # 嘗試載入 scoring.yaml 取得 threshold
    scoring_yaml = "./configs/scoring.yaml"
    if os.path.exists(scoring_yaml) and mirror_overlap_threshold is None:
        try:
            from cpost.core.scoring_config import load as _load_scoring
            cfg = _load_scoring(scoring_yaml)
            threshold = float(cfg.get("mirror_overlap_threshold", threshold))
        except Exception:  # noqa: BLE001
            pass

    # 載入 library canonical URLs（用於鏡像偵測）。
    # _load_library_canonical_urls 內部 try/except 處理 DB 不存在的情況，
    # 回傳空集合並 log warning，不需要在此重複 os.path.exists 判斷。
    library_urls: set[str] = _load_library_canonical_urls(library_db)

    # 收集目標站點
    sites: list[dict[str, Any]] = []
    for tier in tiers:
        sites.extend(site_roster.list_by_tier(roster_path, tier))

    if not sites:
        logger.info("roster 中無符合 tier=%s 的站點", tiers)
        return []

    results: list[dict[str, Any]] = []
    for site in sites:
        domain = str(site.get("domain", "?"))
        current_tier = str(site.get("tier", "?"))
        logger.info("評估站點：%s（tier=%s）", domain, current_tier)

        assessment = _assess_site(site, library_urls, threshold)
        transition_msg = _apply_tier_transition(site, assessment, roster_path, dry_run)

        print(transition_msg)

        results.append({**assessment, "transition": transition_msg})

    return results


# ─── CLI ────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="health-check-sources",
        description="對 roster 中的 candidate/monitored 站點進行健康評估和 tier 轉換。",
    )
    p.add_argument(
        "--roster-path",
        required=True,
        metavar="PATH",
        help="roster SQLite 路徑（必填）",
    )
    p.add_argument(
        "--library-db",
        metavar="PATH",
        default=None,
        help=(
            "library SQLite 路徑，用於鏡像偵測。"
            f"預設：{_DEFAULT_LIBRARY_DB}（或 webui.yaml 的 state_path）"
        ),
    )
    p.add_argument(
        "--tier",
        default="candidate,monitored",
        metavar="TIER[,TIER]",
        help="評估的 tier，逗號分隔（預設：candidate,monitored）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="不寫入 roster，只印評估結果",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    args = _build_parser().parse_args(argv)

    # 解析 tier 清單
    tiers = [t.strip() for t in args.tier.split(",") if t.strip()]

    # library DB 路徑：CLI 優先，其次讀 webui_config DEFAULTS
    library_db = args.library_db
    if library_db is None:
        try:
            from cpost.core.webui_config import DEFAULTS as _WEBUI_DEFAULTS
            library_db = str(_WEBUI_DEFAULTS.get("state_path", _DEFAULT_LIBRARY_DB))
        except Exception:  # noqa: BLE001
            library_db = _DEFAULT_LIBRARY_DB

    results = run(
        roster_path=args.roster_path,
        library_db=library_db,
        tiers=tiers,
        dry_run=args.dry_run,
    )

    print(f"\n評估完成：共 {len(results)} 個站點")
    if args.dry_run:
        print("（dry-run 模式，roster 未更新）")


if __name__ == "__main__":
    main()
