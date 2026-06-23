"""render-caption: apply a fixed-format template to normalized NDJSON.

Reads normalized records from stdin, renders a ``caption`` via SAFE substitution
of ``{title}/{description}/{canonical_url}/{hashtags}`` using a fixed YAML
template, enforces ``max_chars`` while always preserving the canonical_url line,
and writes NDJSON (with added ``caption`` and ``content_hash``) to stdout under
the shared CLI contract (origin §4.4/§11.4, R5).

Determinism (R5): same record + same template -> identical caption.
Missing fields render as empty string (never KeyError, never hallucinated).
"""

import argparse
from collections import defaultdict
from pathlib import Path

from cpost.core import cli, io_ndjson, url_utils
from cpost.core.errors import DependencyError, ValidationError

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
    raise DependencyError(f"PyYAML is required: {exc}")

# Fields substituted into the template. Anything else renders empty.
_RENDER_FIELDS = ("title", "description", "canonical_url", "hashtags")


def load_template(path: str) -> dict:
    """Load and validate the template YAML.

    A missing/unreadable file or a missing ``format`` key is a ValidationError
    (exit 2).
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read template {path}: {exc}")
    try:
        cfg = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid template YAML {path}: {exc}")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("format"), str):
        raise ValidationError(f"template {path} missing 'format' key")
    return cfg


def _enforce_max_chars(caption: str, url_free_body: str, canonical_url: str, max_chars: int) -> str:
    """Truncate to ``max_chars`` while keeping the canonical_url intact.

    Deterministic. The under-budget caption is returned verbatim (url stays at
    its template position). Over budget, we do NOT search the truncated text for
    the url — that risks eating legitimate body characters that merely share a
    prefix with the url. Instead we truncate ``url_free_body`` (the same caption
    rendered with ``{canonical_url}`` blank) to fit, then append the url once on
    its own line so the source link survives exactly once and is never fragmented.
    """
    if max_chars <= 0 or len(caption) <= max_chars:
        return caption
    url = (canonical_url or "").strip()
    if not url:
        return caption[:max_chars]
    # Reserve room for a newline + the url line; truncate the url-free body.
    tail = "\n" + url
    budget = max_chars - len(tail)
    if budget < 0:
        # url alone exceeds budget: keep the url, drop the rest.
        return url[:max_chars]
    body = url_free_body[:budget].rstrip()
    return body + tail


def _render_with(template_cfg: dict, values: "defaultdict[str, str]") -> str:
    fmt: str = template_cfg["format"]
    return fmt.format_map(values).strip()


def render(record: dict, template_cfg: dict) -> str:
    """Pure render: record + template config -> caption string."""
    values: "defaultdict[str, str]" = defaultdict(str)
    for field in _RENDER_FIELDS:
        value = record.get(field)
        if value is not None:
            values[field] = str(value)
    caption = _render_with(template_cfg, values)
    max_chars = int(template_cfg.get("max_chars", 0) or 0)
    if max_chars <= 0 or len(caption) <= max_chars:
        return caption
    # Over budget: rebuild the body with the url slot blank so truncation can
    # never sever the url or eat a legitimate body char that shares its prefix.
    url_free_values = values.copy()
    url_free_values["canonical_url"] = ""
    url_free_body = _render_with(template_cfg, url_free_values)
    return _enforce_max_chars(caption, url_free_body, values["canonical_url"], max_chars)


_render = render  # deprecated: remove in vNEXT (use render)


def make_content_hash(item: dict) -> str:
    """Compute the reviewed-content dedup hash for a rendered ``item``.

    Inputs and order are exactly those of the publish-gate dedup formula:
    ``canonical_url``, ``title``, ``caption`` (each missing field -> ""). The
    item is expected to already carry a rendered ``caption`` (as set by
    :func:`render_record`); pass an item without one only when the empty-caption
    hash is what you want. Pure and deterministic.
    """
    return url_utils.content_hash(
        str(item.get("canonical_url", "")),
        str(item.get("title", "")),
        str(item.get("caption", "")),
    )


def render_record(record: dict, template_cfg: dict) -> dict:
    """Set ``caption`` + ``content_hash`` on ``record`` in place; return it.

    Single source of the caption+hash step shared by the CLI ``_run`` and the
    in-process pipeline (U5b) so the content_hash inputs/formula live in one
    place.
    """
    record["caption"] = render(record, template_cfg)
    record["content_hash"] = make_content_hash(record)
    return record


def _run(template_path: str) -> None:
    template_cfg = load_template(template_path)
    for record in io_ndjson.read_lines():
        io_ndjson.write_line(render_record(record, template_cfg))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="render-caption",
        description="Render fixed-format captions for normalized NDJSON (stdin->stdout).",
    )
    parser.add_argument("--template", required=True, help="Path to template YAML.")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args.template))


if __name__ == "__main__":
    main()
