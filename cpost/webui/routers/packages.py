import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from cpost.core import llm, manifest as mf, reviewed, state as state_mod
from cpost.core.errors import CliError
from cpost.core.filesystem import atomic_write_text
from cpost.webui._helpers import (
    _filter_packages,
    _move_to_trash,
    _read_failure,
    _safe_pkg_dir,
    _scan_packages,
)
from cpost.webui.routers._ctx import cfg_from_request, templates

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
    receipt = None
    receipt_path = pkg / "publish_receipt.json"
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return templates.TemplateResponse(request, "detail.html", {
        "post_id": post_id,
        "title": m.get("content", {}).get("title", ""),
        "status": m.get("backend", {}).get("status", "?"),
        "source_id": m.get("source", {}).get("source_id") or "—",
        "canonical_url": m.get("source", {}).get("canonical_url", ""),
        "caption": caption,
        "failure": _read_failure(pkg),
        "receipt": receipt,
        "backend_config": cfg["backend_config"],
    })


@router.post("/packages/{post_id}/edit", response_class=HTMLResponse)
def edit_package(request: Request, post_id: str,
                 title: str = Form(""), caption: str = Form("")):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None or not (pkg / "manifest.json").exists():
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    title = title.strip()
    caption = caption.strip()
    if not title and not caption:
        return HTMLResponse('<p class="error">標題與文案不可同時為空</p>', status_code=400)
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    if title:
        m.setdefault("content", {})["title"] = title
    if caption:
        # content.body is the publish source; caption.txt is display only.
        # Mirror generate_article: manifest-first so publish truth is never stale.
        m.setdefault("content", {})["body"] = caption
    # 1. Anchor write: manifest.json is the publish source of truth.
    atomic_write_text(pkg / "manifest.json",
                      json.dumps(m, ensure_ascii=False, indent=2))
    # 2. Display write: caption.txt. On failure, roll back manifest body so the
    #    two stores stay consistent (old body or both-new, never diverged).
    if caption:
        try:
            atomic_write_text(pkg / "caption.txt", caption)
        except Exception:
            old_body = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
            old_body.setdefault("content", {}).pop("body", None)
            atomic_write_text(pkg / "manifest.json",
                              json.dumps(old_body, ensure_ascii=False, indent=2))
            raise
    # 3. Reviewed marker: content_id is derived from the now-landed manifest body.
    reviewed.mark(cfg["state_path"], post_id, reviewed.content_id(m))
    return HTMLResponse('<p class="ok">已儲存 ✓</p>')


@router.post("/packages/{post_id}/generate", response_class=HTMLResponse)
def generate_article(request: Request, post_id: str):
    """Rewrite the crawled material into an article via the LLM (custom prompt).

    Runs synchronously: FastAPI serves sync routes from a threadpool, so the LLM
    latency never blocks the event loop. The result replaces both caption.txt
    (shown/edited in the UI) and manifest content.body (what publishing uses), so
    the displayed文案 and the publishable body stay in sync.
    """
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None or not (pkg / "manifest.json").exists():
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    title = m.get("content", {}).get("title", "")
    # Prefer the full crawled body (source_text.txt); fall back to the caption.
    source_file = pkg / "source_text.txt"
    if source_file.exists():
        material = source_file.read_text(encoding="utf-8")
    else:
        caption_file = pkg / "caption.txt"
        material = (caption_file.read_text(encoding="utf-8") if caption_file.exists()
                    else m.get("content", {}).get("body", ""))
    if not material.strip():
        return HTMLResponse(
            '<p class="error">此貼文沒有可用素材（source_text 與文案皆空）</p>',
            status_code=400)
    try:
        llm_cfg = llm.load_config(cfg["llm_config"])
        article = llm.chat(llm_cfg, llm.load_system_prompt(llm_cfg),
                           llm.build_user_content(title, material))
    except CliError as exc:
        return HTMLResponse(f'<p class="error">生成失敗：{exc.message}</p>', status_code=502)
    # Dual-write caption.txt (displayed) + manifest content.body (published) so
    # the two never permanently diverge. Each write is atomic; the manifest goes
    # first and, if the second (caption) write fails, the body is rolled back to
    # its previous value so the pair stays consistent (both old, never half-new).
    prev_body = m.get("content", {}).get("body", "")
    m.setdefault("content", {})["body"] = article
    atomic_write_text(pkg / "manifest.json",
                      json.dumps(m, ensure_ascii=False, indent=2))
    try:
        atomic_write_text(pkg / "caption.txt", article)
    except BaseException:
        m.setdefault("content", {})["body"] = prev_body
        atomic_write_text(pkg / "manifest.json",
                          json.dumps(m, ensure_ascii=False, indent=2))
        raise
    return HTMLResponse('<p class="ok">AI 生成完成 ✓ 即將刷新顯示新文案</p>')


@router.get("/packages/{post_id}/failure-image")
def package_failure_image(post_id: str, request: Request):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None:
        return PlainTextResponse("not found", status_code=404)
    failure = _read_failure(pkg)
    shot = failure.get("screenshot") if failure else None
    if shot:
        # A relative screenshot is stored relative to the package dir; resolve it
        # there BEFORE the traversal guard so an in-package relative path is
        # served (not 404). Absolute paths are taken as-is.
        candidate = (Path(shot).resolve() if Path(shot).is_absolute()
                     else (pkg / shot).resolve())
        # Only serve a screenshot that lives inside this package dir (no traversal).
        if candidate.parent == pkg.resolve() and candidate.exists():
            return FileResponse(str(candidate))
    return PlainTextResponse("no failure image", status_code=404)


@router.post("/packages/{post_id}/rollback", response_class=HTMLResponse)
def rollback_package(request: Request, post_id: str):
    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None or not (pkg / "manifest.json").exists():
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    manifest = mf.load(str(pkg / "manifest.json"))
    if manifest.get("backend", {}).get("status") != "published":
        return HTMLResponse('<p class="error">只有已發布的貼文可以 rollback</p>', status_code=400)
    mf.set_backend(manifest, status="draft_verified", published_url=None)
    mf.save(str(pkg / "manifest.json"), manifest)
    receipt_path = pkg / "publish_receipt.json"
    if receipt_path.exists():
        receipt_path.unlink()
    # B1 rollback fix: clear the state row so re-publishing does a real browser click
    # rather than forward-completing on the stale 'published' state row.
    state_path = cfg.get("state_path")
    canonical_url = (manifest.get("source") or {}).get("canonical_url")
    if state_path and canonical_url:
        with state_mod.connect(state_path) as conn:
            state_mod.reset_for_republish(conn, canonical_url)
    return HTMLResponse('<p class="ok">已回退至已驗證狀態，可重新發布</p>')
