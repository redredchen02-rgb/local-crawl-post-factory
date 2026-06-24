from cpost.core import external_search


NOW = "2026-06-18T00:00:00+00:00"


def test_search_disabled_returns_empty():
    cfg = {"external_search_enabled": False}
    result = external_search.search_cluster({}, [], cfg)
    assert result == {
        "external_article_count": None,
        "external_source_count": None,
        "external_latest_at": None,
        "search_volume_proxy": None,
    }


def test_search_enabled_no_title():
    cfg = {"external_search_enabled": True}
    result = external_search.search_cluster({"cluster_id": "c_xxx"}, [], cfg)
    assert all(v is None for v in result.values())


def test_search_enabled_with_title():
    cfg = {"external_search_enabled": True}
    cluster = {"cluster_id": "c_xxx", "representative_title": "AI 最新發展"}
    result = external_search.search_cluster(cluster, [], cfg)
    assert all(v is None for v in result.values())


def test_best_title_uses_longest():
    members = [
        {"title": "短"},
        {"title": "這是一個中等長度的標題"},
        {"title": "最長標題在這裡：AI 最新發展趨勢分析報告"},
    ]
    assert external_search._best_title(members) == "最長標題在這裡：AI 最新發展趨勢分析報告"


def test_best_title_empty_returns_none():
    members = [{"title": ""}, {"title": None}]
    assert external_search._best_title(members) is None


def test_best_title_no_title_field():
    members = [{}, {}]
    assert external_search._best_title(members) is None


def test_search_with_member_title_fallback():
    cfg = {"external_search_enabled": True}
    cluster = {"cluster_id": "c_xxx"}  # no representative_title
    members = [{"title": "會員文章標題"}, {"title": "另一篇會員文章"}]
    result = external_search.search_cluster(cluster, members, cfg)
    assert all(v is None for v in result.values())
