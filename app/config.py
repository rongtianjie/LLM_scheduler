import os
from pathlib import Path
from typing import Dict, Optional

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
    strategy: str = "api_key"  # "api_key" | "ip_based"
    default_priority: int = 100
    ip_mapping: Dict[str, int] = {}


class BackendConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    timeout: int = 300  # seconds


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"  # "json" | "text"


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
    metrics: MetricsConfig = MetricsConfig()


_config: Optional[AppConfig] = None


def load_config(path: Optional[str] = None) -> AppConfig:
    if path is None:
        path = os.environ.get("LLM_GATEWAY_CONFIG", "config.yaml")
    config_path = Path(path)
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            data = {}
        return AppConfig(**data)
    return AppConfig()


def init_config(path: Optional[str] = None) -> AppConfig:
    global _config
    _config = load_config(path)
    return _config


def get_config() -> AppConfig:
    assert _config is not None, "Config not initialized. Call init_config() first."
    return _config
