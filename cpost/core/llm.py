"""OpenAI-compatible chat client + config for AI article generation.

Runtime-dependency-free on purpose: the project keeps a tight dependency set, so
this uses stdlib ``urllib`` rather than httpx/openai. The endpoint sits behind
Cloudflare, which 403s the default Python User-Agent (Cloudflare error 1010); a
browser User-Agent header passes it. The API key is never stored on disk -- it is
read from the env var named by ``api_key_env``.
"""

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

from cpost.core.errors import ExternalError, ValidationError

_DEFAULTS: dict = {
    "base_url": "",
    "model": "",
    "api_key_env": "CPOST_LLM_API_KEY",
    "prompt_path": "",
    "temperature": 0.7,
    "max_tokens": 4096,
    "timeout_sec": 120,
    "user_agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}


def load_config(path: str) -> dict:
    """Load the LLM yaml merged over defaults; resolve prompt_path next to it."""
    import yaml  # PyYAML is a core dependency

    cfg = dict(_DEFAULTS)
    p = Path(path)
    if p.exists():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValidationError("llm config must be a mapping")
        for key, value in loaded.items():
            if key in cfg and value is not None:
                cfg[key] = value
    prompt_path = str(cfg.get("prompt_path") or "")
    if prompt_path:
        pp = Path(prompt_path)
        if not pp.is_absolute():
            pp = p.parent / pp
        cfg["prompt_path"] = str(pp)
    return cfg


def load_system_prompt(cfg: dict) -> str:
    """Read the system-prompt file referenced by the config."""
    prompt_path = str(cfg.get("prompt_path") or "")
    if not prompt_path or not Path(prompt_path).exists():
        raise ValidationError(f"system prompt file not found: {prompt_path!r}")
    return Path(prompt_path).read_text(encoding="utf-8")


def build_user_content(title: str, material: str) -> str:
    """Wrap the crawled title + body into the user turn for the model."""
    return (
        "以下是从网页爬取的原始素材，请严格按照系统规范，将其改写成一篇成品文章。"
        "只输出文章本身（标题与各小节），不要输出任何解释、前言或结语。\n\n"
        f"【原标题】\n{title}\n\n【原始素材 / 正文】\n{material}"
    )


def chat(cfg: dict, system_prompt: str, user_content: str) -> str:
    """Call the OpenAI-compatible /chat/completions endpoint; return the reply text."""
    base = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    if not base or not model:
        raise ValidationError("llm config 需要 base_url 与 model")
    env_name = str(cfg.get("api_key_env") or "")
    key = os.environ.get(env_name, "")
    if not key:
        raise ValidationError(f"未设定 API key：请先 export {env_name}=<你的 key>")

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": float(cfg.get("temperature", 0.7)),
        "max_tokens": int(cfg.get("max_tokens", 4096)),
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/chat/completions", data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": str(cfg.get("user_agent") or ""),
        })
    try:
        with urllib.request.urlopen(request, timeout=int(cfg.get("timeout_sec", 120))) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise ExternalError(f"LLM 端点错误 HTTP {exc.code}：{detail}")
    except urllib.error.URLError as exc:
        # A connect-phase timeout surfaces here with reason=socket.timeout.
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ExternalError(f"LLM 端点连线逾时（{cfg.get('timeout_sec', 120)}s）：{exc.reason}")
        raise ExternalError(f"LLM 端点连线失败：{exc.reason}")
    except (TimeoutError, socket.timeout) as exc:
        # Read-phase stall: server accepted the connection but never finished the
        # body before timeout_sec. Raised directly by resp.read(), not via URLError.
        raise ExternalError(f"LLM 端点读取逾时（{cfg.get('timeout_sec', 120)}s）：{exc}")
    except OSError as exc:
        # Any other socket-level failure mid-request (e.g. connection reset during
        # read) is an external fault, not an internal bug -> exit 4, not exit 5.
        raise ExternalError(f"LLM 端点连线中断：{exc}")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ExternalError(f"LLM 回应格式异常：{str(data)[:200]}")
    if not isinstance(content, str) or not content.strip():
        raise ExternalError("LLM 回应为空")
    return content.strip()
