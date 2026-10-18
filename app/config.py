"""配置加载：config.yaml（可选）+ 环境变量覆盖。

优先级：环境变量 > config.yaml > 内置默认值。
环境变量统一前缀 ``COOLAPK2API_``，例如 ``COOLAPK2API_PORT``、``COOLAPK2API_API_KEY``。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# env 名 -> (配置路径, 类型)
_ENV_MAP: dict[str, tuple[str, type]] = {
    "COOLAPK2API_HOST": ("server.host", str),
    "COOLAPK2API_PORT": ("server.port", int),
    "COOLAPK2API_API_KEY": ("server.api_key", str),
    "COOLAPK2API_UPSTREAM": ("upstream.base", str),
    "COOLAPK2API_DISCOVERY_QUERY": ("upstream.discovery_query", str),
    "COOLAPK2API_CONTEXT_TTL": ("upstream.context_ttl", int),
    "COOLAPK2API_REQUEST_TIMEOUT": ("upstream.request_timeout", int),
    "COOLAPK2API_STREAM_TIMEOUT": ("upstream.stream_timeout", int),
    "COOLAPK2API_DEFAULT_MODEL": ("models.default", str),
    "COOLAPK2API_COOKIE_FILE": ("credentials.cookie_file", str),
    "COOLAPK2API_DEVICE_FILE": ("credentials.device_file", str),
    "COOLAPK2API_BLOB_FILE": ("credentials.blob_file", str),
    "COOLAPK2API_LOG_LEVEL": ("logging.level", str),
}


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8321
    api_key: str = ""


@dataclass
class UpstreamConfig:
    base: str = "https://api.coolapk.com"
    discovery_query: str = "MIUI"
    context_ttl: int = 3600
    request_timeout: int = 15
    stream_timeout: int = 90


@dataclass
class ModelsConfig:
    available: list[str] = field(default_factory=lambda: ["deepseek-v3", "deepseek-r1"])
    default: str = "deepseek-v3"


@dataclass
class CredentialsConfig:
    cookie_file: str = "secrets/coolapk_cookie.txt"
    device_file: str = "secrets/device_code.txt"
    blob_file: str = "secrets/auth_blob.b64"


@dataclass
class LoggingConfig:
    level: str = "info"


@dataclass
class Settings:
    server: ServerConfig = field(default_factory=ServerConfig)
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # ---------- 路径解析 ----------
    def resolve(self, rel: str) -> Path:
        p = Path(rel).expanduser()
        return p if p.is_absolute() else (ROOT / p)

    @property
    def cookie_path(self) -> Path:
        return self.resolve(self.credentials.cookie_file)

    @property
    def device_path(self) -> Path:
        return self.resolve(self.credentials.device_file)

    @property
    def blob_path(self) -> Path:
        return self.resolve(self.credentials.blob_file)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["credentials"] = {k: str(self.resolve(v)) for k, v in asdict(self.credentials).items()}
        return d


def _set_path(data: dict, dotted: str, value: Any) -> None:
    node = data
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("config.yaml 存在但缺少 PyYAML：pip install PyYAML") from exc
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _apply(section: Any, data: dict) -> None:
    for key, value in data.items():
        if hasattr(section, key):
            setattr(section, key, value)


def load_settings(config_path: str | os.PathLike | None = None) -> Settings:
    """读取配置。``config_path`` 省略时用项目根目录的 config.yaml（存在则用）。"""
    data = _load_yaml(Path(config_path) if config_path else ROOT / "config.yaml")

    settings = Settings()
    for section_name in ("server", "upstream", "models", "credentials", "logging"):
        raw = data.get(section_name)
        if isinstance(raw, dict):
            _apply(getattr(settings, section_name), raw)

    for env_name, (dotted, typ) in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            value = typ(raw)
        except (TypeError, ValueError):
            continue
        target = settings
        parts = dotted.split(".")
        for p in parts[:-1]:
            target = getattr(target, p)
        setattr(target, parts[-1], value)

    return settings
