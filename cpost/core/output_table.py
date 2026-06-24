"""Format scored scoops as a human-readable table (terminal or markdown).

Used by ``score-scoops --format table|markdown`` to supplement the default JSON
summary. 4D‑aware: shows the four new dimension scores when present.
"""

from __future__ import annotations


def _val(v: float | None, ndigits: int = 4) -> str:
    if v is None:
        return "—"
    return str(round(v, ndigits))


def _bar(value: float, width: int = 8) -> str:
    """Simple ASCII bar for 0..1 values."""
    filled = max(0, min(width, round(value * width)))
    return "▰" * filled + "▱" * (width - filled)


def terminal(scored: list[dict], max_rows: int = 20) -> str:
    """Render scored scoops as a terminal-friendly table with ASCII bars."""
    lines: list[str] = []
    lines.append(f"{'#':>3}  {'cluster_id':>12}  {'sources':>7}  {'score':>6}"
                 f"  {'fresh':>6}  {'import':>6}  {'traffic':>6}  {'coverage':>6}"
                 f"  {'title'}")
    lines.append("─" * len(lines[0]))
    for i, r in enumerate(scored[:max_rows]):
        fresh = _val(r.get("freshness"))
        imp = _val(r.get("importance"))
        tp = _val(r.get("traffic_potential"))
        csc = _val(r.get("cross_site_coverage"))
        score = r.get("score", 0)
        bar = _bar(score)
        title = (r.get("representative_title") or r.get("cluster_id", ""))[:40]
        lines.append(
            f"{i + 1:>3}  {r['cluster_id'][:12]:>12}  {r.get('source_count', 0):>7}"
            f"  {_val(score):>6}  {fresh:>6}  {imp:>6}  {tp:>6}  {csc:>6}"
            f"  {bar}  {title}"
        )
    lines.append(f"\n{len(scored)} scoops total (showing {min(len(scored), max_rows)})")
    return "\n".join(lines)


def markdown(scored: list[dict], max_rows: int = 20) -> str:
    """Render scored scoops as a markdown table. Returns empty string when
    ``scored`` is empty (no rows to show)."""
    if not scored:
        return ""
    lines: list[str] = []
    lines.append("| # | cluster_id | sources | score | freshness | importance"
                 " | traffic | coverage | title |")
    lines.append("|---|-----------|--------|-------|-----------|------------"
                 "|---------|----------|-------|")
    for i, r in enumerate(scored[:max_rows]):
        fresh = _val(r.get("freshness"))
        imp = _val(r.get("importance"))
        tp = _val(r.get("traffic_potential"))
        csc = _val(r.get("cross_site_coverage"))
        title = (r.get("representative_title") or r.get("cluster_id", ""))[:40]
        lines.append(
            f"| {i + 1} | {r['cluster_id'][:12]}"
            f" | {r.get('source_count', 0)}"
            f" | {_val(r.get('score', 0))}"
            f" | {fresh} | {imp} | {tp} | {csc}"
            f" | {title} |"
        )
    return "\n".join(lines)
