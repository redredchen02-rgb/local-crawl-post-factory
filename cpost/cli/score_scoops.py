"""score-scoops: score each scoop on confidence + quality (plan U4).

Reads the clusters produced by ``cluster-scoops`` plus their member items, scores
each on the two axes (``cpost.core.scoring``), writes the scores back onto the
``clusters`` rows, and emits a JSON summary sorted by combined score. Operates on
the library store: requires ``--state``; ``--config`` points at scoring.yaml
(optional). A full re-score each run keeps results idempotent.
"""

import argparse
import sqlite3
from datetime import datetime, timezone

from cpost.core import cli, library, scoring, scoring_config
from cpost.core.io_ndjson import write_line


def score_all(conn: sqlite3.Connection, cfg: dict, now: str) -> list[dict]:
    """Score every cluster, persist the scores, return them sorted by score desc."""
    scored = []
    for c in library.list_clusters(conn):
        members = library.get_cluster_members(conn, c["cluster_id"])
        s = scoring.score_cluster(c, members, now, cfg)
        library.set_cluster_scores(conn, c["cluster_id"], now=now, **s)
        scored.append({**c, **s})
    scored.sort(key=lambda r: (r["score"], r["confidence"], r["cluster_id"]), reverse=True)
    return scored


def summary(scored: list[dict]) -> dict:
    return {
        "scored": len(scored),
        "by_cluster": [
            {"cluster_id": r["cluster_id"], "source_count": r["source_count"],
             "confidence": round(r["confidence"], 4), "quality": round(r["quality"], 4),
             "score": round(r["score"], 4)}
            for r in scored
        ],
    }


def _run(args) -> int:
    cfg = scoring_config.load(args.config)
    now = datetime.now(timezone.utc).isoformat()
    with library.connect(args.state) as conn:
        scored = score_all(conn, cfg, now)
    write_line(summary(scored))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="score-scoops",
        description="Score scoops (clusters) by multi-source confidence + content quality.",
    )
    parser.add_argument("--state", required=True, help="path to the SQLite state file")
    parser.add_argument("--config", default=None, help="path to scoring.yaml (optional)")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
