import pytest

from core import url_utils, validators
from core.errors import ValidationError


def test_valid_url():
    assert validators.valid_url("https://example.com/news/a")
    assert not validators.valid_url("ftp://example.com/x")
    assert not validators.valid_url("not-a-url")
    assert not validators.valid_url("")


def test_require_url_rejects_bad():
    with pytest.raises(ValidationError):
        validators.require_url("nope")


def test_require_nonempty():
    assert validators.require_nonempty("  hi ", "title") == "hi"
    with pytest.raises(ValidationError):
        validators.require_nonempty("   ", "title")


def test_normalize_url_strips_trailing_slash_and_lowercases_host():
    assert url_utils.normalize_url("https://Example.com/News/A/") == "https://example.com/News/A"
    assert url_utils.normalize_url("https://example.com:443/x") == "https://example.com/x"
    assert url_utils.normalize_url("https://example.com/") == "https://example.com/"


def test_hashes_are_deterministic():
    a = url_utils.content_hash("https://x.com/a", "Title", "caption")
    b = url_utils.content_hash("https://x.com/a", "Title", "caption")
    assert a == b
    assert a != url_utils.content_hash("https://x.com/a", "Title", "other")


def test_title_hash_ignores_whitespace_and_case():
    assert url_utils.title_hash("Hello  World") == url_utils.title_hash("hello world")


def test_slug_is_filesystem_safe():
    assert url_utils.slug("https://example.com/news/a?x=1") == "https_example_com_news_a_x_1"
    assert url_utils.slug("") == "item"
