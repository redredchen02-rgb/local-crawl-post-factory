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
