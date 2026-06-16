import html

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from core import runs
from core.errors import SessionExpiredError
from src import draft_post, verify_draft
from webui._auto_pipeline import _action_ns
from webui._helpers import _filter_packages, _safe_pkg_dir, _scan_packages
from webui.routers._ctx import (
    cfg_from_request,
    note_session_expiry,
    submit_action,
    submit_job,
    templates,
)

router = APIRouter()


@router.post("/packages/{post_id}/draft", response_class=HTMLResponse)
def action_draft(request: Request, post_id: str):
    cfg = cfg_from_request(request)
    return submit_action(request, "draft", post_id, _action_ns(post_id, "draft", cfg))


@router.post("/packages/{post_id}/verify", response_class=HTMLResponse)
def action_verify(request: Request, post_id: str):
    cfg = cfg_from_request(request)
    return submit_action(request, "verify", post_id, _action_ns(post_id, "verify", cfg))


@router.post("/packages/{post_id}/publish", response_class=HTMLResponse)
def action_publish(request: Request, post_id: str, title: str = Form("")):
    import json
    from core import reviewed
    from webui.app import check_publish_gates

    cfg = cfg_from_request(request)
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None or not (pkg / "manifest.json").exists():
        return HTMLResponse('<p class="error">找不到此貼文包</p>', status_code=404)
    # R6 三重閘門（順序固定 ①→②→③）—— 純決策抽到 check_publish_gates 便於單測。
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    stored_cid = reviewed.get(cfg["state_path"], post_id)
    msg = check_publish_gates(
        stored_cid, reviewed.content_id(m),
        m.get("backend", {}).get("status"),
        title, m.get("content", {}).get("title"))
    if msg:
        return HTMLResponse(f'<p class="error">{msg}</p>', status_code=400)

    from types import SimpleNamespace
    from browser import backend_driver
    from src import publish_post
    ns = SimpleNamespace(
        manifest=str(pkg / "manifest.json"), backend=cfg["backend_config"],
        storage_state=cfg["storage_state"], headless=True,
        timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS, retries=None,
        state=cfg["state_path"], approve=True,
        expected_content_id=stored_cid)
    return submit_job(request, "publish", post_id, cfg, lambda: publish_post._run(ns))


@router.post("/batch/delete", response_class=HTMLResponse)
def batch_delete(request: Request, post_ids: list[str] = Form(default=[])):
    """Move selected packages to .trash — reversible bulk delete."""
    if not post_ids:
        return HTMLResponse('<p class="hint">未選取任何貼文。</p>')
    cfg = cfg_from_request(request)
    from webui._helpers import _move_to_trash
    deleted, skipped = [], []
    for pid in post_ids:
        pkg = _safe_pkg_dir(cfg["out_dir"], pid)
        if pkg is None:
            skipped.append(pid)
            continue
        _move_to_trash(cfg["out_dir"], pkg)
        deleted.append(pid)
    rows = _filter_packages(_scan_packages(cfg["out_dir"]), "", "")
    msg = f'<p class="ok">已移入垃圾桶：{len(deleted)} 篇</p>'
    if skipped:
        escaped = ", ".join(html.escape(p) for p in skipped)
        msg += f'<p class="error">找不到（已略過）：{escaped}</p>'
    return msg + templates.TemplateResponse(
        request, "_packages_table.html", {"packages": rows, "q": "", "status": ""}
    ).body.decode()


@router.post("/batch/{stage}", response_class=HTMLResponse)
def batch_action(request: Request, stage: str, post_ids: list[str] = Form(default=[])):
    """Batch draft/verify over selected packages (R8).

    Publish is intentionally NOT batchable — its review/title gates are
    per-item by design (see plan U8). Each item reuses the single-item
    backend command; one failure never aborts the rest; all share one run_id.
    """
    if stage not in ("draft", "verify"):
        return HTMLResponse('<p class="error">不支援的批量動作</p>', status_code=400)
    if not post_ids:
        return HTMLResponse('<p class="hint">未選取任何貼文。</p>')
    cfg = cfg_from_request(request)
    run_id = runs.new_run_id()
    runner = {"draft": draft_post._run, "verify": verify_draft._run}[stage]

    def _work(job):
        from core import jobs as _jobs
        label = {"draft": "建草稿", "verify": "驗證"}.get(stage, stage)
        _jobs.set_current(job, f"批量{label}中…")
        _jobs.report(job, f"開始批量{label}，共 {len(post_ids)} 篇")
        ok, failed = [], []
        for i, pid in enumerate(post_ids):
            _jobs.set_current(job, f"批量{label}中…（{i + 1}/{len(post_ids)}）")
            prepared = _action_ns(pid, stage, cfg)
            if prepared is None:  # invalid / traversal post_id -> skip, isolate
                failed.append({"post_id": pid, "error": "找不到此貼文包"})
                runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                status="failed", error="invalid post_id",
                                run_id=run_id, severity="error")
                continue
            _, ns = prepared
            try:
                runner(ns)
                ok.append(pid)
                runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                status="ok", run_id=run_id, severity="info")
            except Exception as exc:  # noqa: BLE001 - isolate per item
                if isinstance(exc, SessionExpiredError):
                    note_session_expiry(request, cfg)
                failed.append({"post_id": pid, "error": str(exc)})
                runs.record_run(cfg["state_path"], stage=stage, post_id=pid,
                                status="failed", error=str(exc), run_id=run_id,
                                severity="warning" if isinstance(exc, SessionExpiredError)
                                else "error")
        return {"stage": stage, "batch": True, "run_id": run_id,
                "ok": ok, "failed": failed}

    from core import jobs as _jobs
    job_id = _jobs.submit(_work)
    return templates.TemplateResponse(
        request, "_job_status.html", {"job": _jobs.get(job_id), "job_id": job_id})
