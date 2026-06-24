import pytest

from cpost.browser.selector_recipe import load_backend, get_selector
from cpost.core.errors import ValidationError

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
        "  title: 'input'\n"  # missing body/save_draft/publish
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


# --- load_backend validation error branches (coverage fill) ---

def test_load_backend_file_not_found():
    with pytest.raises(ValidationError, match="backend config not found"):
        load_backend("/no/such/file.yaml")


def test_load_backend_invalid_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{invalid: yaml: too many: colons: }", encoding="utf-8")
    with pytest.raises(ValidationError, match="invalid backend yaml"):
        load_backend(str(bad))


def test_load_backend_non_dict_root(tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list", encoding="utf-8")
    with pytest.raises(ValidationError, match="backend config must be a mapping"):
        load_backend(str(bad))


def test_load_backend_missing_top_level_key(tmp_path):
    bad = tmp_path / "no_create.yaml"
    bad.write_text(
        "selectors:\n"
        "  title: 'input'\n"
        "  body: 'textarea'\n"
        "  save_draft: '#save'\n"
        "  publish: '#pub'\n"
        "verify:\n"
        "  draft_success_text: 'ok'\n"
        "  publish_success_text: 'ok'\n"
        "  login_required_url_contains: '/login'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="missing top-level key: create_url"):
        load_backend(str(bad))


def test_load_backend_selectors_not_mapping(tmp_path):
    bad = tmp_path / "selectors_list.yaml"
    bad.write_text(
        "create_url: 'https://x/create'\n"
        "selectors: not-a-dict\n"
        "verify:\n"
        "  draft_success_text: 'ok'\n"
        "  publish_success_text: 'ok'\n"
        "  login_required_url_contains: '/login'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="selectors' must be a mapping"):
        load_backend(str(bad))


def test_load_backend_verify_not_mapping(tmp_path):
    bad = tmp_path / "verify_list.yaml"
    bad.write_text(
        "create_url: 'https://x/create'\n"
        "selectors:\n"
        "  title: 'input'\n"
        "  body: 'textarea'\n"
        "  save_draft: '#save'\n"
        "  publish: '#pub'\n"
        "verify: just-a-string\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="verify' must be a mapping"):
        load_backend(str(bad))


def test_load_backend_missing_verify_key(tmp_path):
    bad = tmp_path / "no_login_marker.yaml"
    bad.write_text(
        "create_url: 'https://x/create'\n"
        "selectors:\n"
        "  title: 'input'\n"
        "  body: 'textarea'\n"
        "  save_draft: '#save'\n"
        "  publish: '#pub'\n"
        "verify:\n"
        "  draft_success_text: 'ok'\n"
        "  publish_success_text: 'ok'\n",
        # missing login_required_url_contains
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="missing verify key: login_required_url_contains"):
        load_backend(str(bad))
