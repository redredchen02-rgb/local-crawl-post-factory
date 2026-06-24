"""Transport-level coverage for ``cpost.core.llm.chat`` (R12 / U5).

These exercise the outbound HTTP branch (``urllib.request.urlopen`` + the
``HTTPError``/``URLError`` -> ``ExternalError`` mapping and the response-parsing
guards) without touching the real network: ``urllib.request.urlopen`` is
monkeypatched with a controllable fake transport, following the repo's
test-isolation convention of patching the symbol on the module under test.
"""

import io
import json
import socket
import urllib.error

import pytest

from cpost.core import llm
from cpost.core.errors import ExternalError, ValidationError

_CFG = {
    "base_url": "https://llm.example.com/v1",
    "model": "m",
    "api_key_env": "CPOST_LLM_API_KEY",
    "user_agent": "UA/1.0",
    "temperature": 0.7,
    "max_tokens": 4096,
    "timeout_sec": 120,
}


class _FakeResponse:
    """Minimal stand-in for the urlopen() context-manager response."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _ok_payload(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    # The transport branch is only reached once config + key guards pass.
    monkeypatch.setenv("CPOST_LLM_API_KEY", "secret-key")


def _patch_urlopen(monkeypatch, handler):
    monkeypatch.setattr(llm.urllib.request, "urlopen", handler)


def test_normal_200_parses_content(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data)
        return _FakeResponse(_ok_payload("  改写后的成品文章正文  "))

    _patch_urlopen(monkeypatch, fake_urlopen)

    out = llm.chat(_CFG, "system rules", "user material")

    # choices[0].message.content parsed + stripped.
    assert out == "改写后的成品文章正文"
    # Request was constructed against /chat/completions with auth + payload.
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer secret-key"
    assert captured["timeout"] == 120
    assert captured["body"]["model"] == "m"
    assert captured["body"]["messages"][0]["content"] == "system rules"
    assert captured["body"]["messages"][1]["content"] == "user material"


def test_http_error_maps_to_external_error_with_detail(monkeypatch):
    detail = b'{"error":"insufficient_quota"}'

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            url=request.full_url, code=429, msg="Too Many Requests",
            hdrs=None, fp=io.BytesIO(detail),
        )

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError) as exc:
        llm.chat(_CFG, "sp", "uc")
    msg = str(exc.value)
    assert "429" in msg
    # Diagnostic detail from the error body is surfaced.
    assert "insufficient_quota" in msg


def test_url_error_maps_to_external_error_with_reason(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError) as exc:
        llm.chat(_CFG, "sp", "uc")
    assert "connection refused" in str(exc.value)


def test_read_timeout_maps_to_external_error_not_timeout_error(monkeypatch):
    # Server sends headers then never sends the body: urlopen() returns, but
    # resp.read() blocks until timeout_sec and raises socket.timeout (an alias of
    # TimeoutError). U10: this must surface as ExternalError (exit 4), not as a raw
    # TimeoutError that the CLI would map to exit 5 / the webui to a 500.
    class _StallingResponse(_FakeResponse):
        def read(self):
            raise socket.timeout("timed out")

    def fake_urlopen(request, timeout=None):
        return _StallingResponse(b"")

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError) as exc:
        llm.chat(_CFG, "sp", "uc")
    # Exit-4 contract the CLI (generate-article) and webui /generate rely on.
    assert exc.value.exit_code == 4
    assert "逾时" in str(exc.value)
    # The configured budget is surfaced for diagnosis.
    assert "120" in str(exc.value)


def test_read_timeout_via_builtin_timeouterror_maps_to_external_error(monkeypatch):
    # socket.timeout is an alias of TimeoutError on 3.10+, but pin the builtin too.
    def fake_urlopen(request, timeout=None):
        raise TimeoutError("the read operation timed out")

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError) as exc:
        llm.chat(_CFG, "sp", "uc")
    assert exc.value.exit_code == 4
    assert not isinstance(exc.value, TimeoutError)


def test_connection_reset_mid_read_maps_to_external_error(monkeypatch):
    # Connection reset while streaming the body is an OSError, not a URLError/
    # HTTPError. Without the broad OSError catch it would escape as exit 5.
    class _ResettingResponse(_FakeResponse):
        def read(self):
            raise ConnectionResetError("Connection reset by peer")

    def fake_urlopen(request, timeout=None):
        return _ResettingResponse(b"")

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError) as exc:
        llm.chat(_CFG, "sp", "uc")
    assert exc.value.exit_code == 4
    assert "中断" in str(exc.value)


def test_connect_timeout_url_error_maps_to_external_error(monkeypatch):
    # A connect-phase timeout reaches urllib as URLError(reason=socket.timeout).
    # It must still be ExternalError, with connect-appropriate (逾时) messaging.
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError) as exc:
        llm.chat(_CFG, "sp", "uc")
    assert exc.value.exit_code == 4
    assert "逾时" in str(exc.value)


def test_empty_content_raises_not_silent_empty(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(_ok_payload("   \n\t  "))

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError, match="空"):
        llm.chat(_CFG, "sp", "uc")


def test_missing_choices_raises_format_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps({"id": "x", "choices": []}).encode("utf-8"))

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError, match="格式异常"):
        llm.chat(_CFG, "sp", "uc")


def test_no_choices_key_raises_format_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps({"error": "bad"}).encode("utf-8"))

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError, match="格式异常"):
        llm.chat(_CFG, "sp", "uc")


def test_malformed_json_response_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(b"<html>not json</html>")

    _patch_urlopen(monkeypatch, fake_urlopen)

    # Malformed JSON must raise (JSONDecodeError), never silently return "".
    with pytest.raises(json.JSONDecodeError):
        llm.chat(_CFG, "sp", "uc")


def test_non_string_content_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        body = {"choices": [{"message": {"content": 123}}]}
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    _patch_urlopen(monkeypatch, fake_urlopen)

    with pytest.raises(ExternalError, match="空"):
        llm.chat(_CFG, "sp", "uc")


def test_missing_base_url_raises_before_transport(monkeypatch):
    # Config guard fires before any network call.
    def boom(request, timeout=None):  # pragma: no cover - must not be reached
        raise AssertionError("transport should not be called")

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(ValidationError):
        llm.chat({**_CFG, "base_url": ""}, "sp", "uc")


def test_missing_api_key_raises_before_transport(monkeypatch):
    monkeypatch.delenv("CPOST_LLM_API_KEY", raising=False)

    def boom(request, timeout=None):  # pragma: no cover - must not be reached
        raise AssertionError("transport should not be called")

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(ValidationError, match="API key"):
        llm.chat(_CFG, "sp", "uc")


# --- load_config / load_system_prompt / build_user_content (R12 edge cases) ----


def test_load_config_file_not_found(monkeypatch):
    """When the yaml file does not exist, load_config returns defaults unchanged."""
    cfg = llm.load_config("/nonexistent/path.yaml")
    assert cfg["base_url"] == ""


def test_load_config_merges_yaml(tmp_path):
    """When yaml exists and has valid keys, they are merged over defaults (llm.py:42-44)."""
    p = tmp_path / "cfg.yaml"
    p.write_text("base_url: https://my-llm.example.com/v1\nmodel: gpt-4\n", encoding="utf-8")
    cfg = llm.load_config(str(p))
    assert cfg["base_url"] == "https://my-llm.example.com/v1"
    assert cfg["model"] == "gpt-4"


def test_load_config_resolves_relative_prompt_path(tmp_path):
    """When prompt_path is relative, it is resolved next to the config file (llm.py:47-50)."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    p = cfg_dir / "llm.yaml"
    p.write_text("prompt_path: prompts/system.txt\n", encoding="utf-8")
    cfg = llm.load_config(str(p))
    expected = str(cfg_dir / "prompts" / "system.txt")
    assert cfg["prompt_path"] == expected


def test_load_config_yaml_not_a_dict(tmp_path):
    """When yaml content is not a mapping (e.g. a list), ValidationError is raised (llm.py:41)."""
    p = tmp_path / "cfg.yaml"
    p.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="mapping"):
        llm.load_config(str(p))


def test_load_system_prompt_success(tmp_path):
    """When prompt_path exists, returns file content (llm.py:59)."""
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("You are a helpful assistant.", encoding="utf-8")
    result = llm.load_system_prompt({"prompt_path": str(prompt_file)})
    assert result == "You are a helpful assistant."


def test_load_system_prompt_file_not_found():
    """When prompt_path is empty or file is missing, ValidationError is raised (llm.py:58)."""
    with pytest.raises(ValidationError, match="system prompt file not found"):
        llm.load_system_prompt({"prompt_path": ""})


def test_build_user_content_wraps_correctly():
    """build_user_content produces the expected wrapper format."""
    result = llm.build_user_content("測試標題", "測試正文")
    assert "測試標題" in result
    assert "測試正文" in result
