from pathlib import Path

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


def test_cover_retry_keys_roundtrip(tmp_path):
    # R2: cover retry knobs must survive load() so the WebUI pipeline can use them.
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"cover_retries": 2, "cover_backoff_sec": 0.5})
    assert saved["cover_retries"] == 2 and saved["cover_backoff_sec"] == 0.5
    loaded = webui_config.load(p)
    assert loaded["cover_retries"] == 2
    assert loaded["cover_backoff_sec"] == 0.5


def test_invalid_cover_retries_rejected(tmp_path):
    with pytest.raises(ValidationError):
        webui_config.save(str(tmp_path / "webui.yaml"), {"cover_retries": "abc"})


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


# --- U6 (R7): path portability + env overrides + credential safety -----------

def _write(tmp_path, body=""):
    p = tmp_path / "webui.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_relative_paths_resolved_against_config_dir(tmp_path):
    """A relative state_path resolves under the config file's directory, not cwd."""
    p = _write(tmp_path, "state_path: ./state/db.sqlite\n")
    cfg = webui_config.load(str(p))
    assert cfg["state_path"] == str((tmp_path / "state" / "db.sqlite").resolve())


def test_absolute_paths_preserved(tmp_path):
    abs_state = str((tmp_path / "elsewhere" / "db.sqlite"))
    p = _write(tmp_path, f"state_path: {abs_state}\n")
    cfg = webui_config.load(str(p))
    assert cfg["state_path"] == str(Path(abs_state).resolve())


def test_env_override_takes_precedence(tmp_path, monkeypatch):
    p = _write(tmp_path, "state_path: ./state/db.sqlite\n")
    target = tmp_path / "env" / "custom.sqlite"
    monkeypatch.setenv("CPOST_STATE_PATH", str(target))
    cfg = webui_config.load(str(p))
    assert cfg["state_path"] == str(target.resolve())


def test_default_resolves_when_no_env_no_yaml(tmp_path):
    p = _write(tmp_path)  # empty file -> defaults
    cfg = webui_config.load(str(p))
    assert cfg["out_dir"] == str((tmp_path / "out").resolve())


def test_empty_env_override_rejected(tmp_path, monkeypatch):
    p = _write(tmp_path)
    monkeypatch.setenv("CPOST_STATE_PATH", "   ")
    with pytest.raises(ValidationError):
        webui_config.load(str(p))


def test_storage_state_inside_out_dir_rejected(tmp_path):
    p = _write(tmp_path, "out_dir: ./out\nstorage_state: ./out/ss.json\n")
    with pytest.raises(ValidationError):
        webui_config.load(str(p))


# --- auto_pipeline bool field ---

def test_auto_pipeline_defaults_false(tmp_path):
    cfg = webui_config.load(str(tmp_path / "nope.yaml"))
    assert cfg["auto_pipeline"] is False


def test_auto_pipeline_roundtrip_true(tmp_path):
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"auto_pipeline": True})
    assert saved["auto_pipeline"] is True
    loaded = webui_config.load(p)
    assert loaded["auto_pipeline"] is True


def test_auto_pipeline_form_on_coerced_to_true(tmp_path):
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"auto_pipeline": "on"})
    assert saved["auto_pipeline"] is True


def test_auto_pipeline_form_absent_coerced_to_false(tmp_path):
    # Unchecked checkbox sends nothing; we pass "" to simulate absent field
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"auto_pipeline": ""})
    assert saved["auto_pipeline"] is False


def test_auto_pipeline_yaml_native_bool(tmp_path):
    p = _write(tmp_path, "auto_pipeline: true\n")
    cfg = webui_config.load(str(p))
    assert cfg["auto_pipeline"] is True
