"""generate-article: scoop -> synthesized original item (LLM stubbed, no network)."""

import json

import pytest

from cpost.core import cli, library, state
from cpost.core.errors import ValidationError
from cpost.core.url_utils import slug, title_hash
from cpost.core.validators import valid_url
from cpost.cli import build_manifest, generate_article


def _seed_cluster(conn, cluster_id="c1", sources=("src_a", "src_b")):
    """Seed a scoop with one member per source; return the member urls."""
    now = "2026-06-15T00:00:00Z"
    urls = []
    for i, sid in enumerate(sources):
        u = f"https://{sid}.example.com/news/{i}"
        urls.append(u)
        library.upsert(conn, canonical_url=u, title=f"原始標題{i}", now=now,
                       source_id=sid, source_text=f"{sid} 的正文內容片段。",
                       published_at="2026-06-15T10:00:00+08:00")
    library.assign_clusters(conn, [{
        "cluster_id": cluster_id, "members": urls,
        "member_count": len(urls), "source_count": len(set(sources)),
        "representative_url": urls[0], "representative_title": "代表性瓜標題",
        "earliest_published": "2026-06-15T10:00:00+08:00",
        "latest_published": "2026-06-15T12:00:00+08:00",
    }], now)
    return urls


def test_generate_synthesizes_item(tmp_path):
    cfg = {"model": "test-model"}
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", cfg, "系统提示", "2026-06-15T13:00:00Z",
            _chat=lambda c, sp, uc: "合成出的新標題\n\n这是综合多源后的正文内容。")
    assert item["title"] == "合成出的新標題"
    assert item["caption"] == "这是综合多源后的正文内容。"
    assert item["canonical_url"] == "https://scoop.cpost.local/c1"
    assert item["source_id"] == "scoop"


def test_title_falls_back_to_representative(tmp_path):
    cfg = {"model": "m"}
    # No clean first-line title (single long blob) -> fall back to cluster title.
    blob = "正" * 120
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(conn, "c1", cfg, "sp", "2026-06-15T13:00:00Z",
                                         _chat=lambda c, sp, uc: blob)
    assert item["title"] == "代表性瓜標題"
    assert item["caption"] == blob          # whole blob kept as body, never empty


def test_unknown_cluster_raises(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        with pytest.raises(ValidationError):
            generate_article.generate(conn, "nope", {"model": "m"}, "sp",
                                      "2026-06-15T13:00:00Z", _chat=lambda *a: "x")


def test_malformed_cluster_id_rejected(tmp_path):
    # Form-supplied ids that aren't the c_<hex> shape are rejected before they
    # reach the synthetic URL (defense in depth).
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        with pytest.raises(ValidationError):
            generate_article.generate(conn, "../etc/passwd", {"model": "m"}, "sp",
                                      "t", _chat=lambda *a: "x")


def test_title_only_output_raises(tmp_path):
    # LLM returns a single title line with no body -> fail rather than duplicate
    # the title into the body / cache an empty article.
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        with pytest.raises(ValidationError):
            generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t",
                                      _chat=lambda c, sp, uc: "只有一行標題沒有正文")


def test_whitespace_output_raises(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        with pytest.raises(ValidationError):
            generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t",
                                      _chat=lambda c, sp, uc: "   \n\n  \n")


def test_empty_members_raises(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        library.assign_clusters(conn, [{
            "cluster_id": "c_empty", "members": [], "member_count": 0,
            "source_count": 0, "representative_url": None,
            "representative_title": "空瓜", "earliest_published": None,
            "latest_published": None}], "2026-06-15T00:00:00Z")
        with pytest.raises(ValidationError):
            generate_article.generate(conn, "c_empty", {"model": "m"}, "sp", "t",
                                      _chat=lambda *a: "x")


def test_cache_hit_skips_llm(tmp_path):
    calls = {"n": 0}

    def chat(c, sp, uc):
        calls["n"] += 1
        return "標題行\n正文內容。"

    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t", _chat=chat)
        generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t", _chat=chat)
    assert calls["n"] == 1                   # second run hit the generations cache


def test_description_change_invalidates_cache_when_source_text_empty(tmp_path):
    # U6: description is the model's source text when source_text is empty. A
    # changed description (same url, source_text still empty) MUST produce a fresh
    # article (LLM re-called), not the stale cached one.
    cid = "c_desc"
    url = "https://src_a.example.com/news/0"
    now = "2026-06-15T00:00:00Z"
    articles = iter(["標題A\n描述A 的正文。", "標題B\n描述B 的正文。"])
    calls = {"n": 0}

    def chat(c, sp, uc):
        calls["n"] += 1
        return next(articles)

    def _seed(description):
        with library.connect(str(tmp_path / "s.sqlite")) as conn:
            library.upsert(conn, canonical_url=url, title="標題", now=now,
                           source_id="src_a", source_text="", description=description,
                           published_at="2026-06-15T10:00:00+08:00")
            library.assign_clusters(conn, [{
                "cluster_id": cid, "members": [url], "member_count": 1,
                "source_count": 1, "representative_url": url,
                "representative_title": "代表標題",
                "earliest_published": "2026-06-15T10:00:00+08:00",
                "latest_published": "2026-06-15T12:00:00+08:00"}], now)

    _seed("描述A")
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        item1 = generate_article.generate(conn, cid, {"model": "m"}, "sp", "t",
                                          _chat=chat)
    # Re-ingest the same url with a new description; source_text stays empty.
    _seed("描述B")
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        item2 = generate_article.generate(conn, cid, {"model": "m"}, "sp", "t",
                                          _chat=chat)
    assert calls["n"] == 2                    # LLM re-called, no stale cache hit
    assert item1["title"] == "標題A"
    assert item2["title"] == "標題B"          # fresh article reflects new input


def test_source_text_present_unchanged_still_caches(tmp_path):
    # Edge: when source_text is present and inputs are unchanged, behavior is
    # unchanged -- the second run still hits the cache (no LLM call).
    calls = {"n": 0}

    def chat(c, sp, uc):
        calls["n"] += 1
        return "標題行\n正文內容。"

    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)               # seeds non-empty source_text per member
        generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t", _chat=chat)
        generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t", _chat=chat)
    assert calls["n"] == 1


def test_missing_key_maps_to_validation_error(tmp_path, monkeypatch):
    # Real cpost.core.llm.chat path: base_url+model present, API key env absent ->
    # ValidationError (exit 2), NOT DependencyError(3). Message must not leak a key.
    monkeypatch.delenv("CPOST_LLM_API_KEY", raising=False)
    cfg = {"base_url": "https://llm.example.com/v1", "model": "m",
           "api_key_env": "CPOST_LLM_API_KEY"}
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        with pytest.raises(ValidationError) as exc:
            generate_article.generate(conn, "c1", cfg, "sp", "t")  # real llm.chat
    assert "CPOST_LLM_API_KEY" in str(exc.value)


def test_synthetic_item_feeds_build_manifest(tmp_path):
    out_dir = str(tmp_path / "out")
    log = str(tmp_path / "audit.jsonl")
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "建包用標題\n\n建包用的正文內容。")
    manifest_path = build_manifest.build(item, out_dir, log)
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    assert manifest["content"]["body"] == "建包用的正文內容。"   # caption -> content.body
    assert manifest["content"]["body"]                          # non-empty


# --- Unit 12 (R8): synthetic canonical identity + cross-run dedup ---

def test_canonical_is_self_describing_host_and_valid(tmp_path):
    # The synthetic identity uses the self-describing scoop.cpost.local host and
    # MUST pass valid_url (http(s)+hostname), or it never reaches build-manifest.
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "標題\n正文。")
    assert item["canonical_url"] == "https://scoop.cpost.local/c1"
    assert valid_url(item["canonical_url"])


def test_same_membership_same_canonical_across_runs(tmp_path):
    # Same cluster membership -> same canonical across two independent runs, so a
    # published row from run 1 makes run 2 see it as already-processed (dedup
    # round-trip through state.upsert/is_processed).
    cid = "c_0123456789ab"  # realistic c_<12hex>, exercises the slug budget
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn, cluster_id=cid)
        item1 = generate_article.generate(
            conn, cid, {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "標題\n正文。")

    canonical = item1["canonical_url"]
    with state.connect(str(tmp_path / "state.sqlite")) as st:
        # Before publish: not processed.
        assert not state.is_processed(st, canonical)
        # Simulate the publish that the manifest's source.canonical_url feeds.
        state.upsert(st, canonical_url=canonical, title=item1["title"],
                     title_hash=title_hash(item1["title"]),
                     status=state.PUBLISHED, now="t",
                     post_id=f"20260615_{slug(canonical)}",
                     published_url="https://blog.example.com/p/1")
        # Rerun yields the SAME canonical -> dedup sees it as already published.
        with library.connect(str(tmp_path / "s.sqlite")) as conn:
            item2 = generate_article.generate(
                conn, cid, {"model": "m"}, "sp", "t",
                _chat=lambda c, sp, uc: "完全不同的標題\n完全不同的正文。")
        assert item2["canonical_url"] == canonical
        assert state.is_processed(st, item2["canonical_url"])


def test_g5_source_id_is_scoop_not_member(tmp_path):
    # G5 decision: the synthesized article carries the fixed "scoop" source_id,
    # NOT any member's source_id (provenance lives in the library, not manifest).
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn, sources=("src_a", "src_b"))
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "標題\n正文。")
    assert item["source_id"] == generate_article.SCOOP_SOURCE_ID == "scoop"
    assert item["source_id"] not in ("src_a", "src_b")


def test_cli_run_unknown_cluster_exit_2(tmp_path):
    # End-to-end CLI contract: unknown cluster -> ValidationError -> exit 2.
    state = str(tmp_path / "s.sqlite")
    with library.connect(state):
        pass  # create the db/schema
    args = type("A", (), {"state": state, "cluster_id": "missing",
                          "llm_config": "./configs/llm.yaml",
                          "prompt": "./configs/scoop_prompt.zh.md"})()
    assert cli.run(lambda: generate_article._run(args)) == 2


# --- Unit 4: tags parsing, cache round-trip, and end-to-end flow ---------------

def test_tags_parsed_from_footer(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "人物A事件标题\n\n正文内容。\n---\n标签：人物A, 平台X, 内容类型Y")
    assert item["tags"] == ["人物A", "平台X", "内容类型Y"]


def test_tags_missing_footer_defaults_empty(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "标题行\n\n正文内容，没有标签行。")
    assert item["tags"] == []


def test_tags_cache_round_trip(tmp_path):
    calls = {"n": 0}

    def chat(c, sp, uc):
        calls["n"] += 1
        return "标题\n\n正文。\n---\n标签：A, B"

    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item1 = generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t",
                                          _chat=chat)
        item2 = generate_article.generate(conn, "c1", {"model": "m"}, "sp", "t",
                                          _chat=chat)
    assert calls["n"] == 1
    assert item1["tags"] == ["A", "B"]
    assert item2["tags"] == ["A", "B"]


def test_tags_empty_label_defaults_empty(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "标题\n\n正文。\n---\n标签：")
    assert item["tags"] == []


def test_body_intact_when_dash_rule_no_footer(tmp_path):
    body_with_rule = "标题\n\n## 前言\n\n内容。\n---\n\n## 后记\n\n更多内容。"
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: body_with_rule)
    assert item["tags"] == []
    assert "后记" in item["caption"]


def test_tags_trailing_comma_filtered(tmp_path):
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "标题\n\n正文。\n---\n标签：A, , B,")
    assert item["tags"] == ["A", "B"]


def test_migration_legacy_row_tags_empty(tmp_path):
    db_path = str(tmp_path / "s.sqlite")
    import sqlite3 as _sqlite3
    with _sqlite3.connect(db_path) as raw:
        raw.execute(
            "CREATE TABLE IF NOT EXISTS generations "
            "(cache_key TEXT PRIMARY KEY, cluster_id TEXT, title TEXT NOT NULL, "
            "body TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL)"
        )
        raw.execute(
            "INSERT INTO generations VALUES (?, ?, ?, ?, ?, ?)",
            ("old_key", "c1", "旧标题", "旧正文", "m", "2026-01-01T00:00:00Z"),
        )
        raw.commit()
    with library.connect(db_path) as conn:
        result = library.get_generation(conn, "old_key")
    assert result is not None
    assert result["tags"] == []


def test_tags_flow_to_manifest(tmp_path):
    out_dir = str(tmp_path / "out")
    log = str(tmp_path / "audit.log")
    with library.connect(str(tmp_path / "s.sqlite")) as conn:
        _seed_cluster(conn)
        item = generate_article.generate(
            conn, "c1", {"model": "m"}, "sp", "t",
            _chat=lambda c, sp, uc: "建包标题\n\n建包正文。\n---\n标签：人物A, 平台X")
    manifest_path = build_manifest.build(item, out_dir, log)
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    assert manifest["content"]["tags"] == ["人物A", "平台X"]


def test_run_success_path_writes_line(tmp_path):
    """_run() success path exercises write_line + return 0."""
    db = str(tmp_path / "s.sqlite")
    llm_cfg = tmp_path / "llm.yaml"
    llm_cfg.write_text("model: m\nbase_url: http://localhost:9999\napi_key_env: X\n", encoding="utf-8")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("system prompt", encoding="utf-8")
    with library.connect(db):
        pass
    from unittest.mock import patch
    import io
    args = type("A", (), {"state": db, "cluster_id": "c1",
                          "llm_config": str(llm_cfg), "prompt": str(prompt)})()
    item = {"title": "T", "body": "B", "canonical_url": "https://x.com/1", "source_id": "s"}
    with patch.object(generate_article, "generate", return_value=item), \
         patch("sys.stdout", io.StringIO()):
        code = cli.run(lambda: generate_article._run(args))
    assert code == 0
