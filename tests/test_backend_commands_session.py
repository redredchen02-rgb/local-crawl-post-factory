"""U2: commands surface SessionExpiredError as exit-4 guidance; --retries passthrough."""

import contextlib
import json

import pytest

from core import cli
from core.errors import SessionExpiredError
from src import draft_post
from browser import backend_driver


@contextlib.contextmanager
def _fake_session(*a, **k):
    yield object()  # fake page; driver funcs are monkeypatched


def _manifest(tmp_path, status="package_built"):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({
        "post_id": "20260615_demo",
        "content": {"title": "標題", "body": "x"},
        "media": {},
        "backend": {"status": status},
    }), encoding="utf-8")
    return str(p)


def _args(tmp_path, **over):
    argv = ["--manifest", _manifest(tmp_path), "--backend", "configs/backend.yaml", "--headless"]
    for k, v in over.items():
        argv += [f"--{k.replace('_','-')}", str(v)]
    return draft_post._parse(argv)


def test_session_expired_maps_to_exit_4_with_guidance(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backend_driver, "session", _fake_session)

    def boom(*a, **k):
        raise SessionExpiredError("login session expired — re-run auth-login")

    monkeypatch.setattr(backend_driver, "create_draft", boom)
    code = cli.run(lambda: draft_post._run(_args(tmp_path)))
    err = capsys.readouterr().err
    assert code == 4
    assert "auth-login" in err


def test_retries_flag_passed_through(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_driver, "session", _fake_session)
    captured = {}

    def fake_create(page, cfg, manifest, manifest_path, **kw):
        captured.update(kw)
        return {"draft_url": "https://example.com/admin/posts/1/edit"}

    monkeypatch.setattr(backend_driver, "create_draft", fake_create)
    code = cli.run(lambda: draft_post._run(_args(tmp_path, retries=5)))
    assert code == 0
    assert captured["retries"] == 5
