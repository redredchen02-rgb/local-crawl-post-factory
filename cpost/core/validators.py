"""Validation helpers shared across pipeline stages."""

from urllib.parse import urlsplit

from cpost.core.errors import ValidationError

_VALID_SCHEMES = {"http", "https"}


def valid_url(url: str) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parts = urlsplit(url.strip())
    return parts.scheme.lower() in _VALID_SCHEMES and bool(parts.hostname)


def require_url(url: str, field: str = "url") -> str:
    if not valid_url(url):
        raise ValidationError(f"invalid {field}: {url!r}")
    return url.strip()


def require_nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"missing or empty field: {field}")
    return value.strip()
