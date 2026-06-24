from cpost.core import cluster


def _it(url, title, **kw):
    item = {"canonical_url": url, "title": title, "source_id": kw.get("source_id"),
            "source_text": kw.get("source_text", ""), "published_at": kw.get("published_at")}
    return item


def _by_url(clusters):
    """Flatten to {canonical_url: cluster_id} for easy assertions."""
    out = {}
    for c in clusters:
        for url in c["members"]:
            out[url] = c["cluster_id"]
    return out


def test_same_event_across_sources_groups_with_source_count():
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a"),
        _it("https://b.com/9", "藝人A被爆隱婚生子內幕", source_id="site-b"),
        _it("https://c.com/7", "獨家：藝人A隱婚生子", source_id="site-c"),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3)
    assert len(clusters) == 1
    assert clusters[0]["member_count"] == 3
    assert clusters[0]["source_count"] == 3  # three independent sources


def test_unrelated_events_stay_separate():
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a"),
        _it("https://b.com/2", "某球隊奪冠賽後慶祝", source_id="site-b"),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.5)
    assert len(clusters) == 2


def test_isolated_item_is_its_own_cluster_source_count_one():
    clusters = cluster.cluster_items([_it("https://a.com/1", "唯一一則", source_id="site-a")])
    assert len(clusters) == 1
    assert clusters[0]["source_count"] == 1


def test_same_source_repeated_counts_as_one_source():
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a"),
        _it("https://a.com/2", "藝人A被爆隱婚生子續報", source_id="site-a"),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3)
    assert len(clusters) == 1
    assert clusters[0]["member_count"] == 2
    assert clusters[0]["source_count"] == 1  # same source -> not corroboration


def test_time_window_splits_same_title_far_apart():
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a",
            published_at="2026-06-01T00:00:00+00:00"),
        _it("https://b.com/2", "藝人A被爆隱婚生子", source_id="site-b",
            published_at="2026-06-18T00:00:00+00:00"),
    ]
    near = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=24 * 30)
    far = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=24)
    assert len(near) == 1  # within 30 days -> merged
    assert len(far) == 2   # 17 days apart, 24h window -> separate


def test_missing_published_at_imposes_no_time_constraint():
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a"),
        _it("https://b.com/2", "藝人A被爆隱婚生子", source_id="site-b",
            published_at="2026-06-18T00:00:00+00:00"),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=1)
    assert len(clusters) == 1  # one side has no time -> similarity alone decides


def test_deterministic_same_input_same_ids():
    items = [
        _it("https://b.com/9", "藝人A被爆隱婚生子內幕", source_id="site-b"),
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a"),
    ]
    a = cluster.cluster_items(items, similarity_threshold=0.3)
    b = cluster.cluster_items(list(reversed(items)), similarity_threshold=0.3)
    assert _by_url(a) == _by_url(b)  # order-independent, stable ids


def test_representative_is_longest_source_text():
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a", source_text="短"),
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            source_text="這是一段比較長的正文" * 10),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3)
    assert clusters[0]["representative_url"] == "https://b.com/2"


def test_mixed_naive_and_aware_published_at_does_not_crash():
    """Regression: a naive timestamp + an aware one must not raise TypeError."""
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a",
            published_at="2026-06-18T00:00:00"),          # naive (no offset)
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            published_at="2026-06-18T01:00:00+00:00"),    # aware
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=72)
    assert len(clusters) == 1  # naive assumed UTC -> 1h apart -> within window


def test_published_sorted_chronologically_not_lexically():
    """Mixed offsets: the true-latest instant wins, even when it sorts first as a string.

    +08:00 08:00 == 00:00 UTC (earlier instant) but sorts lexically AFTER
    +00:00 01:00 == 01:00 UTC (later instant). latest_published must be the
    +00:00 value (true newest), earliest the +08:00 value.
    """
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a",
            published_at="2026-06-18T08:00:00+08:00"),     # 00:00 UTC (earliest instant)
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            published_at="2026-06-18T01:00:00+00:00"),     # 01:00 UTC (latest instant)
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=72)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["latest_published"] == "2026-06-18T01:00:00+00:00"
    assert c["earliest_published"] == "2026-06-18T08:00:00+08:00"


def test_published_uniform_offset_sorts_unchanged():
    """No regression: with a single uniform offset, chrono order == lexical order."""
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a",
            published_at="2026-06-18T01:00:00+00:00"),
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            published_at="2026-06-18T05:00:00+00:00"),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=72)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["earliest_published"] == "2026-06-18T01:00:00+00:00"
    assert c["latest_published"] == "2026-06-18T05:00:00+00:00"


def test_published_missing_and_empty_skipped():
    """Members with missing/empty published_at are skipped, not sorted as values."""
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a"),  # missing -> None
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            published_at=""),                                            # empty string
        _it("https://c.com/3", "藝人A被爆隱婚生子細節", source_id="site-c",
            published_at="2026-06-18T03:00:00+08:00"),                   # only real value
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=72)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["earliest_published"] == "2026-06-18T03:00:00+08:00"
    assert c["latest_published"] == "2026-06-18T03:00:00+08:00"


def test_published_unparseable_excluded_not_earliest():
    """U5: a garbage published_at must never surface as the cluster's earliest/latest.

    published_at is ingested raw with no validation. An unparseable value
    (e.g. "2026/06/18 10:00") must be dropped from the earliest/latest
    computation -- not coerced to datetime.min (which would sort it FIRST and
    make garbage the displayed "oldest" timestamp).
    """
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a",
            published_at="2026/06/18 10:00"),                 # garbage, non-ISO
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            published_at="2026-06-18T08:00:00+00:00"),        # only real value
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=72)
    assert len(clusters) == 1
    c = clusters[0]
    # The valid value is both earliest and latest; the garbage never appears.
    assert c["earliest_published"] == "2026-06-18T08:00:00+00:00"
    assert c["latest_published"] == "2026-06-18T08:00:00+00:00"


def test_published_all_unparseable_yields_none():
    """U5: if no member has a parseable published_at, earliest/latest are None."""
    items = [
        _it("https://a.com/1", "藝人A被爆隱婚生子", source_id="site-a",
            published_at="2026/06/18 10:00"),                 # garbage
        _it("https://b.com/2", "藝人A被爆隱婚生子內幕", source_id="site-b",
            published_at="not-a-date"),                       # garbage
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3, time_window_hours=72)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["earliest_published"] is None
    assert c["latest_published"] is None


def test_empty_and_punctuation_only_titles_do_not_merge():
    items = [
        _it("https://a.com/1", "", source_id="site-a"),
        _it("https://b.com/2", "！！！", source_id="site-b"),
    ]
    clusters = cluster.cluster_items(items, similarity_threshold=0.3)
    assert len(clusters) == 2  # empty ngram sets -> jaccard 0 -> never merge


def test_ngram_size_affects_clustering():
    # Titles differ only in the last char: bigrams overlap a lot, 4-grams little.
    items = [
        _it("https://a.com/1", "藝人結婚了", source_id="site-a"),
        _it("https://b.com/2", "藝人結婚啦", source_id="site-b"),
    ]
    fine = cluster.cluster_items(items, ngram=2, similarity_threshold=0.5)
    coarse = cluster.cluster_items(items, ngram=4, similarity_threshold=0.5)
    assert len(fine) == 1    # bigram Jaccard 0.6 >= 0.5 -> merge
    assert len(coarse) == 2  # 4-gram Jaccard 0.33 < 0.5 -> split


def test_threshold_controls_merge():
    items = [
        _it("https://a.com/1", "貓咪影片爆紅", source_id="site-a"),
        _it("https://b.com/2", "狗狗影片爆紅", source_id="site-b"),
    ]
    loose = cluster.cluster_items(items, similarity_threshold=0.1)
    strict = cluster.cluster_items(items, similarity_threshold=0.9)
    assert len(loose) == 1   # low bar merges the near-identical titles
    assert len(strict) == 2  # high bar keeps them apart


# --- _ngrams edge cases (cluster.py:37-38) ----------------------------------


def test_ngrams_short_text_returns_itself():
    from cpost.core.cluster import _ngrams
    assert _ngrams("", 3) == set()          # empty → empty set
    assert _ngrams("a", 3) == {"a"}          # single char ≤ n → text itself
    assert _ngrams("ab", 3) == {"ab"}        # short text ≤ n → text itself
