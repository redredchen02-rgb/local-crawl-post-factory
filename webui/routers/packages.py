import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from core import reviewed
from webui._helpers import (
    _filter_packages,
    _move_to_trash,
    _read_failure,
    _safe_pkg_dir,
    _scan_packages,
)
from webui.routers._ctx import cfg_from_request, templates

router = APIRouter()


@router.get("/packages", response_class=HTMLResponse)
def packages(request: Request, q: str = "", status: str = ""):
    cfg = cfg_from_request(request)
    rows = _filter_packages(_scan_packages(cfg["out_dir"]), q, status)
    template = "_packages_table.html" if request.headers.get("HX-Request") else "packages.html"
    return templates.TemplateResponse(
        request, template, {"packages": rows, "q": q, "status": status})


@router.post("/packages/{post_id}/delete", response_class=HTMLResponse)
def delete_package(request: Request, post_id: str):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None:
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    _move_to_trash(cfg["out_dir"], pkg)
    rows = _filter_packages(_scan_packages(cfg["out_dir"]), "", "")
    return templates.TemplateResponse(
        request, "_packages_table.html", {"packages": rows, "q": "", "status": ""})


@router.get("/packages/{post_id}", response_class=HTMLResponse)
def package_detail(request: Request, post_id: str):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None or not (pkg / "manifest.json").exists():
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    # Gate ① : record the review bound to the content version just shown (Q9).
    reviewed.mark(cfg["state_path"], post_id, reviewed.content_id(m))
    caption_file = pkg / "caption.txt"
    caption = caption_file.read_text(encoding="utf-8") if caption_file.exists() else m.get("content", {}).get("body", "")
    has_cover = (pkg / "watermarked_cover.jpg").exists() or (pkg / "cover.jpg").exists()
    return templates.TemplateResponse(request, "detail.html", {
        "post_id": post_id,
        "title": m.get("content", {}).get("title", ""),
        "status": m.get("backend", {}).get("status", "?"),
        "canonical_url": m.get("source", {}).get("canonical_url", ""),
        "caption": caption,
        "has_cover": has_cover,
        "failure": _read_failure(pkg),
        "backend_config": cfg["backend_config"],
    })


@router.post("/packages/{post_id}/edit", response_class=HTMLResponse)
def edit_package(request: Request, post_id: str,
                 title: str = Form(""), caption: str = Form("")):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None or not (pkg / "manifest.json").exists():
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    title = title.strip()
    caption = caption.strip()
    if title:
        m.setdefault("content", {})["title"] = title
    if caption:
        (pkg / "caption.txt").write_text(caption, encoding="utf-8")
    (pkg / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return HTMLResponse('<p class="ok">已儲存 ✓</p>')


@router.get("/packages/{post_id}/failure-image")
def package_failure_image(post_id: str, request: Request):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None:
        return PlainTextResponse("not found", status_code=404)
    failure = _read_failure(pkg)
    shot = failure.get("screenshot") if failure else None
    # Only serve a screenshot that lives inside this package dir (no traversal).
    if shot and Path(shot).resolve().parent == pkg.resolve() and Path(shot).exists():
        return FileResponse(shot)
    return PlainTextResponse("no failure image", status_code=404)


@router.get("/packages/{post_id}/cover")
def package_cover(post_id: str, request: Request):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None:
        return PlainTextResponse("not found", status_code=404)
    for name in ("watermarked_cover.jpg", "cover.jpg"):
        f = pkg / name
        if f.exists():
            return FileResponse(str(f))
    return PlainTextResponse("no cover", status_code=404)
