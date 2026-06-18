"""Data models and field contracts (origin spec §5).

Models are intentionally light: dicts flow through the NDJSON pipeline, and
these helpers define required/optional fields plus the canonical state names.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Crawled / Normalized item  (origin §5.1)
# ---------------------------------------------------------------------------

CRAWLED_REQUIRED = ("source_id", "url", "canonical_url", "title", "discovered_at")
CRAWLED_OPTIONAL = ("description", "image_url", "published_at", "text")


class CrawledItem(TypedDict, total=False):
    """Raw item produced by crawl_posts (required fields only)."""
    source_id: str
    url: str
    canonical_url: str
    title: str
    discovered_at: str
    description: str
    image_url: str
    published_at: str
    text: str


class NormalizedItem(CrawledItem):
    """After normalize_one: same shape, fields cleaned, empty optionals dropped."""
    pass


class PipelineItem(TypedDict, total=True):
    """Item as it moves through the pipeline after build-manifest."""
    post_id: str
    title: str
    manifest_path: str


class PipelineFailed(TypedDict, total=True):
    """A single item failure recorded during pipeline processing."""
    post_id: str | None
    stage: str
    error: str
    error_class: NotRequired[str]


class PipelineResult(TypedDict, total=True):
    """Return value of run_pipeline()."""
    built: list[PipelineItem]
    failed: list[PipelineFailed]
    skipped: int
    auto_pipeline: NotRequired["AutoPipelineResult"]


class AutoPipelineResult(TypedDict, total=True):
    """Return value of run_auto_pipeline()."""
    ok: int
    failed: list[PipelineFailed]
    verify_fail_count: int


# ---------------------------------------------------------------------------
# Manifest (origin §5.3)
# ---------------------------------------------------------------------------

class ManifestSource(TypedDict, total=False):
    source_id: str | None
    url: str | None
    canonical_url: str | None
    published_at: str | None
    discovered_at: str | None


class ManifestContent(TypedDict, total=False):
    title: str
    caption_path: str
    body: str
    source_text_path: str | None
    tags: list[str]
    category: str | None


class ManifestBackend(TypedDict, total=False):
    status: str
    draft_url: str | None
    published_url: str | None
    remote_id: str | None
    run_id: str | None


class ManifestAudit(TypedDict, total=False):
    created_at: str | None
    updated_at: str | None
    last_error: str | None


class Manifest(TypedDict, total=False):
    """Full manifest.json shape."""
    post_id: str
    source: ManifestSource
    content: ManifestContent
    backend: ManifestBackend
    audit: ManifestAudit

# State machine (origin §6)
STATES = (
    "crawled",
    "normalized",
    "deduped",
    "caption_rendered",
    "package_built",
    "drafted",
    "draft_verified",
    "published",
    "failed",
)


def empty_manifest(post_id: str, item: dict) -> dict:
    """Build a fresh manifest skeleton (origin §5.3) in 'package_built' state.

    Paths in content are filled by build-manifest; timestamps are stamped
    by the caller so this stays deterministic/pure.
    """
    return {
        "post_id": post_id,
        "source": {
            "source_id": item.get("source_id"),
            "url": item.get("url"),
            "canonical_url": item.get("canonical_url"),
            "published_at": item.get("published_at"),
            "discovered_at": item.get("discovered_at"),
        },
        "content": {
            "title": item.get("title"),
            "caption_path": "./caption.txt",
            "body": item.get("caption", ""),
            # Raw full article text (内文), distinct from body(=caption). Filled by
            # build-manifest when the crawled record carries non-empty ``text``.
            "source_text_path": None,
            "tags": item.get("tags", []),
            "category": item.get("category"),
        },
        "backend": {
            "status": "package_built",
            "draft_url": None,
            "published_url": None,
            "remote_id": None,
            "run_id": item.get("run_id"),  # Q7: build-time run_id for cross-process lifecycle
        },
        "audit": {
            "created_at": None,
            "updated_at": None,
            "last_error": None,
        },
    }
