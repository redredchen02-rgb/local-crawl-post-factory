"""generate-article: synthesize one original article from a scoop (plan U4).

Reads a cluster's multi-source members from the crawl library, calls the LLM
(reusing ``core.llm``) with the multi-source synthesis prompt, and emits ONE
synthesized normalized item (NDJSON) ready for build-manifest.

Two contract details that the build/publish chain depends on:
- the generated body is carried in the ``caption`` field -- build-manifest maps
  ``content.body = caption`` (a record's ``text`` only lands in source_text.txt);
- the synthetic identity is ``https://scoop.local/<cluster_id>`` -- a real
  http(s) URL so ``core.validators.valid_url`` accepts it (a ``scoop://`` scheme
  would be rejected and never reach build-manifest).

The title is parsed from the model's first line, falling back to the cluster's
``representative_title``. A (member fingerprint + model + prompt version) cache
makes reruns stable and avoids re-billing the API. Missing key -> ValidationError
(exit 2) and HTTP/network -> ExternalError (exit 4) propagate from core.llm; an
empty/unknown cluster -> ValidationError (exit 2).
"""

import argparse
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from core import cli, library, llm
from core.errors import ValidationError
from core.io_ndjson import write_line

_PROMPT_VERSION = "scoop-v1"
SCOOP_SOURCE_ID = "scoop"
# Real cluster ids are ``c_<12 hex>`` (core.cluster._cluster_id); this guard
# rejects malformed form-supplied ids before they reach the synthetic URL.
_CLUSTER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def cache_key(members: list[dict], model: str, prompt_key: str) -> str:
    """Hash member content + model + prompt (membership/prompt change -> new key)."""
    h = hashlib.sha256()
    h.update(prompt_key.encode("utf-8"))
    h.update(b"\x00")
    h.update((model or "").encode("utf-8"))
    for m in sorted(members, key=lambda r: r["canonical_url"]):
        h.update(b"\x00")
        h.update(m["canonical_url"].encode("utf-8"))
        h.update((m.get("source_text") or "").encode("utf-8"))
    return h.hexdigest()


def build_material(members: list[dict]) -> str:
    """Compose the multi-source user content, each member labeled by source."""
    blocks = []
    for i, m in enumerate(members, 1):
        blocks.append(
            f"【来源 {i}｜source_id={m.get('source_id') or '未知'}】\n"
            f"标题：{m.get('title') or ''}\n"
            f"正文：{m.get('source_text') or m.get('description') or ''}"
        )
    header = (f"这是同一件事来自 {len(members)} 个来源的素材，"
              "请综合成一篇原创文章（第一行为标题）：\n\n")
    return header + "\n\n".join(blocks)


def split_title_body(article: str, fallback_title: str) -> tuple[str, str]:
    """First non-empty line = title; the rest = body. Fall back when unclear."""
    lines = article.strip().splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            title = line.strip().lstrip("#").strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    # No clean title line (empty or implausibly long) -> use the cluster title
    # and keep the whole article as body. When a title parses cleanly, return the
    # remaining body as-is (which may be empty -- the caller rejects empty bodies
    # rather than duplicating the title into the body).
    if not title or len(title) > 80:
        return (fallback_title or "未命名瓜"), article.strip()
    return title, body


def generate(conn, cluster_id: str, llm_cfg: dict, system_prompt: str, now: str,
             *, _chat=llm.chat) -> dict:
    """Synthesize one article for ``cluster_id``; return a normalized item dict."""
    if not _CLUSTER_ID_RE.fullmatch(cluster_id or ""):
        raise ValidationError(f"invalid cluster_id: {cluster_id!r}")
    cluster = library.get_cluster(conn, cluster_id)
    members = library.get_cluster_members(conn, cluster_id)
    if not cluster or not members:
        raise ValidationError(f"unknown or empty cluster: {cluster_id!r}")

    model = str(llm_cfg.get("model") or "")
    key = cache_key(members, model, _PROMPT_VERSION + system_prompt)
    cached = library.get_generation(conn, key)
    if cached:
        title, body = cached["title"], cached["body"]
    else:
        article = _chat(llm_cfg, system_prompt, build_material(members))
        title, body = split_title_body(article, cluster.get("representative_title") or "")
        if not body.strip():
            # LLM returned only a title or whitespace -> fail this scoop rather
            # than cache an empty article that build-manifest would reject.
            raise ValidationError("LLM 生成內容無正文")
        library.put_generation(conn, cache_key=key, cluster_id=cluster_id,
                               title=title, body=body, model=model, now=now)

    published = cluster.get("latest_published") or cluster.get("earliest_published")
    return {
        "title": title,
        "caption": body,                 # build-manifest maps content.body = caption
        "text": body,                    # also kept as a source_text copy
        "canonical_url": f"https://scoop.local/{cluster_id}",
        "source_id": SCOOP_SOURCE_ID,
        "url": cluster.get("representative_url"),
        "published_at": published,
        "discovered_at": now,
    }


def _run(args) -> int:
    now = datetime.now(timezone.utc).isoformat()
    llm_cfg = llm.load_config(args.llm_config)
    system_prompt = Path(args.prompt).read_text(encoding="utf-8")
    with library.connect(args.state) as conn:
        item = generate(conn, args.cluster_id, llm_cfg, system_prompt, now)
    write_line(item)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="generate-article",
        description="Synthesize one original article from a scoop's multi-source members.")
    parser.add_argument("--state", required=True, help="path to the SQLite state file")
    parser.add_argument("--cluster-id", required=True, help="cluster (scoop) id to generate")
    parser.add_argument("--llm-config", default="./configs/llm.yaml",
                        help="path to llm.yaml (base_url/model/api_key_env)")
    parser.add_argument("--prompt", default="./configs/scoop_prompt.zh.md",
                        help="path to the multi-source synthesis system prompt")
    args = parser.parse_args()
    cli.main_wrapper(lambda: _run(args))


if __name__ == "__main__":
    main()
