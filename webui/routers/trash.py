import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webui._helpers import _restore_from_trash, _scan_trash
from webui.routers._ctx import cfg_from_request, templates

router = APIRouter()


@router.get("/trash", response_class=HTMLResponse)
def trash_list(request: Request):
    cfg = cfg_from_request(request)
    rows = _scan_trash(cfg["out_dir"])
    return templates.TemplateResponse(request, "trash.html", {"items": rows})


@router.post("/trash/{post_id}/restore", response_class=HTMLResponse)
def restore_package(request: Request, post_id: str):
    cfg = cfg_from_request(request)
    result = _restore_from_trash(cfg["out_dir"], post_id)
    if result == "not_found":
        return HTMLResponse('<p class="error">找不到此垃圾桶項目</p>', status_code=404)
    if result == "conflict":
        return HTMLResponse('<p class="error">上膛清單已有同名貼文，無法復原</p>', status_code=409)
    rows = _scan_trash(cfg["out_dir"])
    return templates.TemplateResponse(request, "_trash_table.html",
                                      {"items": rows, "msg": f"已復原：{post_id}",
                                       "msg_class": "ok"})


@router.post("/trash/empty", response_class=HTMLResponse)
def empty_trash(request: Request):
    cfg = cfg_from_request(request)
    trash = Path(cfg["out_dir"]) / ".trash"
    if trash.exists():
        shutil.rmtree(trash)
    return templates.TemplateResponse(request, "_trash_table.html",
                                      {"items": [], "msg": "垃圾桶已清空",
                                       "msg_class": "ok"})
