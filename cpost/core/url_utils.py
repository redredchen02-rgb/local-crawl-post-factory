"""URL normalization, slugging, and deterministic hashing helpers.

Determinism is a hard requirement (origin R5): the same input must always
produce the same slug / post_id / content_hash.
"""

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """Return a canonicalized URL.

    Lowercases scheme/host, strips default ports, drops fragments, and removes a
    trailing slash on non-root paths. Query is preserved as-is (order matters
    for some CMS routes). Raises nothing; validation lives in ``validators``.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    host = host.lower()
    if ":" in host:
        # IPv6 literal: parts.hostname strips the brackets, so re-wrap before
        # re-attaching a port, otherwise "::1" + ":443" is ambiguous.
        host = f"[{host}]"
    netloc = host
    if parts.port and not _is_default_port(scheme, parts.port):
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme == "http" and port == 80) or (scheme == "https" and port == 443)


def slug(text: str, max_len: int = 60) -> str:
    """ASCII slug for filesystem-safe identifiers."""
    s = _SLUG_STRIP.sub("_", text.lower()).strip("_")
    return s[:max_len] or "item"


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def clean_text(value: str) -> str:
    """Collapse whitespace and trim."""
    return _WS.sub(" ", value).strip()


def sha256_hex(*parts: str) -> str:
    h = hashlib.sha256()
    h.update(" ".join(parts).encode("utf-8"))
    return h.hexdigest()


def title_hash(title: str) -> str:
    return sha256_hex(clean_text(title).lower())


def content_hash(canonical_url: str, title: str, caption: str) -> str:
    return sha256_hex(canonical_url, clean_text(title), caption)
