"""Generation-track orchestration: today-prep (+ scoop generation, plan 006).

Kept separate from ``core.pipeline`` (the template-repost track) so the two WebUI
entry points stay decoupled. Reuses ``crawl_all_sources`` from ``core.pipeline``
and the already-built library/cluster/scoring stages in-process (no shell-out),
mirroring ``run_pipeline``'s per-item isolation: one bad item never aborts the
batch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from core import library, scoring_config
from core.pipeline import crawl_all_sources
from src import cluster_scoops, library_ingest, normalize_items, score_scoops


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_prep_pipeline(webui_cfg: dict,
                      progress_cb: Callable[[str], object] | None = None) -> dict:
    """Crawl (multi-source) → normalize → library-ingest → cluster → score.

    Produces ranked scoops in the library for the ``/today`` selection page,
    wiring the generation-track data-prep that plan 004 U2 deferred. Returns a
    summary dict for the job done view. A single bad item is recorded under
    ``failed`` and never aborts the batch.
    """
    def _report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    now = _utcnow()
    cfg = scoring_config.load(webui_cfg.get("scoring_config"))

    raw = crawl_all_sources(webui_cfg, progress_cb=progress_cb)
    _report(f"爬取完成：{len(raw)} 篇")

    normalized: list[dict] = []
    failed: list[dict] = []
    for item in raw:
        try:
            normalized.append(normalize_items.normalize_one(item))
        except Exception as exc:  # noqa: BLE001 - record and keep the batch alive
            failed.append({"stage": "normalize", "error": str(exc)})
    _report(f"正規化 {len(normalized)} 篇")

    with library.connect(webui_cfg["state_path"]) as conn:
        # ingest() is a generator: consume it so the upserts actually run.
        ingested = list(library_ingest.ingest(normalized, conn, now))
        _report(f"落庫 {len(ingested)} 筆")
        clusters = cluster_scoops.cluster_library(conn, cfg, now)
        _report(f"聚成 {len(clusters)} 個瓜")
        scored = score_scoops.score_all(conn, cfg, now)
        _report("打分完成")

    single_source = bool(scored) and all(s["source_count"] <= 1 for s in scored)
    top = [
        {"cluster_id": s["cluster_id"],
         "representative_title": s.get("representative_title"),
         "source_count": s["source_count"],
         "confidence": round(s["confidence"], 4),
         "quality": round(s["quality"], 4),
         "score": round(s["score"], 4)}
        for s in scored[:10]
    ]
    return {
        "ingested": len(ingested),
        "clusters": len(clusters),
        "scored": len(scored),
        "single_source": single_source,
        "top": top,
        "failed": failed,
    }
