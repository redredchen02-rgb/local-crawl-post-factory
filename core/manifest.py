"""Manifest load / save / status helpers shared by the backend commands."""

import json
from datetime import datetime, timezone
from pathlib import Path

from core.errors import ValidationError

# Manifest backend.status state machine (origin §6).
ALLOWED_STATES = (
    "package_built",
    "drafted",
    "draft_verified",
    "published",
    "failed",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ValidationError(f"manifest not found: {path}")
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid manifest json: {exc}")
    if not isinstance(data, dict):
        raise ValidationError("manifest must be a JSON object")
    return data


def save(path, manifest: dict) -> None:
    manifest.setdefault("audit", {})["updated_at"] = now_iso()
    Path(path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def require_status(manifest: dict, expected) -> None:
    """Raise ValidationError unless manifest backend.status is in ``expected``."""
    if isinstance(expected, str):
        expected = (expected,)
    status = manifest.get("backend", {}).get("status")
    if status not in ALLOWED_STATES:
        raise ValidationError(f"unknown manifest status: {status!r}")
    if status not in expected:
        raise ValidationError(
            f"refusing: manifest status {status!r} not in {list(expected)}"
        )


def set_backend(manifest: dict, *, status=None, draft_url=None,
                published_url=None, last_error=None) -> dict:
    backend = manifest.setdefault("backend", {})
    if status is not None:
        backend["status"] = status
    if draft_url is not None:
        backend["draft_url"] = draft_url
    if published_url is not None:
        backend["published_url"] = published_url
    manifest.setdefault("audit", {})["last_error"] = last_error
    return manifest
