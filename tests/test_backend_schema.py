import pytest

from browser.selector_recipe import load_backend, get_selector
from core.errors import ValidationError

VALID = "configs/backend.yaml"


def test_valid_backend_loads_and_selectors_resolve():
    cfg = load_backend(VALID)
    assert cfg["create_url"] == "https://example.com/admin/posts/create"
    assert get_selector(cfg, "title") == 'input[name="title"]'
    assert get_selector(cfg, "body") == 'textarea[name="content"]'
    assert get_selector(cfg, "save_draft") == 'button:has-text("儲存草稿")'
    assert get_selector(cfg, "publish") == 'button:has-text("發布")'


def test_missing_required_selector_raises_validation(tmp_path):
    bad = tmp_path / "backend.yaml"
    bad.write_text(
        "create_url: 'https://x/create'\n"
        "selectors:\n"
        "  title: 'input'\n"  # missing body/cover/save_draft/publish
        "verify:\n"
        "  draft_success_text: 'ok'\n"
        "  publish_success_text: 'ok'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_backend(str(bad))


def test_get_selector_unknown_raises_validation():
    cfg = load_backend(VALID)
    with pytest.raises(ValidationError):
        get_selector(cfg, "nope")
