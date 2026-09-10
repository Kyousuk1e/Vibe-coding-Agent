"""Small dotenv loader and configuration; environment variables always win."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re

from .errors import AgentError


@dataclass
class Config:
    provider: str = "qwen"
    api_key: str = field(default="", repr=False)
    base_url: str | None = None
    model: str | None = None
    port: int = 8787
    data_dir: str = field(default_factory=lambda: str(Path("data").resolve()))
    max_steps: int = 8
    max_context_chars: int = 24000
    timeout_ms: int = 30000
    max_retries: int = 2
    max_output_tokens: int = 1200

    def __post_init__(self):
        if self.base_url is None:
            self.base_url = ("https://dashscope.aliyuncs.com/compatible-mode/v1"
                             if self.provider == "qwen" else "https://api.openai.com/v1")
        if self.model is None:
            self.model = "qwen-plus" if self.provider == "qwen" else "gpt-4.1-mini"
        self.data_dir = str(Path(self.data_dir).resolve())


def _dotenv(path):
    """Parse quoted/unquoted values, comments and optional export, without expansion."""
    if path is None:
        return {}
    try:
        with Path(path).open("r", encoding="utf-8-sig") as stream:
            source = stream.read(65537)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise AgentError("CONFIG", "无法读取环境配置文件。") from None
    if len(source) > 65536:
        raise AgentError("CONFIG", "环境配置文件超过大小限制。")
    values = {}
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        line_number = index + 1
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise AgentError("CONFIG", f"环境配置文件第 {line_number} 行格式无效。")
        name, raw = match.groups()
        if not raw.startswith(("'", '"')):
            values[name] = raw.split("#", 1)[0].strip()
            continue
        quote, content, position = raw[0], [], 1
        while True:
            if position >= len(raw):
                if index >= len(lines):
                    raise AgentError("CONFIG", f"环境配置文件第 {line_number} 行引号未闭合。")
                raw += "\n" + lines[index]
                index += 1
                continue
            char = raw[position]
            if char == quote:
                remaining = raw[position + 1:].strip()
                if remaining and not remaining.startswith("#"):
                    raise AgentError("CONFIG", f"环境配置文件第 {line_number} 行格式无效。")
                break
            if quote == '"' and char == "\\" and position + 1 < len(raw):
                following = raw[position + 1]
                escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
                if following in escapes:
                    content.append(escapes[following])
                    position += 2
                    continue
            content.append(char)
            position += 1
        values[name] = "".join(content)
    return values


def _integer(env, name, fallback, minimum, maximum):
    try:
        value = Decimal(str(env.get(name, fallback)).strip() or "0")
        if not value.is_finite() or value != value.to_integral_value() or not minimum <= value <= maximum:
            raise ValueError
        return int(value)
    except (InvalidOperation, ValueError, TypeError):
        raise AgentError("CONFIG", f"{name} 必须在 {minimum}–{maximum} 之间。") from None


def read_config(env=None, env_file=".env"):
    # An explicit mapping is an isolated environment for embedding/tests. Never mutate os.environ.
    values = _dotenv(env_file)
    values.update(dict(os.environ if env is None else env))
    provider = values.get("LLM_PROVIDER") or "qwen"
    if provider not in ("qwen", "openai"):
        raise AgentError("CONFIG", "LLM_PROVIDER 应为 qwen 或 openai。")
    key_name = "DASHSCOPE_API_KEY" if provider == "qwen" else "OPENAI_API_KEY"
    api_key = values.get(key_name)
    if not isinstance(api_key, str) or not api_key.strip() or "\n" in api_key or "\r" in api_key:
        raise AgentError("CONFIG", f"请配置有效的 {key_name}。不会回退到 mock LLM。")
    return Config(
        provider=provider,
        api_key=api_key.strip(),
        base_url=values.get("LLM_BASE_URL") or None,
        model=values.get("LLM_MODEL") or None,
        port=_integer(values, "PORT", 8787, 1, 65535),
        data_dir=values.get("DATA_DIR") or "./data",
        max_steps=_integer(values, "MAX_STEPS", 8, 1, 30),
        max_context_chars=_integer(values, "MAX_CONTEXT_CHARS", 24000, 10000, 200000),
        timeout_ms=_integer(values, "LLM_TIMEOUT_MS", 30000, 100, 120000),
        max_retries=_integer(values, "LLM_MAX_RETRIES", 2, 0, 3),
        max_output_tokens=_integer(values, "MAX_OUTPUT_TOKENS", 1200, 64, 8192),
    )


def read_client_url(env=None, env_file=".env"):
    """Read only the client connection setting; no API key is required."""
    values = _dotenv(env_file)
    values.update(dict(os.environ if env is None else env))
    port = _integer(values, "PORT", 8787, 1, 65535)
    return f"http://127.0.0.1:{port}"
