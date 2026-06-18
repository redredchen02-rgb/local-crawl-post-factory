"""WebUI settings (configs/webui.yaml) — shared by the WebUI and the CLI.

Holds only WebUI-level fields; the crawler/template configs continue
to live in their own existing yaml files, referenced here by path.
"""

import os
from pathlib import Path
from typing import Any

from core.errors import ValidationError, DependencyError
from core.validators import valid_url

DEFAULTS = {
    "start_url": "",
    "item_regex": "archives/\\d+",
    "deny_regex": "login|admin|tag|category|search|author|page/[0-9]+",
    "limit": 30,
    "max_pages": 200,
    "download_delay": 0.0,
    "concurrency": 8,
    "max_text_chars": 20000,
    "source_id": "",
    "template_path": "./templates/fixed-format.zh.yaml",
    "download_dir": "./out/assets",
    "out_dir": "./out",
    "state_path": "./state/published.sqlite",
    "audit_log": "./logs/audit.jsonl",
    "backend_config": "./configs/backend.yaml",
    "storage_state": "./auth/storage-state.json",
    "llm_config": "./configs/llm.yaml",
    "scoring_config": "./configs/scoring.yaml",
    # /today scoop list default filters. min_confidence is the minimum number of
    # independent sources (source_count); 0 = no minimum, so a single-source
    # library is never filtered to empty. min_score gates the combined score.
    "min_confidence": 0,
    "min_score": 0.0,
    "auto_pipeline": False,
}

_INT_FIELDS = ("limit", "max_pages", "concurrency", "max_text_chars", "min_confidence")
_FLOAT_FIELDS = ("download_delay", "min_score")
# Checkbox fields: form POST sends "on" when checked, absent when unchecked.
_BOOL_FIELDS = ("auto_pipeline",)

# Output/runtime path fields resolved relative to the config file's directory
# (R7) so the WebUI writes to the same place regardless of launch directory.
# Asset-config paths (template_path/backend_config) are
# deliberately NOT resolved here: their defaults reference repo-shipped files
# relative to the run directory; rewriting them against the config dir would
# break the shipped defaults.
_PATH_FIELDS = ("download_dir", "out_dir", "state_path", "audit_log", "storage_state")
# Env overrides are scoped to the runtime-output paths an operator actually
# relocates for CI/containers; precedence is env > yaml > default.
_ENV_OVERRIDES = {
    "state_path": "CPOST_STATE_PATH",
    "out_dir": "CPOST_OUT_DIR",
    "download_dir": "CPOST_DOWNLOAD_DIR",
}


def load_raw(path: str) -> dict:
    """Load settings merged over defaults **without** resolving path fields.

    The on-disk (relative) form of path fields is preserved. The settings save
    flow uses this to carry forward non-form fields without baking ``load()``'s
    machine-absolute paths back into the file (which would break relocation).
    """
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


def load(path: str) -> dict:
    """Load settings (path fields resolved to absolute), defaults for missing keys."""
    cfg = load_raw(path)
    _resolve_paths(cfg, base=Path(path).parent)
    return cfg


def _resolve_paths(cfg: dict, base: Path) -> None:
    """Resolve path fields to absolute paths (R7). Mutates ``cfg`` in place.

    Relative paths are resolved against ``base`` (the config file's directory);
    env overrides (``CPOST_*``) take precedence and are ``expanduser``-ed.
    ``storage_state`` is credential-grade: it must not resolve inside ``out_dir``
    or ``download_dir`` (which may be served / contain remote-fetched content).
    """
    base = Path(base).resolve()
    for field in _PATH_FIELDS:
        env_name = _ENV_OVERRIDES.get(field)
        raw: Any
        if env_name is not None and env_name in os.environ:
            raw = os.environ[env_name]
            from_env = True
            if not raw.strip():
                raise ValidationError(f"{env_name} is empty or not a valid path")
        else:
            raw = cfg.get(field)
            from_env = False
            if not isinstance(raw, str) or not raw.strip():
                continue
        p = Path(raw).expanduser() if from_env else Path(raw)
        if not p.is_absolute():
            p = base / p
        cfg[field] = str(p.resolve())

    ss = Path(cfg["storage_state"])
    for guard in ("out_dir", "download_dir"):
        gpath = Path(cfg[guard])
        if ss == gpath or ss.is_relative_to(gpath):
            raise ValidationError(
                f"storage_state must not resolve inside {guard} (credential exposure)")


def save(path: str, cfg: dict) -> dict:
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
    if int(cfg.get("max_pages", 0)) < 1:
        raise ValidationError("max_pages must be >= 1")
    if float(cfg.get("download_delay", 0)) < 0:
        raise ValidationError("download_delay must be >= 0")
    if int(cfg.get("concurrency", 1)) < 1:
        raise ValidationError("concurrency must be >= 1")
    if int(cfg.get("max_text_chars", 0)) < 0:
        raise ValidationError("max_text_chars must be >= 0")
    if int(cfg.get("min_confidence", 0)) < 0:
        raise ValidationError("min_confidence must be >= 0")
    if float(cfg.get("min_score", 0)) < 0:
        raise ValidationError("min_score must be >= 0")


def _coerce(cfg: dict) -> None:
    for field in _INT_FIELDS:
        try:
            cfg[field] = int(cfg[field])
        except (TypeError, ValueError):
            raise ValidationError(f"{field} must be an integer")
    for field in _FLOAT_FIELDS:
        try:
            cfg[field] = float(cfg[field])
        except (TypeError, ValueError):
            raise ValidationError(f"{field} must be a number")
    for field in _BOOL_FIELDS:
        val = cfg.get(field)
        # HTML checkbox: "on" when checked, absent (None/missing) when unchecked.
        # YAML native bool passes through unchanged.
        if isinstance(val, bool):
            pass
        elif isinstance(val, str):
            cfg[field] = val.lower() == "on"
        elif isinstance(val, (int, float)):
            cfg[field] = bool(val)
        elif val is None:
            cfg[field] = False
        else:
            raise ValidationError(f"{field} must be a boolean value, got {type(val).__name__}")
