"""WebUI settings (configs/webui.yaml) — shared by the WebUI and the CLI.

Holds only WebUI-level fields; the crawler/watermark/template configs continue
to live in their own existing yaml files, referenced here by path.
"""

from pathlib import Path

from core.errors import ValidationError, DependencyError
from core.validators import valid_url

DEFAULTS = {
    "start_url": "",
    "item_regex": "/news/|/article/|/post/",
    "deny_regex": "login|admin|tag|category|search|page/[0-9]+",
    "limit": 30,
    "source_id": "",
    "template_path": "./templates/fixed-format.zh.yaml",
    "watermark_config": "./configs/watermark.yaml",
    "download_dir": "./out/assets",
    "out_dir": "./out",
    "state_path": "./state/published.sqlite",
    "audit_log": "./logs/audit.jsonl",
    "backend_config": "./configs/backend.yaml",
    "storage_state": "./auth/storage-state.json",
}

_INT_FIELDS = ("limit",)


def load(path) -> dict:
    """Load settings, falling back to defaults for a missing file/keys."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise DependencyError(f"PyYAML not installed: {exc}")

    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        try:
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValidationError(f"invalid webui yaml: {exc}")
        if not isinstance(loaded, dict):
            raise ValidationError("webui config must be a mapping")
        for key, value in loaded.items():
            if key in cfg and value is not None:
                cfg[key] = value
    _coerce(cfg)
    return cfg


def save(path, cfg: dict) -> dict:
    """Validate and persist settings, merging over defaults."""
    import yaml

    merged = dict(DEFAULTS)
    for key, value in (cfg or {}).items():
        if key in merged and value is not None:
            merged[key] = value
    _coerce(merged)
    validate(merged)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=True),
                 encoding="utf-8")
    return merged


def validate(cfg: dict) -> None:
    if cfg.get("start_url") and not valid_url(cfg["start_url"]):
        raise ValidationError(f"invalid start_url: {cfg.get('start_url')!r}")
    if int(cfg.get("limit", 0)) < 0:
        raise ValidationError("limit must be >= 0")


def _coerce(cfg: dict) -> None:
    for field in _INT_FIELDS:
        try:
            cfg[field] = int(cfg[field])
        except (TypeError, ValueError):
            raise ValidationError(f"{field} must be an integer")
