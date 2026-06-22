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


def _strip_url(body: str, url: str) -> str:
    """Remove the url (and any leading fragment of it) from the end of ``body``.

    The caption is built from a template that embeds the url mid-string, so a
    naive ``caption[:budget]`` can leave the url intact or — worse — as a
    partial fragment in the body. We re-append the url ourselves, so any whole
    or partial occurrence trailing the truncated body must go first.
    """
    if not url:
        return body
    idx = body.find(url)
    if idx != -1:
        # Whole url present: cut from its first occurrence onward.
        return body[:idx]
    # No whole url, but the truncation may have severed it: drop the longest
    # suffix of body that is a prefix of url (a partial url fragment).
    for cut in range(len(body)):
        if url.startswith(body[cut:]):
            return body[:cut]
    return body


def _enforce_max_chars(caption: str, canonical_url: str, max_chars: int) -> str:
    """Truncate to ``max_chars`` while keeping the canonical_url intact.

    Deterministic: if over budget, cut the caption to fit, strip any whole or
    partial url left in the body, then re-append the canonical_url on its own
    line so the source link survives exactly once and is never fragmented.
    """
    if max_chars <= 0 or len(caption) <= max_chars:
        return caption
    url = (canonical_url or "").strip()
    if not url:
        return caption[:max_chars]
    # Reserve room for a newline + the url line; truncate the body region.
    tail = "\n" + url
    budget = max_chars - len(tail)
    if budget < 0:
        # url alone exceeds budget: keep the url, drop the rest.
        return url[:max_chars]
    body = _strip_url(caption[:budget], url).rstrip()
    return body + tail


def render(record: dict, template_cfg: dict) -> str:
    """Pure render: record + template config -> caption string."""
    values = defaultdict(str)
    for field in _RENDER_FIELDS:
        value = record.get(field)
        if value is not None:
            values[field] = str(value)
    caption = template_cfg["format"].format_map(values).strip()
    max_chars = int(template_cfg.get("max_chars", 0) or 0)
    return _enforce_max_chars(caption, values["canonical_url"], max_chars)


_render = render  # deprecated: remove in vNEXT (use render)


def render_record(record: dict, template_cfg: dict) -> dict:
    """Set ``caption`` + ``content_hash`` on ``record`` in place; return it.

    Single source of the caption+hash step shared by the CLI ``_run`` and the
    in-process pipeline (U5b) so the content_hash inputs/formula live in one
    place.
    """
    caption = render(record, template_cfg)
    record["caption"] = caption
    record["content_hash"] = url_utils.content_hash(
        str(record.get("canonical_url", "")),
        str(record.get("title", "")),
        caption,
    )
    return record


def _run(template_path: str):
    template_cfg = load_template(template_path)
    for record in io_ndjson.read_lines():
        io_ndjson.write_line(render_record(record, template_cfg))


def main():
    parser = argparse.ArgumentParser(
        prog="render-caption",
        description="Render fixed-format captions for normalized NDJSON (stdin->stdout).",
    )
    parser.add_argument("--template", required=True, help="Path to template YAML.")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args.template))


if __name__ == "__main__":
    main()
