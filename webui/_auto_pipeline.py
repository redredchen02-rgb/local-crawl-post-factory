"""Auto-pipeline orchestrator: draft → verify → publish for newly built packages."""

import json
import time
from types import SimpleNamespace
from typing import Any

from browser import backend_driver
from core import jobs, runs, reviewed
from core.errors import SessionExpiredError
from src import draft_post, verify_draft, publish_post
from webui._helpers import _safe_pkg_dir


def _action_ns(post_id: str, stage: str, cfg: dict):
    """Build the argparse-style namespace for a backend command from webui cfg."""
    pkg = _safe_pkg_dir(cfg["out_dir"], post_id)
    if pkg is None:
        return None
    return cfg, SimpleNamespace(
        manifest=str(pkg / "manifest.json"),
        backend=cfg["backend_config"],
        storage_state=cfg["storage_state"],
        headless=True,
        timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS,
        retries=None,
        state=cfg["state_path"],
        dry_run=False,
    )


def _retry(fn, times: int = 3, delay: float = 1.0) -> tuple[Any, Exception | None]:
    """Call fn() up to *times* attempts, sleeping *delay* seconds between retries.

    Returns (result, None) on success, (None, last_exception) after all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(times):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < times - 1:
                time.sleep(delay)
    return None, last_exc


def _run_auto_pipeline(job, cfg: dict, built: list[dict], *, note_expiry=None) -> None:
    """Run draft→verify→publish for all *built* packages inside an existing job.

    *built* is the list of dicts from pipeline.run_pipeline()["built"]:
    [{"post_id": str, "title": str, "manifest_path": str}, ...]

    Gate ① (reviewed) is bypassed by calling reviewed.mark() before publish.
    Gates ② (draft_verified status) and ③ (title match) remain enforced.
    Each stage retries up to 3 times with 1s delay on failure.

    *note_expiry* is an optional callable(cfg) invoked on SessionExpiredError.
    """
    if not built:
        jobs.report(job, "無新稿件，跳過自動發布")
        return

    total = len(built)
    run_id = runs.new_run_id()
    drafted_ok: list[dict] = []
    verify_ok: list[dict] = []
    failed: list[dict] = []

    # --- DRAFT LOOP ---
    jobs.report(job, f"自動建草稿（共 {total} 篇）…")
    for i, item in enumerate(built):
        pid = item["post_id"]
        jobs.set_current(job, f"建草稿 {i + 1}/{total}：{item.get('title', pid)}")
        prepared = _action_ns(pid, "draft", cfg)
        if prepared is None:
            failed.append({"post_id": pid, "stage": "draft", "error": "找不到此貼文包"})
            continue
        _, ns = prepared
        _, exc = _retry(lambda ns=ns: draft_post._run(ns))
        if exc is None:
            drafted_ok.append(item)
            runs.record_run(cfg["state_path"], stage="draft", post_id=pid,
                            status="ok", run_id=run_id, severity="info")
        else:
            if note_expiry is not None and isinstance(exc, SessionExpiredError):
                note_expiry(cfg)
            failed.append({"post_id": pid, "stage": "draft", "error": str(exc)})
            runs.record_run(cfg["state_path"], stage="draft", post_id=pid,
                            status="failed", error=str(exc), run_id=run_id, severity="error")

    jobs.report(job, f"建草稿完成：{len(drafted_ok)}/{total} 成功")

    # --- VERIFY LOOP ---
    jobs.report(job, f"自動驗證（共 {len(drafted_ok)} 篇）…")
    for i, item in enumerate(drafted_ok):
        pid = item["post_id"]
        jobs.set_current(job, f"驗證 {i + 1}/{len(drafted_ok)}：{item.get('title', pid)}")
        prepared = _action_ns(pid, "verify", cfg)
        if prepared is None:
            failed.append({"post_id": pid, "stage": "verify", "error": "找不到此貼文包"})
            continue
        _, ns = prepared
        _, exc = _retry(lambda ns=ns: verify_draft._run(ns))
        if exc is None:
            verify_ok.append(item)
            runs.record_run(cfg["state_path"], stage="verify", post_id=pid,
                            status="ok", run_id=run_id, severity="info")
        else:
            if note_expiry is not None and isinstance(exc, SessionExpiredError):
                note_expiry(cfg)
            failed.append({"post_id": pid, "stage": "verify", "error": str(exc)})
            runs.record_run(cfg["state_path"], stage="verify", post_id=pid,
                            status="failed", error=str(exc), run_id=run_id, severity="error")

    verify_fail_count = len(drafted_ok) - len(verify_ok)
    jobs.report(job, f"驗證完成：{len(verify_ok)}/{len(drafted_ok)} 成功")

    # --- PUBLISH LOOP (only verified packages) ---
    jobs.report(job, f"自動發布（共 {len(verify_ok)} 篇）…")
    publish_ok = 0
    for i, item in enumerate(verify_ok):
        pid = item["post_id"]
        jobs.set_current(job, f"發布 {i + 1}/{len(verify_ok)}：{item.get('title', pid)}")
        pkg = _safe_pkg_dir(cfg["out_dir"], pid)
        if pkg is None or not (pkg / "manifest.json").exists():
            failed.append({"post_id": pid, "stage": "publish", "error": "找不到 manifest"})
            continue
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        cid = reviewed.content_id(m)
        # Bypass Gate ① by marking as reviewed before calling publish.
        # Gates ② (draft_verified) and ③ (title match) remain enforced inside publish_post._run.
        try:
            reviewed.mark(cfg["state_path"], pid, cid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"post_id": pid, "stage": "publish", "error": f"reviewed.mark 失敗：{exc}"})
            continue
        ns = SimpleNamespace(
            manifest=str(pkg / "manifest.json"),
            backend=cfg["backend_config"],
            storage_state=cfg["storage_state"],
            headless=True,
            timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS,
            retries=None,
            state=cfg["state_path"],
            approve=True,
            expected_content_id=cid,
        )
        _, err = _retry(lambda ns=ns: publish_post._run(ns))
        if err is None:
            publish_ok += 1
        else:
            if note_expiry is not None and isinstance(err, SessionExpiredError):
                note_expiry(cfg)
            failed.append({"post_id": pid, "stage": "publish", "error": str(err)})
            runs.record_run(cfg["state_path"], stage="publish", post_id=pid,
                            status="failed", error=str(err), run_id=run_id, severity="error")

    jobs.report(
        job,
        f"自動發布完成：成功 {publish_ok} / 失敗 {len(failed)} / "
        f"跳過 {verify_fail_count}（驗證失敗 {verify_fail_count}）"
    )
