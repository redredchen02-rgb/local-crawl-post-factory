"""AI 文章生成路由 + LLM client（network mock 掉，纯逻辑）。"""

import json

import pytest
from fastapi.testclient import TestClient

from cpost.core import llm, webui_config
from cpost.core.errors import ExternalError, ValidationError
from cpost.webui.app import create_app
from cpost.webui.routers import packages as packages_router


def _setup(tmp_path, *, source_text="原始素材内文，事件经过……", with_source=True):
    out = tmp_path / "out"
    pkg = out / "20260615_demo"
    pkg.mkdir(parents=True)
    (pkg / "caption.txt").write_text("舊文案", encoding="utf-8")
    if with_source:
        (pkg / "source_text.txt").write_text(source_text, encoding="utf-8")
    (pkg / "manifest.json").write_text(json.dumps({
        "post_id": "20260615_demo",
        "source": {"canonical_url": "https://example.com/news/a"},
        "content": {"title": "原標題", "body": "舊文案", "tags": [], "category": None},
        "media": {},
        "backend": {"status": "package_built", "draft_url": None,
                    "published_url": None, "remote_id": None},
        "audit": {},
    }), encoding="utf-8")
    cfgp = tmp_path / "webui.yaml"
    webui_config.save(str(cfgp), {
        "start_url": "https://example.com", "out_dir": str(out),
        "state_path": str(tmp_path / "state.sqlite"),
        "audit_log": str(tmp_path / "audit.jsonl"),
        "storage_state": str(tmp_path / "nostate.json"),
    })
    return TestClient(create_app(str(cfgp))), pkg


def test_generate_writes_caption_and_body(tmp_path, monkeypatch):
    client, pkg = _setup(tmp_path)
    captured = {}

    def fake_chat(cfg, system_prompt, user_content):
        captured["system"] = system_prompt
        captured["user"] = user_content
        return "## 生成的文章\n完整成品正文"

    monkeypatch.setattr(packages_router.llm, "chat", fake_chat)
    r = client.post("/packages/20260615_demo/generate")
    assert r.status_code == 200
    assert "生成完成" in r.text
    # caption.txt 与 manifest body 都被替换成生成结果
    assert (pkg / "caption.txt").read_text(encoding="utf-8") == "## 生成的文章\n完整成品正文"
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert m["content"]["body"] == "## 生成的文章\n完整成品正文"
    # 素材取自 source_text.txt（全文），且带上原标题
    assert "原始素材内文" in captured["user"]
    assert "原標題" in captured["user"]
    # 系统提示来自规范档
    assert "排版" in captured["system"] or "标题" in captured["system"]


def test_generate_unknown_package_404(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path)
    monkeypatch.setattr(packages_router.llm, "chat", lambda *a, **k: "x")
    assert client.post("/packages/nope/generate").status_code == 404


def test_generate_empty_material_400(tmp_path, monkeypatch):
    client, pkg = _setup(tmp_path, with_source=False)
    (pkg / "caption.txt").write_text("   ", encoding="utf-8")
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    m["content"]["body"] = ""
    (pkg / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(packages_router.llm, "chat", lambda *a, **k: "x")
    r = client.post("/packages/20260615_demo/generate")
    assert r.status_code == 400
    assert "素材" in r.text


def test_generate_llm_error_502(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path)

    def boom(*a, **k):
        raise ExternalError("端点连线失败")

    monkeypatch.setattr(packages_router.llm, "chat", boom)
    r = client.post("/packages/20260615_demo/generate")
    assert r.status_code == 502
    assert "生成失敗" in r.text


def test_llm_load_config_resolves_prompt_path(tmp_path):
    (tmp_path / "p.md").write_text("系统规范内容", encoding="utf-8")
    (tmp_path / "llm.yaml").write_text(
        "base_url: https://x/v1\nmodel: m\nprompt_path: ./p.md\n", encoding="utf-8")
    cfg = llm.load_config(str(tmp_path / "llm.yaml"))
    assert cfg["base_url"] == "https://x/v1"
    assert cfg["model"] == "m"
    assert llm.load_system_prompt(cfg) == "系统规范内容"


def test_llm_chat_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CPOST_LLM_API_KEY", raising=False)
    cfg = {"base_url": "https://x/v1", "model": "m",
           "api_key_env": "CPOST_LLM_API_KEY", "user_agent": "UA"}
    with pytest.raises(ValidationError):
        llm.chat(cfg, "sys", "user")
