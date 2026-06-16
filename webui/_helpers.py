"""Pure I/O helpers for the webui — no FastAPI or app-state dependencies."""

import json
import shutil
from pathlib import Path

__all__ = [
    "_safe_pkg_dir",
    "_scan_packages",
    "_filter_packages",
    "_read_failure",
    "_tail_audit",
    "_move_to_trash",
    "_scan_trash",
    "_restore_from_trash",
    "check_publish_gates",
]


def check_publish_gates(stored_cid, current_cid, status, submitted_title, manifest_title):
    """Pure publish-gate decision (R6/Q9). Returns a rejection message, or None
    if all three gates pass. Order is fixed and security-critical:
    ① reviewed AND content unchanged (fail-closed) → ② draft_verified → ③ title.
    """
    if stored_cid is None or stored_cid != current_cid:
        return "請先開啟審核頁再發布（或內容已變更，需重新審核）"
    if status != "draft_verified":
        return "尚未驗證，不可發布"
    if (submitted_title or "").strip() != (manifest_title or "").strip():
        return "標題不符，發布取消"
    return None


def _safe_pkg_dir(out_dir: str, post_id: str):
    """Resolve out_dir/post_id, rejecting path traversal and dot-dirs (e.g. .trash)."""
    if not post_id or post_id.startswith(".") or "/" in post_id or "\\" in post_id or ".." in post_id:
        return None
    base = Path(out_dir).resolve()
    target = (base / post_id).resolve()
    if target.parent != base or not target.is_dir():
        return None
    return target


def _scan_packages(out_dir: str):
    """Read every out/<post_id>/manifest.json; skip broken ones."""
    rows: list[dict] = []
    base = Path(out_dir)
    if not base.exists():
        return rows
    for manifest_path in sorted(base.glob("*/manifest.json")):
        if manifest_path.parent.name.startswith("."):
            continue  # skip .trash and other dot dirs
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rows.append({"post_id": manifest_path.parent.name, "title": "(壞掉的 manifest)",
                         "status": "error", "broken": True})
            continue
        rows.append({
            "post_id": m.get("post_id", manifest_path.parent.name),
            "title": m.get("content", {}).get("title", ""),
            "status": m.get("backend", {}).get("status", "?"),
            "broken": False,
        })
    return rows


def _filter_packages(rows, q: str, status: str):
    """Filter scanned packages by case-insensitive query (title or post_id) and status.

    status="" (default) hides published packages so the list stays actionable.
    status="all" shows everything including published.
    Any other value filters to that exact status.
    """
    q = (q or "").strip().lower()
    status = (status or "").strip()
    out = rows
    if status == "all":
        pass  # show everything
    elif status:
        out = [r for r in out if r.get("status") == status]
    else:
        out = [r for r in out if r.get("status") != "published"]
    if q:
        out = [r for r in out
               if q in str(r.get("title", "")).lower() or q in str(r.get("post_id", "")).lower()]
    return out


def _read_failure(pkg):
    """Return the latest failure.json contents for a package, or None."""
    f = Path(pkg) / "failure.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _tail_audit(audit_log: str, limit: int):
    """Return the last ``limit`` parsed audit lines (newest first); skip bad lines.

    Reads at most 64 KB from the end of the file so large logs don't bloat memory.
    """
    p = Path(audit_log)
    if not p.exists():
        return []
    size = p.stat().st_size
    chunk = 65536  # 64 KB — enough for ~200 typical audit lines
    with p.open("rb") as f:
        f.seek(max(0, size - chunk))
        tail = f.read().decode("utf-8", errors="replace")
    # drop the first (possibly partial) line when we didn't start from offset 0
    lines = tail.splitlines()
    if size > chunk:
        lines = lines[1:]
    parsed = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return list(reversed(parsed))[:limit]


def _move_to_trash(out_dir: str, pkg):
    """Move a package dir into out_dir/.trash/ — reversible delete (never hard-remove)."""
    trash = Path(out_dir) / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / pkg.name
    if dest.exists():
        shutil.rmtree(dest)  # replace a previously trashed package of the same id
    shutil.move(str(pkg), str(dest))


def _scan_trash(out_dir: str) -> list[dict]:
    """List packages in out_dir/.trash/; return [{post_id, title}]."""
    trash = Path(out_dir) / ".trash"
    if not trash.exists():
        return []
    rows = []
    for d in sorted(trash.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        title = d.name
        mp = d / "manifest.json"
        if mp.exists():
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
                title = m.get("content", {}).get("title", d.name) or d.name
            except (json.JSONDecodeError, OSError):
                pass
        rows.append({"post_id": d.name, "title": title})
    return rows


def _restore_from_trash(out_dir: str, post_id: str) -> str:
    """Move post_id from .trash/ back to out_dir/.

    Returns "ok" | "not_found" | "conflict".
    """
    if not post_id or post_id.startswith(".") or "/" in post_id or "\\" in post_id:
        return "not_found"
    trash = Path(out_dir) / ".trash"
    src = (trash / post_id).resolve()
    if src.parent != trash.resolve() or not src.is_dir():
        return "not_found"
    dest = Path(out_dir) / post_id
    if dest.exists():
        return "conflict"
    shutil.move(str(src), str(dest))
    return "ok"
