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


def test_load_raw_does_not_resolve_paths(tmp_path):
    """load_raw() keeps path fields in their on-disk (relative) form; load() resolves."""
    p = _write(tmp_path, "state_path: ../state/db.sqlite\nout_dir: ../out\n")
    raw = webui_config.load_raw(str(p))
    assert raw["state_path"] == "../state/db.sqlite"
    assert raw["out_dir"] == "../out"
    assert webui_config.load(str(p))["out_dir"].startswith("/")  # contrast: load resolves


def test_settings_save_via_load_raw_keeps_paths_portable(tmp_path):
    """Portability (#3): the settings save flow merges over load_raw() (unresolved)
    + a form edit. Infra paths must stay portable -- never baked to absolute."""
    import yaml
    p = _write(tmp_path,
               "start_url: https://example.com/news\n"
               "state_path: ../state/db.sqlite\n"
               "out_dir: ../out\n"
               "download_dir: ../out/assets\n"
               "audit_log: ../logs/audit.jsonl\n"
               "storage_state: ../auth/ss.json\n")
    # Replicates webui/routers/settings_auth.save_settings.
    webui_config.save(str(p), {**webui_config.load_raw(str(p)),
                               "start_url": "https://example.com/changed"})
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["start_url"] == "https://example.com/changed"  # the real edit persists
    for field in ("state_path", "out_dir", "download_dir", "audit_log", "storage_state"):
        assert not str(raw[field]).startswith("/"), \
            f"{field} persisted as absolute machine path: {raw[field]!r}"


def test_settings_save_via_load_does_bake_absolute_paths(tmp_path):
    """Regression witness: merging over load() (resolved) -- the OLD behavior --
    bakes machine-absolute paths into the file. This is exactly what load_raw avoids."""
    import yaml
    p = _write(tmp_path, "out_dir: ../out\nstate_path: ../state/db.sqlite\n")
    webui_config.save(str(p), {**webui_config.load(str(p)), "limit": 5})
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert str(raw["out_dir"]).startswith("/")  # documents WHY the handler must use load_raw


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


def test_auto_pipeline_int_truthy_coerced_to_true(tmp_path):
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"auto_pipeline": 1})
    assert saved["auto_pipeline"] is True


def test_auto_pipeline_int_falsy_coerced_to_false(tmp_path):
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"auto_pipeline": 0})
    assert saved["auto_pipeline"] is False


def test_auto_pipeline_invalid_type_rejected(tmp_path):
    p = str(tmp_path / "webui.yaml")
    with pytest.raises(ValidationError, match="auto_pipeline"):
        webui_config.save(p, {"auto_pipeline": ["oops"]})


# --- max_text_chars knob (Unit 2, R3) ---

def test_max_text_chars_defaults_20000(tmp_path):
    cfg = webui_config.load(str(tmp_path / "nope.yaml"))
    assert cfg["max_text_chars"] == 20000


def test_max_text_chars_roundtrip(tmp_path):
    p = str(tmp_path / "webui.yaml")
    saved = webui_config.save(p, {"max_text_chars": 0})  # 0 = no clamp
    assert saved["max_text_chars"] == 0
    assert webui_config.load(p)["max_text_chars"] == 0


def test_max_text_chars_negative_rejected(tmp_path):
    with pytest.raises(ValidationError, match="max_text_chars"):
        webui_config.save(str(tmp_path / "webui.yaml"), {"max_text_chars": -1})


# --- R4: legacy cover_* keys are ignored, not errors ---

def test_legacy_cover_keys_ignored(tmp_path):
    """R4 backward-compat: an old config carrying now-removed cover_* keys must
    load without error, and those keys must not leak into the loaded config."""
    p = _write(tmp_path,
               "start_url: https://example.com/news\n"
               "cover_enabled: false\n"
               "cover_retries: 3\n"
               "cover_backoff_sec: 1.5\n"
               "cover_download_concurrency: 9\n"
               "watermark_config: ./configs/watermark.yaml\n")
    cfg = webui_config.load(str(p))
    assert cfg["start_url"] == "https://example.com/news"
    for k in ("cover_enabled", "cover_retries", "cover_backoff_sec",
              "cover_download_concurrency", "watermark_config"):
        assert k not in cfg
