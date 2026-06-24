"""Generation-track orchestration: today-prep (+ scoop generation, plan 006).

Kept separate from ``cpost.core.pipeline`` (the template-repost track) so the two WebUI
entry points stay decoupled. Reuses ``crawl_all_sources`` from ``cpost.core.pipeline``
and the already-built library/cluster/scoring stages in-process (no shell-out),
mirroring ``run_pipeline``'s per-item isolation: one bad item never aborts the
batch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from cpost.core import library, llm, scoring_config
from cpost.core.pipeline import crawl_all_sources
from cpost.core.schema import (
    GenerationBuilt,
    GenerationPipelineResult,
    PrepPipelineResult,
    PrepTopScoop,
    ScoopFailed,
)
from cpost.cli import (
    build_manifest,
    cluster_scoops,
    generate_article,
    library_ingest,
    normalize_items,
    score_scoops,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_prep_pipeline(webui_cfg: dict,
                      progress_cb: Callable[[str], object] | None = None,
                      on_source: Callable[[str, object], object] | None = None,
                      crawl_progress_cb: Callable[[dict], object] | None = None,
                      ) -> PrepPipelineResult:
    """Crawl (multi-source) → normalize → library-ingest → cluster → score.

    Produces ranked scoops in the library for the ``/today`` selection page,
    wiring the generation-track data-prep that plan 004 U2 deferred. Returns a
    summary dict for the job done view. A single bad item is recorded under
    ``failed`` and never aborts the batch. ``on_source`` is threaded into
    :func:`crawl_all_sources` so per-source failures on the ``/today`` path are
    visible instead of silently swallowed (flow G3).

    ``progress_cb`` carries human-readable *stage* reports (string). The
    realtime crawl telemetry from :func:`crawl_all_sources` is dict-shaped
    (``{responses, items, last_url, last_title}`` snapshots), so it travels on a
    *separate* ``crawl_progress_cb`` -- routing it through ``progress_cb`` would
    append stringified dicts to the job log (U18). When ``crawl_progress_cb`` is
    None the crawl phase emits no live telemetry; stage reports still flow.
    """
    def _report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    now = _utcnow()
    cfg = scoring_config.load(webui_cfg.get("scoring_config"))

    # crawl_all_sources' progress_cb is annotated Callable[[str], ...] but fires
    # dict snapshots into it (it threads it straight into crawl_items). Our
    # crawl_progress_cb is the honestly-typed dict callback; cast to bridge the
    # upstream annotation without widening it here.
    raw = crawl_all_sources(
        webui_cfg,
        progress_cb=cast("Callable[[str], object] | None", crawl_progress_cb),
        on_source=on_source)
    _report(f"爬取完成：{len(raw)} 篇")

    normalized: list[dict] = []
    failed: list[ScoopFailed] = []
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
    top: list[PrepTopScoop] = [
        {"cluster_id": s["cluster_id"],
         "representative_title": s.get("representative_title"),
         "source_count": s["source_count"],
         "confidence": round(s["confidence"], 4),
         "quality": round(s["quality"], 4),
         "score": round(s["score"], 4),
         "score_legacy": round(s.get("score_legacy", s["score"]), 4),
         "freshness": round(s.get("freshness", 0), 4),
         "importance": round(s.get("importance", 0), 4),
         "traffic_potential": round(s.get("traffic_potential", 0), 4),
         "cross_site_coverage": round(s.get("cross_site_coverage", 0), 4),
         "external_article_count": s.get("external_article_count"),
         "external_source_count": s.get("external_source_count"),
         "external_latest_at": s.get("external_latest_at"),
         "search_volume_proxy": s.get("search_volume_proxy"),
         }
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


def run_generation_pipeline(selected_cluster_ids: list[str], webui_cfg: dict,
                            progress_cb: Callable[[str], object] | None = None) -> GenerationPipelineResult:
    """Generate one original article per selected scoop and build its package.

    Each selected cluster -> generate-article (synthetic item, body carried in
    ``caption``) -> build-manifest (-> ``out/<post_id>/``, content.body = caption).
    Per-cluster isolation: a failed scoop is recorded under ``failed`` and never
    aborts the rest. Packages land in ``package_built`` and flow into the existing
    packages console for the manual triple-gate publish -- the generation track
    never auto-publishes and never bypasses the review gates.
    """
    def _report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    now = _utcnow()
    llm_cfg = llm.load_config(webui_cfg["llm_config"])
    prompt = Path(webui_cfg["scoop_prompt"]).read_text(encoding="utf-8")
    out_dir = webui_cfg["out_dir"]
    audit_log = webui_cfg["audit_log"]

    built: list[GenerationBuilt] = []
    failed: list[ScoopFailed] = []
    with library.connect(webui_cfg["state_path"]) as conn:
        for cid in selected_cluster_ids:
            try:
                item = generate_article.generate(conn, cid, llm_cfg, prompt, now)
                manifest_path = build_manifest.build(item, out_dir, audit_log)
                post_id = Path(manifest_path).parent.name
                built.append({"post_id": post_id, "title": item["title"]})
                _report(f"生成 {post_id}")
            except Exception as exc:  # noqa: BLE001 - isolate, record, keep going
                # cpost.core.llm exceptions carry the upstream response body, never the
                # Authorization header, so str(exc) is safe to surface to the UI.
                failed.append({"cluster_id": cid, "stage": "generate", "error": str(exc)})
                _report(f"失敗 {cid}：{exc}")
    return {"built": built, "failed": failed, "kind": "generate"}
