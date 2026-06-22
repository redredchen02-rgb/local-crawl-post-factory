"""U8 (R3): WebUI read-only sources list on the settings page.

This round is display-only — no add/edit/delete controls (those are R3b).
"""

from fastapi.testclient import TestClient

from cpost.core import webui_config
from cpost.webui.app import create_app


def _client(tmp_path, sources):
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {"start_url": "https://example.com/news",
                                  "sources": sources})
    return TestClient(create_app(str(cfgp)))


def test_settings_lists_sources_when_configured(tmp_path):
    client = _client(tmp_path, [
        {"source_id": "51cg", "start_url": "https://51cg1.com/", "enabled": True},
        {"source_id": "ph", "start_url": "https://example.com/feed", "enabled": True},
    ])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "來源清單" in r.text
    # each source's id + start_url is shown
    assert "51cg" in r.text
    assert "https://51cg1.com/" in r.text
    assert "ph" in r.text
    assert "https://example.com/feed" in r.text
    # enabled badge present, no all-disabled / empty banners
    assert '<span class="pill ok">啟用</span>' in r.text
    assert "全部停用" not in r.text


def test_settings_is_read_only_no_crud_controls(tmp_path):
    """R3 is display-only: no add/edit/delete affordances for sources (that's R3b)."""
    client = _client(tmp_path, [
        {"source_id": "a", "start_url": "https://a.example.com/", "enabled": True},
    ])
    r = client.get("/settings")
    assert "唯讀" in r.text  # clearly labeled read-only
    # no per-source CRUD endpoints wired this round
    assert "/sources/add" not in r.text
    assert "/sources/delete" not in r.text
    assert "/sources/edit" not in r.text


def test_settings_empty_state_when_no_sources(tmp_path):
    client = _client(tmp_path, [])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "尚無設定來源" in r.text
    # no source badges rendered (no table rows)
    assert '<span class="pill ok">啟用</span>' not in r.text
    assert '<span class="pill">停用</span>' not in r.text


def test_settings_all_disabled_indication(tmp_path):
    client = _client(tmp_path, [
        {"source_id": "a", "start_url": "https://a.example.com/", "enabled": False},
        {"source_id": "b", "start_url": "https://b.example.com/", "enabled": False},
    ])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "全部停用" in r.text
    # both still listed, marked disabled
    assert "a" in r.text and "b" in r.text
    assert '<span class="pill">停用</span>' in r.text


def test_settings_disabled_source_is_demphasized(tmp_path):
    """A single disabled source among enabled ones is visibly de-emphasized + marked."""
    client = _client(tmp_path, [
        {"source_id": "live", "start_url": "https://live.example.com/", "enabled": True},
        {"source_id": "off", "start_url": "https://off.example.com/", "enabled": False},
    ])
    r = client.get("/settings")
    assert r.status_code == 200
    # not "all disabled" — one is enabled
    assert "全部停用" not in r.text
    # the disabled row carries the de-emphasis marker class + a 停用 badge
    assert "source-disabled" in r.text
    assert '<span class="pill">停用</span>' in r.text
    assert '<span class="pill ok">啟用</span>' in r.text


def test_settings_enabled_defaults_true_when_omitted(tmp_path):
    """A source without an explicit enabled flag is treated as enabled (mirrors config default)."""
    client = _client(tmp_path, [
        {"source_id": "noflag", "start_url": "https://noflag.example.com/"},
    ])
    r = client.get("/settings")
    assert r.status_code == 200
    assert '<span class="pill ok">啟用</span>' in r.text
    assert "全部停用" not in r.text


def _client_raw_yaml(tmp_path, yaml_text):
    """Write webui.yaml directly (bypassing save()'s source validation).

    save() rejects malformed sources, but load() never re-validates them, so a
    hand-edited YAML is the only way the bad shape reaches the read-only panel.
    """
    cfgp = tmp_path / "webui.yaml"
    cfgp.write_text(yaml_text, encoding="utf-8")
    return TestClient(create_app(str(cfgp)))


def test_settings_scalar_list_sources_does_not_500(tmp_path):
    """sources = list of scalars → 200 with a format-error hint, not a 500."""
    client = _client_raw_yaml(
        tmp_path,
        "start_url: https://example.com/news\nsources:\n  - foo\n  - bar\n",
    )
    r = client.get("/settings")
    assert r.status_code == 200
    assert "部分來源設定格式有誤" in r.text


def test_settings_scalar_string_sources_does_not_500(tmp_path):
    """sources = a scalar string → 200 with the format-error hint, not a 500."""
    client = _client_raw_yaml(
        tmp_path,
        "start_url: https://example.com/news\nsources: oops\n",
    )
    r = client.get("/settings")
    assert r.status_code == 200
    assert "部分來源設定格式有誤" in r.text


def test_settings_valid_dict_sources_still_render(tmp_path):
    """A valid dict-list still renders normally with no format-error hint."""
    client = _client_raw_yaml(
        tmp_path,
        "start_url: https://example.com/news\n"
        "sources:\n"
        "  - source_id: ok\n"
        "    start_url: https://ok.example.com/\n"
        "    enabled: true\n",
    )
    r = client.get("/settings")
    assert r.status_code == 200
    assert "ok" in r.text
    assert "https://ok.example.com/" in r.text
    assert "部分來源設定格式有誤" not in r.text
