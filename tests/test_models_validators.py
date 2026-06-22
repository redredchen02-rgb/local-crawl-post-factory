import pytest

from cpost.core import manifest as mf
from cpost.core import url_utils, validators
from cpost.core.errors import ValidationError


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


# --- U12: set_backend published_url clear-vs-leave-unchanged ------------------

def test_set_backend_explicit_none_clears_published_url():
    """Rollback consistency: after publish (sets url) -> rollback (status back to
    draft_verified, url cleared), the manifest must not still carry a stale url."""
    m = {"backend": {}}
    mf.set_backend(m, status="published", published_url="https://x.com/p/1")
    assert m["backend"]["published_url"] == "https://x.com/p/1"
    mf.set_backend(m, status="draft_verified", published_url=None)
    assert m["backend"]["status"] == "draft_verified"
    assert m["backend"]["published_url"] is None


def test_set_backend_unset_leaves_published_url_unchanged():
    m = {"backend": {"published_url": "https://x.com/p/1"}}
    mf.set_backend(m, published_url=mf.UNSET)
    assert m["backend"]["published_url"] == "https://x.com/p/1"


def test_set_backend_default_leaves_published_url_unchanged():
    """Existing callers (draft/verify) that omit published_url must not clear it."""
    m = {"backend": {"published_url": "https://x.com/p/1"}}
    mf.set_backend(m, status="draft_verified")
    assert m["backend"]["published_url"] == "https://x.com/p/1"
    assert m["backend"]["status"] == "draft_verified"
