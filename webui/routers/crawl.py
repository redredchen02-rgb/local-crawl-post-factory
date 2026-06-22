from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core import jobs, pipeline
from core.errors import ValidationError
from webui._auto_pipeline import _run_auto_pipeline
from webui.routers._ctx import cfg_from_request, note_session_expiry, templates

router = APIRouter()


@router.post("/crawl", response_class=HTMLResponse)
def start_crawl(request: Request):
    cfg = cfg_from_request(request)
    # crawl_all_sources collapses "no sources" and "all disabled" into the same
    # crawl-or-empty path without telling the caller which, so decide the
    # precondition here. enabled_sources() also validates the sources shape, so a
    # malformed config yields a clean 400 here instead of a 500 in the job.
    try:
        enabled = pipeline.enabled_sources(cfg)
    except ValidationError as e:
        return HTMLResponse(
            f'<p class="error">設定錯誤：{e}</p>', status_code=400)
    if not enabled and not cfg.get("start_url"):
        if cfg.get("sources"):  # sources exist but every one is disabled
            return HTMLResponse('<p class="error">所有來源都已停用</p>', status_code=400)
        return HTMLResponse(
            '<p class="error">請先在設定新增至少一個來源</p>', status_code=400)

    def _work(job):
        jobs.set_current(job, "準備爬取…")
        jobs.report(job, "爬取中…")

        def _crawl_cb(snap):
            parts = [f"爬取進度 {snap['responses']} 頁"]
            if snap.get("last_title"):
                parts.append(snap["last_title"])
            jobs.set_current(job, " — ".join(parts))

        items = pipeline.crawl_all_sources(
            cfg, progress_cb=_crawl_cb,
            on_source=lambda sid, r: jobs.report(job, f"來源 {sid}：{r}"))
        jobs.report(job, f"爬取完成：{len(items)} 篇")
        jobs.set_current(job, "建包中…")
        result = pipeline.run_pipeline(items, cfg, progress_cb=lambda m: jobs.report(job, m))
        if cfg.get("auto_pipeline"):
            result["auto_pipeline"] = _run_auto_pipeline(
                job, cfg, result.get("built", []),
                note_expiry=lambda c: note_session_expiry(request, c),
            )
        return result

    job_id = jobs.submit(_work)
    return templates.TemplateResponse(
        request, "_job_status.html", {"job": jobs.get(job_id), "job_id": job_id})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return HTMLResponse('<p class="error">job not found</p>', status_code=404)
    return templates.TemplateResponse(
        request, "_job_status.html", {"job": job, "job_id": job_id})
