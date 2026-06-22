"""Manifest load / save / status helpers shared by the backend commands."""

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from cpost.core.errors import ValidationError
from cpost.core.filesystem import atomic_write_text

# Manifest backend.status state machine (origin §6).
ALLOWED_STATES = (
    "package_built",
    "drafted",
    "draft_verified",
    "published",
    "failed",
)


class _Unset(Enum):
    """Sentinel so set_backend can distinguish 'leave unchanged' from 'clear'.

    A plain ``None`` already means "clear to None" (rollback must drop a stale
    published_url); ``UNSET`` is the default that leaves an existing value alone.
    Modelled as an Enum member so it has a precise singleton type for mypy.
    """

    token = 0


UNSET = _Unset.token


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: str | Path) -> dict:
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


def save(path: str | Path, manifest: dict) -> None:
    manifest.setdefault("audit", {})["updated_at"] = now_iso()
    atomic_write_text(
        Path(path),
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
    )


def require_status(manifest: dict, expected: str | tuple[str, ...]) -> None:
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


def set_backend(manifest: dict, *, status: str | None = None,
                draft_url: str | None = None,
                published_url: str | None | _Unset = UNSET,
                last_error: str | None = None) -> dict:
    """Update backend fields. ``published_url`` distinguishes three intents:
    ``UNSET`` (default) leaves any existing value untouched, ``None`` clears it
    (so rollback drops a stale url), and a string sets it.
    """
    backend = manifest.setdefault("backend", {})
    if status is not None:
        backend["status"] = status
    if draft_url is not None:
        backend["draft_url"] = draft_url
    if published_url is not UNSET:
        backend["published_url"] = published_url
    manifest.setdefault("audit", {})["last_error"] = last_error
    return manifest
