"""Config-driven selector recipe (R7).

This module is the ONLY source of admin backend selectors. No literal CSS
selector strings live in ``src/`` logic — they are all loaded and validated
from ``backend.yaml`` here, so a new real admin only requires editing the YAML.
"""

from pathlib import Path

from core.errors import ValidationError, DependencyError

REQUIRED_TOP_LEVEL = ("create_url", "selectors", "verify")
REQUIRED_SELECTORS = ("title", "body", "cover", "save_draft", "publish")
REQUIRED_VERIFY = ("draft_success_text", "publish_success_text")


def load_backend(path) -> dict:
    """Load and validate ``backend.yaml``.

    Missing PyYAML -> DependencyError (exit 3).
    Missing file / invalid structure / missing required keys -> ValidationError.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DependencyError(f"PyYAML not installed: {exc}")

    p = Path(path)
    if not p.exists():
        raise ValidationError(f"backend config not found: {path}")

    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid backend yaml: {exc}")

    if not isinstance(cfg, dict):
        raise ValidationError("backend config must be a mapping")

    for key in REQUIRED_TOP_LEVEL:
        if key not in cfg:
            raise ValidationError(f"backend config missing top-level key: {key}")

    selectors = cfg.get("selectors")
    if not isinstance(selectors, dict):
        raise ValidationError("backend config 'selectors' must be a mapping")
    for key in REQUIRED_SELECTORS:
        if key not in selectors or not selectors[key]:
            raise ValidationError(f"backend config missing selector: {key}")

    verify = cfg.get("verify")
    if not isinstance(verify, dict):
        raise ValidationError("backend config 'verify' must be a mapping")
    for key in REQUIRED_VERIFY:
        if key not in verify or not verify[key]:
            raise ValidationError(f"backend config missing verify key: {key}")

    return cfg


def get_selector(cfg: dict, name: str) -> str:
    """Return a selector by name. The ONLY place selectors are sourced."""
    try:
        return cfg["selectors"][name]
    except KeyError:
        raise ValidationError(f"unknown selector: {name}")
