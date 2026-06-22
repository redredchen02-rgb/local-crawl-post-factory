"""cluster-scoops: aggregate library items into scoops (plan U3).

Reads the crawl library, groups items into clusters by title similarity + time
window (``cpost.core.cluster``), and writes the assignment back as a *view*
(``clusters`` table + ``library_items.cluster_id``) -- never dropping library
rows. Emits a one-line JSON summary to stdout.

Operates on the library store (like dedupe-posts on state): requires ``--state``;
``--config`` points at a scoring.yaml for thresholds (optional). A full recompute
each run keeps the assignment idempotent.
"""

import argparse
import sqlite3
from datetime import datetime, timezone

from cpost.core import cli, cluster, library, scoring_config
from cpost.core.io_ndjson import write_line


def cluster_library(conn: sqlite3.Connection, cfg: dict, now: str) -> list[dict]:
    """Cluster every library item and persist the assignment; return the clusters."""
    items = library.list_items(conn)
    clusters = cluster.cluster_items(
        items,
        ngram=int(cfg["ngram"]),
        similarity_threshold=float(cfg["similarity_threshold"]),
        time_window_hours=float(cfg["time_window_hours"]),
    )
    library.assign_clusters(conn, clusters, now)
    return clusters


def summary(clusters: list[dict]) -> dict:
    return {
        "clusters": len(clusters),
        "items": sum(c["member_count"] for c in clusters),
        "by_cluster": [
            {"cluster_id": c["cluster_id"], "member_count": c["member_count"],
             "source_count": c["source_count"]}
            for c in clusters
        ],
    }


def _run(args) -> int:
    cfg = scoring_config.load(args.config)
    now = datetime.now(timezone.utc).isoformat()
    with library.connect(args.state) as conn:
        clusters = cluster_library(conn, cfg, now)
    write_line(summary(clusters))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cluster-scoops",
        description="Aggregate library items into scoops (clusters); write the view back.",
    )
    parser.add_argument("--state", required=True, help="path to the SQLite state file")
    parser.add_argument("--config", default=None, help="path to scoring.yaml (optional)")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
