"""Data models and field contracts (origin spec §5).

Models are intentionally light: dicts flow through the NDJSON pipeline, and
these helpers define required/optional fields plus the canonical state names.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

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
# Scoop / generation-track orchestration (cpost.core.scoop_pipeline)
# ---------------------------------------------------------------------------

class ScoopFailed(TypedDict, total=True):
    """A single failure recorded during prep/generation (stage-tagged)."""
    stage: str
    error: str
    cluster_id: NotRequired[str]


class PrepTopScoop(TypedDict, total=True):
    """One ranked scoop summary in PrepPipelineResult.top."""
    cluster_id: str
    representative_title: str | None
    source_count: int
    confidence: float
    quality: float
    score: float


class PrepPipelineResult(TypedDict, total=True):
    """Return value of run_prep_pipeline()."""
    ingested: int
    clusters: int
    scored: int
    single_source: bool
    top: list[PrepTopScoop]
    failed: list[ScoopFailed]


class GenerationBuilt(TypedDict, total=True):
    """One built package summary in GenerationPipelineResult.built."""
    post_id: str
    title: str


class GenerationPipelineResult(TypedDict, total=True):
    """Return value of run_generation_pipeline()."""
    built: list[GenerationBuilt]
    failed: list[ScoopFailed]
    kind: str


# ---------------------------------------------------------------------------
# PackageInput: the normalized item contract feeding build-manifest (R8)
# ---------------------------------------------------------------------------

# build_manifest._REQUIRED = ('title', 'canonical_url', 'caption'). These three
# are the hard contract; the rest are optional source/provenance fields the
# manifest carries through (see empty_manifest).
PACKAGE_INPUT_REQUIRED = ("title", "canonical_url", "caption")


class PackageInput(TypedDict, total=False):
    """Normalized item handed to ``build_manifest.build`` (origin R8).

    ONE shared contract for BOTH convergence tracks:
      * legacy repost track: normalize-items -> build-manifest;
      * scoop generation track: generate-article -> build-manifest.

    Required (build_manifest._REQUIRED): ``title``, ``canonical_url``, ``caption``.
    ``caption`` is the publishable body -> build-manifest maps it to
    ``content.body``; ``text`` (if present) is the raw full article persisted to
    source_text.txt and never touches ``content.body``.

    ``canonical_url`` is the dedup/identity key. For the scoop track it is the
    self-describing synthetic ``https://scoop.cpost.local/<cluster_id>`` -- a real
    http(s)+hostname URL (``cpost.core.validators.valid_url`` rejects custom schemes and
    hostless URLs); ``cluster_id`` is content-derived (``c_<hex>``) so the same
    membership yields the same canonical, keeping cross-run dedup correct.

    G5 provenance decision (R8): for the scoop track ``source_id`` is the fixed
    ``"scoop"`` -- the aggregated article is a NEW synthesized artifact, not from
    any single source. Per-source provenance lives in the cluster members /
    library (tracked by R4); R4's per-member ``source_id`` does NOT flow into the
    manifest.
    """
    title: str
    canonical_url: str
    caption: str
    source_id: str | None
    text: str
    url: str | None
    published_at: str | None
    discovered_at: str | None
    tags: list[str]
    category: str | None
    run_id: str | None


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


def empty_manifest(post_id: str, item: Mapping[str, Any]) -> dict:
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
