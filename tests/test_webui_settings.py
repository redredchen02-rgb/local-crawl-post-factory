"""R3 + R3b: WebUI sources list with full CRUD controls on the settings page."""

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
    assert "啟用" in r.text
    assert "全部停用" not in r.text


def test_settings_has_crud_controls(tmp_path):
    """R3b: CRUD endpoints are wired — add form, delete and toggle buttons present."""
    client = _client(tmp_path, [
        {"source_id": "a", "start_url": "https://a.example.com/", "enabled": True},
    ])
    r = client.get("/settings")
    assert r.status_code == 200
    # add form endpoint exists
    assert "/sources/add" in r.text
    # per-source toggle + delete buttons exist
    assert "/sources/toggle/a" in r.text
    assert "/sources/delete/a" in r.text


def test_settings_empty_state_when_no_sources(tmp_path):
    client = _client(tmp_path, [])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "尚無設定來源" in r.text
    # no source badges rendered (no table rows) — check text content absence is tricky
    # since "啟用"/"停用" appears in button labels too; check for source-list context
    assert "尚無設定來源" in r.text


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
    assert "pill error" in r.text  # disabled badge uses pill error class


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
    # the disabled row carries the de-emphasis marker class + 停用/啟用 badges
    assert "source-disabled" in r.text
    assert "停用" in r.text
    assert "啟用" in r.text


def test_settings_enabled_defaults_true_when_omitted(tmp_path):
    """A source without an explicit enabled flag is treated as enabled (mirrors config default)."""
    client = _client(tmp_path, [
        {"source_id": "noflag", "start_url": "https://noflag.example.com/"},
    ])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "啟用" in r.text
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


# --- B3: CRUD routes ---------------------------------------------------------

def test_sources_add_valid(tmp_path):
    client = _client(tmp_path, [])
    r = client.post("/sources/add", data={
        "source_id": "newsite", "start_url": "https://newsite.example.com/", "enabled": "on"})
    assert r.status_code == 200
    assert "newsite" in r.text
    assert "https://newsite.example.com/" in r.text


def test_sources_add_duplicate_source_id_400(tmp_path):
    client = _client(tmp_path, [
        {"source_id": "dup", "start_url": "https://dup.example.com/"},
    ])
    r = client.post("/sources/add", data={
        "source_id": "dup", "start_url": "https://other.example.com/", "enabled": "on"})
    assert r.status_code == 400
    assert "duplicate" in r.text.lower() or "dup" in r.text


def test_sources_add_bad_url_400(tmp_path):
    client = _client(tmp_path, [])
    r = client.post("/sources/add", data={
        "source_id": "bad", "start_url": "not-a-url", "enabled": "on"})
    assert r.status_code == 400


def test_sources_delete_removes_entry(tmp_path):
    from cpost.core import webui_config as wc
    cfgp = tmp_path / "webui.yaml"
    wc.save(str(cfgp), {"start_url": "https://example.com", "sources": [
        {"source_id": "keep", "start_url": "https://keep.example.com/"},
        {"source_id": "gone", "start_url": "https://gone.example.com/"},
    ]})
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/sources/delete/gone")
    assert r.status_code == 200
    assert "gone" not in r.text
    assert "keep" in r.text
    # config persisted
    cfg = wc.load(str(cfgp))
    ids = [s["source_id"] for s in cfg.get("sources", [])]
    assert "gone" not in ids
    assert "keep" in ids


def test_sources_delete_unknown_404(tmp_path):
    client = _client(tmp_path, [
        {"source_id": "a", "start_url": "https://a.example.com/"},
    ])
    r = client.post("/sources/delete/nonexistent")
    assert r.status_code == 404


def test_sources_toggle_disables_enabled_source(tmp_path):
    from cpost.core import webui_config as wc
    cfgp = tmp_path / "webui.yaml"
    wc.save(str(cfgp), {"start_url": "https://example.com", "sources": [
        {"source_id": "live", "start_url": "https://live.example.com/", "enabled": True},
    ]})
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/sources/toggle/live")
    assert r.status_code == 200
    # entry now has explicit enabled: False in the config
    cfg = wc.load(str(cfgp))
    src = next(s for s in cfg["sources"] if s["source_id"] == "live")
    assert src["enabled"] is False


def test_sources_toggle_stores_explicit_false_not_omitted(tmp_path):
    """toggle-off must write enabled: False explicitly — omitting the key lets
    the template default-true re-enable the source silently."""
    import yaml
    from cpost.core import webui_config as wc
    cfgp = tmp_path / "webui.yaml"
    wc.save(str(cfgp), {"start_url": "https://example.com", "sources": [
        {"source_id": "s", "start_url": "https://s.example.com/", "enabled": True},
    ]})
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    client = TestClient(create_app(str(cfgp)))
    client.post("/sources/toggle/s")
    raw = yaml.safe_load(cfgp.read_text(encoding="utf-8"))
    src = next(s for s in raw["sources"] if s["source_id"] == "s")
    assert "enabled" in src, "enabled key must be explicitly present after toggle-off"
    assert src["enabled"] is False


def test_sources_edit_preserves_unknown_keys(tmp_path):
    """Editing a source must not drop unknown per-source keys (in-place update)."""
    from cpost.core import webui_config as wc
    cfgp = tmp_path / "webui.yaml"
    # Write a source with an extra key directly (bypass save() which strips unknowns at cfg level)
    raw_yaml = (
        "start_url: https://example.com/\n"
        "sources:\n"
        "  - source_id: s\n"
        "    start_url: https://s.example.com/\n"
        "    enabled: true\n"
        "    item_regex: /news/\n"  # known per-source override, preserved
    )
    cfgp.write_text(raw_yaml, encoding="utf-8")
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    client = TestClient(create_app(str(cfgp)))
    r = client.post("/sources/edit/s", data={
        "start_url": "https://s-new.example.com/", "enabled": "on"})
    assert r.status_code == 200
    cfg = wc.load(str(cfgp))
    src = next(s for s in cfg["sources"] if s["source_id"] == "s")
    assert src["start_url"] == "https://s-new.example.com/"
    assert src.get("item_regex") == "/news/", "item_regex must survive the edit"


def test_sources_portability_after_crud(tmp_path):
    """After add + toggle, webui.yaml must contain no absolute paths."""
    from cpost.core import webui_config as wc
    cfgp = tmp_path / "webui.yaml"
    wc.save(str(cfgp), {"start_url": "https://example.com", "sources": []})
    from fastapi.testclient import TestClient
    from cpost.webui.app import create_app
    client = TestClient(create_app(str(cfgp)))
    client.post("/sources/add", data={
        "source_id": "check", "start_url": "https://check.example.com/", "enabled": "on"})
    client.post("/sources/toggle/check")
    text = cfgp.read_text(encoding="utf-8")
    assert str(tmp_path) not in text, "absolute path leaked into webui.yaml after CRUD"
