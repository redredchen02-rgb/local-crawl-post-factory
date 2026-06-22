from cpost.core import url_utils


def test_normalize_url_ipv6_with_port_keeps_brackets():
    assert (
        url_utils.normalize_url("https://[2001:db8::1]:8443/path/")
        == "https://[2001:db8::1]:8443/path"
    )


def test_normalize_url_ipv6_without_port_keeps_brackets():
    assert url_utils.normalize_url("http://[::1]/a") == "http://[::1]/a"


def test_normalize_url_ipv6_default_port_dropped_brackets_kept():
    assert url_utils.normalize_url("https://[2001:db8::1]:443/x") == "https://[2001:db8::1]/x"


def test_normalize_url_ipv6_round_trips():
    once = url_utils.normalize_url("https://[2001:db8::1]:8443/path/")
    assert url_utils.normalize_url(once) == once


def test_normalize_url_ordinary_hostname_unchanged():
    assert url_utils.normalize_url("https://example.com/a") == "https://example.com/a"
    assert url_utils.normalize_url("https://Example.com:8080/b/") == "https://example.com:8080/b"
