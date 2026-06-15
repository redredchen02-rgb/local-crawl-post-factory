"""select-cover: download/pick a cover image per record (origin §4.5/§11.5, R4).

Reads normalized NDJSON from stdin, and for each record prefers its
``image_url``. The URL must look like an image, validated by file extension
(.jpg/.jpeg/.png/.webp/.gif) or by an HTTP ``Content-Type`` starting with
``image/``. On success the image is downloaded into ``--download-dir`` under a
deterministic filename and the record gains ``cover_source`` + ``cover_path``.

Determinism + no-overwrite (R4): the target filename derives from
``slug(canonical_url)`` (falling back to the image URL), so re-runs reuse the
same path; if the file already exists it is NOT re-downloaded.

A record with no usable ``image_url`` is still emitted with
``cover_source=None`` / ``cover_path=None`` (the batch never crashes). A
network/timeout/connection failure raises ExternalError (exit 4). An
``image_url`` whose content is not an image raises ValidationError (exit 2)
per origin §13.1.
"""

import argparse
import time
import urllib.error
import urllib.request
from pathlib import Path

from core import cli, filesystem, io_ndjson, url_utils
from core.errors import ExternalError, ValidationError

DEFAULT_RETRIES = 0  # backward compatible: one attempt, no retry
DEFAULT_BACKOFF_SEC = 0.0

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _ext_from_url(url: str) -> str | None:
    """Return a known image extension from the URL path, or None."""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    for ext in _IMAGE_EXTS:
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return None


def _download_once(url: str, timeout: int) -> tuple[bytes, str]:
    """Single network attempt: return (data, ext) or raise.

    Network/timeout/connection failures raise ExternalError; a non-image
    content-type raises ValidationError.
    """
    ext = _ext_from_url(url)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ext is None:
                if content_type.startswith("image/"):
                    ext = _CONTENT_TYPE_EXT.get(content_type, ".jpg")
                else:
                    raise ValidationError(
                        f"image_url is not an image (content-type {content_type!r}): {url}"
                    )
            data = resp.read()
    except ValidationError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExternalError(f"failed to download image {url}: {exc}")
    return data, ext


def _fetch(url: str, dest: Path, timeout: int,
           retries: int = DEFAULT_RETRIES, backoff_sec: float = DEFAULT_BACKOFF_SEC) -> str:
    """Download ``url`` into ``dest``; return the chosen extension.

    Retries transient ``ExternalError`` (network/timeout) up to ``retries``
    extra attempts with linear backoff; ``ValidationError`` (non-image) is never
    retried. Factored out so tests can monkeypatch it without touching the
    network.
    """
    attempts = max(1, int(retries) + 1)
    for attempt in range(1, attempts + 1):
        try:
            data, ext = _download_once(url, timeout)
            break
        except ExternalError:
            if attempt >= attempts:
                raise
            if backoff_sec:
                time.sleep(backoff_sec * attempt)

    final = dest.with_suffix(ext)
    filesystem.ensure_dir(final.parent)
    if not final.exists():  # R4: never overwrite an existing download.
        final.write_bytes(data)
    return str(final)


def _target_stem(record: dict, image_url: str) -> str:
    """Deterministic, filesystem-safe stem from canonical_url (or image url)."""
    basis = record.get("canonical_url") or image_url
    return url_utils.slug(basis)


def _select(record: dict, download_dir: Path, timeout: int,
            retries: int = DEFAULT_RETRIES, backoff_sec: float = DEFAULT_BACKOFF_SEC) -> dict:
    """Return a copy of ``record`` with cover_source/cover_path set."""
    out = dict(record)
    image_url = record.get("image_url")
    if not isinstance(image_url, str) or not image_url.strip():
        out["cover_source"] = None
        out["cover_path"] = None
        return out

    image_url = image_url.strip()
    stem = _target_stem(record, image_url)
    dest = download_dir / stem

    # If a download already exists for this stem (any known ext), reuse it (R4).
    for ext in (".jpg", ".png", ".webp", ".gif"):
        existing = dest.with_suffix(ext)
        if existing.exists():
            out["cover_source"] = image_url
            out["cover_path"] = str(existing)
            return out

    # If the URL carries an extension and that exact file exists, reuse it.
    url_ext = _ext_from_url(image_url)
    if url_ext is not None and dest.with_suffix(url_ext).exists():
        out["cover_source"] = image_url
        out["cover_path"] = str(dest.with_suffix(url_ext))
        return out

    out["cover_source"] = image_url
    out["cover_path"] = _fetch(image_url, dest, timeout, retries, backoff_sec)
    return out


def _run_factory(download_dir: Path, timeout: int,
                 retries: int = DEFAULT_RETRIES, backoff_sec: float = DEFAULT_BACKOFF_SEC):
    def _run():
        filesystem.ensure_dir(download_dir)
        for obj in io_ndjson.read_lines():
            io_ndjson.write_line(_select(obj, download_dir, timeout, retries, backoff_sec))

    return _run


def main():
    parser = argparse.ArgumentParser(
        prog="select-cover",
        description="Download/pick cover images for normalized NDJSON (stdin->stdout).",
    )
    parser.add_argument("--download-dir", required=True, help="directory to store downloaded covers")
    parser.add_argument("--timeout-sec", type=int, default=20, help="download timeout in seconds")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                        help="extra retry attempts on transient download failure")
    parser.add_argument("--backoff-sec", type=float, default=DEFAULT_BACKOFF_SEC,
                        help="linear backoff seconds between retries")
    args = parser.parse_args()
    cli.main_wrapper(_run_factory(Path(args.download_dir), args.timeout_sec,
                                  args.retries, args.backoff_sec))


if __name__ == "__main__":
    main()
