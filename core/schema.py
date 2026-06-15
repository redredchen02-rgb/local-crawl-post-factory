"""Data models and field contracts (origin spec §5).

Models are intentionally light: dicts flow through the NDJSON pipeline, and
these helpers define required/optional fields plus the canonical state names.
"""

# Crawled item (origin §5.1)
CRAWLED_REQUIRED = ("source_id", "url", "canonical_url", "title", "discovered_at")
CRAWLED_OPTIONAL = ("description", "image_url", "published_at", "text")

# State machine (origin §6)
STATES = (
    "crawled",
    "normalized",
    "deduped",
    "caption_rendered",
    "cover_selected",
    "watermarked",
    "package_built",
    "drafted",
    "draft_verified",
    "published",
    "failed",
)


def empty_manifest(post_id: str, item: dict) -> dict:
    """Build a fresh manifest skeleton (origin §5.3) in 'package_built' state.

    Paths in media/content are filled by build-manifest; timestamps are stamped
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
            "tags": item.get("tags", []),
            "category": item.get("category"),
        },
        "media": {
            "cover_path": None,
            "watermarked_cover_path": None,
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
