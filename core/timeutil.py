"""Shared ISO-8601 parsing for clustering + scoring.

Returns timezone-aware datetimes: a naive timestamp (no offset, e.g. a crawled
``published_at`` of ``2026-06-18T00:00:00``) is assumed to be UTC. This keeps all
comparisons aware-vs-aware -- mixing naive and aware datetimes would otherwise
raise ``TypeError: can't subtract offset-naive and offset-aware datetimes`` and
crash the cluster/score stage on real-world data.
"""

from datetime import datetime, timezone


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string to an aware datetime (UTC if naive); None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
