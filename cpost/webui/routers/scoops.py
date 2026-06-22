"""WebUI /today: the single-page 今日備稿 workspace (plan 006 U2/U5).

Hosts the three operator stages on one page: 開始備稿 (prep job) -> scoop list
(sort/filter/multi-select) -> 生成選取 (generation job). Reuses the in-process
``jobs`` registry; localhost single-user, no auth (same shape as the existing
routers). Prep and generation get their own ``_today_job.html`` status view +
``/today/jobs/{id}`` poll endpoint so the shared ``_job_status.html`` (which only
knows the template-repost result shapes) stays untouched.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from cpost.core import jobs, library, scoop_pipeline
from cpost.webui.routers._ctx import cfg_from_request, templates

router = APIRouter()


def _scoops(cfg: dict, min_confidence: int, min_score: float) -> tuple[list[dict], bool]:
    """Return (rows, single_source): score-sorted clusters, filtered, with sources.

    ``min_confidence`` filters on source_count -- "appeared in >=N distinct-canonical
    sources" (best-effort, INFORMATIONAL, NOT corroboration: mirrors sharing a
    canonical_url collapse to 1). Default 0 never empties a single-source library.
    ``min_score`` gates the combined score, which is quality-only (the confidence
    axis is neutralized via ``weight_confidence: 0.0``). ``single_source`` reflects
    the *whole* library (pre-filter) so the UI can flag that source_count carries
    no real distinguishing power yet.
    """
    with library.connect(cfg["state_path"]) as conn:
        clusters = library.list_clusters(conn, by_score=True)
        rows = []
        for c in clusters:
            if (c.get("source_count") or 0) < min_confidence:
                continue
            if (c.get("score") or 0) < min_score:
                continue
            members = library.get_cluster_members(conn, c["cluster_id"])
            c["sources"] = sorted({m.get("source_id") for m in members if m.get("source_id")})
            rows.append(c)
    single_source = bool(clusters) and all((c.get("source_count") or 0) <= 1 for c in clusters)
    return rows, single_source


def _list_ctx(cfg: dict, min_confidence: int, min_score: float) -> dict:
    rows, single_source = _scoops(cfg, min_confidence, min_score)
    return {"rows": rows, "single_source": single_source,
            "min_confidence": min_confidence, "min_score": min_score}


@router.get("/today", response_class=HTMLResponse)
def today(request: Request):
    cfg = cfg_from_request(request)
    ctx = _list_ctx(cfg, int(cfg.get("min_confidence", 0)), float(cfg.get("min_score", 0.0)))
    return templates.TemplateResponse(request, "today.html", ctx)


@router.get("/today/list", response_class=HTMLResponse)
def today_list(request: Request, min_confidence: int = 0, min_score: float = 0.0):
    cfg = cfg_from_request(request)
    ctx = _list_ctx(cfg, min_confidence, min_score)
    return templates.TemplateResponse(request, "_scoop_list.html", ctx)


@router.post("/today/prep", response_class=HTMLResponse)
def start_prep(request: Request):
    cfg = cfg_from_request(request)

    def _work(job):
        jobs.set_current(job, "準備備稿…")

        def _crawl_cb(snap):
            # Mirror crawl.py:_crawl_cb — map the dict snapshot to a live status
            # line. Routing these dicts through progress_cb (jobs.report) would
            # append raw dict reprs to the log (U18).
            parts = [f"爬取進度 {snap['responses']} 頁"]
            if snap.get("last_title"):
                parts.append(snap["last_title"])
            jobs.set_current(job, " — ".join(parts))

        result = scoop_pipeline.run_prep_pipeline(
            cfg, progress_cb=lambda m: jobs.report(job, m),
            on_source=lambda sid, r: jobs.report(job, f"來源 {sid}：{r}"),
            crawl_progress_cb=_crawl_cb)
        return {**result, "kind": "prep"}

    job_id = jobs.submit(_work)
    return templates.TemplateResponse(
        request, "_today_job.html", {"job": jobs.get(job_id), "job_id": job_id})


@router.get("/today/jobs/{job_id}", response_class=HTMLResponse)
def today_job(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return HTMLResponse('<p class="error">job not found</p>', status_code=404)
    return templates.TemplateResponse(
        request, "_today_job.html", {"job": job, "job_id": job_id})


@router.post("/today/generate", response_class=HTMLResponse)
def start_generate(request: Request, cluster_ids: list[str] = Form(default=[])):
    if not cluster_ids:
        return HTMLResponse('<p class="error">請先勾選至少一個瓜</p>', status_code=400)
    cfg = cfg_from_request(request)

    def _work(job):
        jobs.set_current(job, "生成中…")
        return scoop_pipeline.run_generation_pipeline(
            cluster_ids, cfg, progress_cb=lambda m: jobs.report(job, m))

    job_id = jobs.submit(_work)
    return templates.TemplateResponse(
        request, "_today_job.html", {"job": jobs.get(job_id), "job_id": job_id})
