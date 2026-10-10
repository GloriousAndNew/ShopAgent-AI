"""模型配置与本地秘密分开；不提供读取密钥的 HTTP 接口。"""
import os
import tomllib
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlsplit
from shopagent.settings import ROOT
from shopagent.contracts.models import BusinessError


@dataclass(frozen=True)
class AgentConfig:
    mode: str = "mock"
    protocol: str = "chat_completions"
    base_url: str = ""
    model: str = ""
    thinking_mode: str = "default"
    max_steps: int = 6
    max_tool_calls: int = 8
    max_output_tokens: int = 1200
    timeout_seconds: int = 60


def load_config():
    try:
        from shopagent.core.environment import production
        path = Path(os.getenv("SHOPAGENT_AGENT_CONFIG", str(ROOT / ('agent.production.toml' if production() else 'agent.toml'))))
        values = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        config = AgentConfig(**values.get("agent", {}))
        if config.mode not in ("mock", "llm") or config.protocol not in ("chat_completions", "responses"):
            raise ValueError()
        if config.thinking_mode not in ("default", "enabled", "disabled") or (config.protocol != "chat_completions" and config.thinking_mode != "default"):
            raise ValueError()
        for name, low, high in (("max_steps", 1, 10), ("max_tool_calls", 1, 20), ("max_output_tokens", 100, 4000), ("timeout_seconds", 5, 120)):
            value = getattr(config, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError()
        if not all(isinstance(getattr(config, name), str) for name in ("base_url", "model")):
            raise ValueError()
        return config
    except (TypeError, ValueError, OSError, tomllib.TOMLDecodeError):
        raise BusinessError("AGENT_CONFIG_INVALID", "agent.toml 格式或参数不正确，请查阅 Agent 接入说明。") from None


def read_api_key():
    value = os.getenv("SHOPAGENT_MODEL_API_KEY", "").strip()
    if value:
        return value
    from shopagent.core.environment import production
    path = ROOT / ('.env.production' if production() else '.env')
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key == "SHOPAGENT_MODEL_API_KEY":
                return value.strip().strip('"\'')
    return ""


def validate_connection(config):
    try:
        url = urlsplit(config.base_url)
        url.port  # 验证端口和 IPv6 格式。
    except ValueError:
        raise BusinessError("AGENT_CONFIG_INVALID", "模型基础地址格式不正确。") from None
    local = url.hostname in ("127.0.0.1", "localhost", "::1")
    if not url.hostname or url.username or url.password or url.query or url.fragment or not (url.scheme == "https" or (local and url.scheme == "http")):
        raise BusinessError("AGENT_CONFIG_INVALID", "模型地址须为 HTTPS 的 API 基础地址；本机调试可以使用 HTTP。")
    if not config.model.strip():
        raise BusinessError("AGENT_NOT_CONFIGURED", "请先在 agent.toml 填写支持工具调用的模型名称。")
    key = read_api_key()
    if not key or key in ("在这里填写密钥", "your-api-key"):
        raise BusinessError("AGENT_NOT_CONFIGURED", "尚未配置模型密钥。请在本地 .env 填写，勿发送到聊天中。")
    return key
