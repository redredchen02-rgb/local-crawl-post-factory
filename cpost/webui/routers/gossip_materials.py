"""WebUI /gossip-materials: user URL submission + on-demand crawl + intersection view."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from cpost.core import gossip_crawl, jobs, library, validators
from cpost.webui.routers._ctx import cfg_from_request, templates

router = APIRouter()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _intersection_rows(cfg: dict) -> list[dict]:
    """Return intersection clusters with sources attached (mirrors _scoops pattern)."""
    with library.connect(cfg["state_path"]) as conn:
        clusters = library.list_intersection_clusters(conn)
        for c in clusters:
            members = library.get_cluster_members(conn, c["cluster_id"])
            c["sources"] = sorted({m.get("source_id") for m in members
                                    if m.get("source_id")})
    return clusters


@router.get("/gossip-materials", response_class=HTMLResponse)
def gossip_materials(request: Request):
    cfg = cfg_from_request(request)
    with library.connect(cfg["state_path"]) as conn:
        gossip_list = library.list_gossip_urls(conn)
    intersection = _intersection_rows(cfg)
    return templates.TemplateResponse(
        request, "gossip_materials.html",
        {"gossip_list": gossip_list, "intersection_clusters": intersection})


@router.post("/gossip-materials/crawl", response_class=HTMLResponse)
def start_gossip_crawl(request: Request, url: str = Form(...),
                       label: str = Form(default="")):
    url = url.strip()
    cfg = cfg_from_request(request)

    if not validators.valid_url(url):
        return HTMLResponse('<p class="error">請輸入有效的 http/https URL</p>',
                            status_code=400)

    hostname = urlparse(url).hostname or ""
    if not validators.is_safe_external_host(hostname):
        return HTMLResponse('<p class="error">URL 指向私有或無法解析的主機，已拒絕</p>',
                            status_code=400)

    now = _utcnow()
    with library.connect(cfg["state_path"]) as conn:
        library.submit_gossip_url(conn, url, label or None, now)

    def _work(job):
        jobs.set_current(job, f"爬取中：{url}")
        result = gossip_crawl.crawl_url(
            url, cfg,
            progress_cb=lambda m: jobs.report(job, m),
            now=_utcnow())
        return {**result, "kind": "gossip", "url": url}

    job_id = jobs.submit(_work)
    return templates.TemplateResponse(
        request, "_gossip_job.html",
        {"job": jobs.get(job_id), "job_id": job_id})


@router.post("/gossip-materials/delete", response_class=HTMLResponse)
def delete_gossip_url_route(request: Request, url: str = Form(...)):
    cfg = cfg_from_request(request)
    with library.connect(cfg["state_path"]) as conn:
        library.delete_gossip_url(conn, url)
    return HTMLResponse("", status_code=200)


@router.get("/gossip-materials/jobs/{job_id}", response_class=HTMLResponse)
def gossip_job(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return HTMLResponse('<p class="error">job not found</p>', status_code=404)
    return templates.TemplateResponse(
        request, "_gossip_job.html", {"job": job, "job_id": job_id})
