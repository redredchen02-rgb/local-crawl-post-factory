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


def _apply_min_sources(scored: list[dict], min_sources: int) -> list[dict]:
    """Filter out scoops whose source_count is below *min_sources*.

    Scoops that lack a ``source_count`` field (legacy format) are treated as 0
    and filtered out when *min_sources* > 0.
    """
    if min_sources <= 0:
        return scored
    return [s for s in scored if s.get("source_count", 0) >= min_sources]


def _run(args: argparse.Namespace) -> int:
    cfg = scoring_config.load(args.config)
    # --min-sources: explicit CLI value takes precedence; fall back to config key.
    min_sources_raw: int | None = getattr(args, "min_sources", None)
    min_sources: int = (
        min_sources_raw
        if min_sources_raw is not None
        else int(cfg.get("actionable_min_sources", 0))
    )
    now = datetime.now(timezone.utc).isoformat()
    with library.connect(args.state) as conn:
        scored = score_all(conn, cfg, now)
    scored = _apply_min_sources(scored, min_sources)
    write_line(summary(scored))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="score-scoops",
        description="Score scoops (clusters) by multi-source confidence + content quality.",
    )
    parser.add_argument("--state", required=True, help="path to the SQLite state file")
    parser.add_argument("--config", default=None, help="path to scoring.yaml (optional)")
    parser.add_argument(
        "--min-sources", type=int, default=None, dest="min_sources",
        metavar="N",
        help=(
            "only output scoops with source_count >= N "
            "(default: read actionable_min_sources from config, or 0 = no filter)"
        ),
    )
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
