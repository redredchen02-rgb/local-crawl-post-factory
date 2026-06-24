from cpost.core import output_table


def test_terminal_headers_present():
    s = [{"cluster_id": "c_aaa", "source_count": 3, "score": 0.85,
          "freshness": 0.9, "importance": 0.7, "traffic_potential": 0.5,
          "cross_site_coverage": 0.6, "representative_title": "Test"}]
    out = output_table.terminal(s)
    assert "cluster_id" in out
    assert "fresh" in out
    assert "import" in out
    assert "traffic" in out
    assert "coverage" in out
    assert "score" in out


def test_terminal_missing_4d_fields():
    s = [{"cluster_id": "c_bbb", "source_count": 1, "score": 0.5}]
    out = output_table.terminal(s)
    assert "—" in out  # missing fields shown as —
    assert "1 scoops total" in out


def test_terminal_empty():
    out = output_table.terminal([], max_rows=10)
    assert "0 scoops total" in out


def test_markdown_headers_present():
    s = [{"cluster_id": "c_ccc", "source_count": 2, "score": 0.75,
          "freshness": 0.8, "importance": 0.6, "traffic_potential": 0.4,
          "cross_site_coverage": 0.5, "representative_title": "Markdown Test"}]
    out = output_table.markdown(s)
    assert "| # | cluster_id" in out
    assert "freshness" in out
    assert "importance" in out
    assert "| 1 |" in out
    assert "Markdown Test" in out


def test_markdown_empty():
    out = output_table.markdown([])
    assert out == ""


def test_val_none_returns_dash():
    assert output_table._val(None) == "—"


def test_val_float_rounds():
    assert output_table._val(0.12345, ndigits=3) == "0.123"


def test_bar_zero():
    assert "▱" * 8 in output_table._bar(0.0)


def test_bar_full():
    assert "▰" * 8 in output_table._bar(1.0)


def test_bar_half():
    bar = output_table._bar(0.5)
    assert bar.count("▰") == 4
    assert bar.count("▱") == 4


def test_max_rows_truncates():
    s = [{"cluster_id": f"c_{i}", "source_count": 1, "score": 0.1} for i in range(30)]
    out = output_table.terminal(s, max_rows=5)
    # should show "showing 5" not "showing 30" or "showing 20"
    assert "showing 5" in out
