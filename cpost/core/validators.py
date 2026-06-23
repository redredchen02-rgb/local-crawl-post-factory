"""Validation helpers shared across pipeline stages."""

import ipaddress
import socket
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


def is_safe_external_host(hostname: str) -> bool:
    """Return True only if *hostname* resolves to a routable (non-private) address.

    SSRF防護：過濾 RFC-1918 私有 IP、loopback、link-local，以及無法解析的主機名。
    使用 stdlib ``ipaddress`` + ``socket``，不依賴外部套件。
    """
    if not hostname:
        return False
    try:
        ip_str = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(ip_str)
    except (socket.gaierror, ValueError):
        # 無法解析 → 視為不安全
        return False
    # is_private covers RFC-1918, loopback, link-local, and reserved ranges
    return not addr.is_private
