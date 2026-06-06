import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8001


class AuthConfig(BaseModel):
    enabled: bool = True


class AdminConfig(BaseModel):
    enabled: bool = True
    username: str = "admin"
    password: str = "admin123"


class DatabaseConfig(BaseModel):
    path: str = "data/gateway.db"


class QueueConfig(BaseModel):
    max_length: int = 5
    concurrency: int = 1


class PriorityConfig(BaseModel):
    strategy: str = "api_key"  # reserved for future strategy selection
    default_priority: int = 100


class BackendConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    timeout: int = 300  # seconds


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"  # "json" | "text"


class DebugConfig(BaseModel):
    enabled: bool = False
    dir: str = "data/debug"


class MetricsConfig(BaseModel):
    enabled: bool = True


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    admin: AdminConfig = AdminConfig()
    database: DatabaseConfig = DatabaseConfig()
    queue: QueueConfig = QueueConfig()
    priority: PriorityConfig = PriorityConfig()
    backend: BackendConfig = BackendConfig()
    logging: LoggingConfig = LoggingConfig()
    debug: DebugConfig = DebugConfig()
    metrics: MetricsConfig = MetricsConfig()


_config: Optional[AppConfig] = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. override values take precedence."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: Optional[str] = None) -> AppConfig:
    if path is None:
        path = os.environ.get("LLM_GATEWAY_CONFIG", "config.yaml")
    config_path = Path(path)

    # Load primary config
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
        loaded_files = [str(config_path)]
    else:
        data = {}
        loaded_files = []

    # Auto-merge config.local.yaml if it exists alongside the primary config
    local_path = config_path.with_name("config.local.yaml")
    if local_path.exists() and str(local_path) != str(config_path):
        with open(local_path, "r") as f:
            local_data = yaml.safe_load(f) or {}
        data = _deep_merge(data, local_data)
        loaded_files.append(str(local_path))

    result = AppConfig(**data)
    result._loaded_files = loaded_files  # type: ignore
    return result


def init_config(path: Optional[str] = None) -> AppConfig:
    global _config
    _config = load_config(path)
    return _config


def get_config() -> AppConfig:
    assert _config is not None, "Config not initialized. Call init_config() first."
    return _config
