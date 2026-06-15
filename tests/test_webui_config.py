import pytest

from core import webui_config
from core.errors import ValidationError


def test_missing_file_returns_defaults(tmp_path):
    cfg = webui_config.load(str(tmp_path / "nope.yaml"))
    assert cfg["limit"] == webui_config.DEFAULTS["limit"]
    assert cfg["start_url"] == ""


def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"start_url": "https://example.com/news", "limit": 5})
    assert saved["start_url"] == "https://example.com/news"
    loaded = webui_config.load(p)
    assert loaded["start_url"] == "https://example.com/news"
    assert loaded["limit"] == 5


def test_invalid_start_url_rejected(tmp_path):
    with pytest.raises(ValidationError):
        webui_config.save(str(tmp_path / "webui.yaml"), {"start_url": "not-a-url"})


def test_non_mapping_rejected(tmp_path):
    p = tmp_path / "webui.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        webui_config.load(str(p))


def test_bad_limit_rejected(tmp_path):
    with pytest.raises(ValidationError):
        webui_config.save(str(tmp_path / "webui.yaml"),
                          {"start_url": "https://x.com", "limit": "abc"})
