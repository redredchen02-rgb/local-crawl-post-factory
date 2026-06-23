from cpost.core import url_utils
from cpost.core.url_utils import make_source_id


# ---------------------------------------------------------------------------
# make_source_id
# ---------------------------------------------------------------------------

def test_make_source_id_basic():
    assert make_source_id("example.com") == "example-com"


def test_make_source_id_max_64_chars():
    assert len(make_source_id("a" * 100 + ".com")) <= 64


def test_make_source_id_special_chars_replaced():
    assert make_source_id("hello_world.org") == "hello-world-org"


def test_make_source_id_uppercase_lowercased():
    assert make_source_id("EXAMPLE.COM") == "example-com"


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

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
