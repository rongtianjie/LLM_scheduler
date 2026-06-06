import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel


# ── Pydantic schemas for Admin API ──────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str
    priority: int = 100
    rate_limit: int = 0  # requests per minute, 0 = unlimited
    token_quota_daily: int = 0  # 0 = unlimited
    token_quota_monthly: int = 0  # 0 = unlimited


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    rate_limit: Optional[int] = None
    token_quota_daily: Optional[int] = None
    token_quota_monthly: Optional[int] = None


class ApiKeyResponse(BaseModel):
    id: int
    key: str
    name: str
    priority: int
    enabled: bool
    created_at: str
    rate_limit: int = 0
    token_quota_daily: int = 0
    token_quota_monthly: int = 0


class QueueStatus(BaseModel):
    max_length: int
    current_waiting: int
    current_processing: bool
    queue_full: bool


# ── Internal request context ────────────────────────────────────────

@dataclass
class RequestContext:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    priority: int = 100
    user_name: str = "anonymous"
    body: dict = field(default_factory=dict)
    endpoint: str = ""
    client_ip: str = ""
    timestamp: float = 0.0
    model: str = ""
    enqueue_time: Optional[datetime] = None
    dequeue_time: Optional[datetime] = None
    complete_time: Optional[datetime] = None
    response_status: Optional[int] = None
    error: Optional[str] = None
    streamed: bool = False
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


# ── Database helpers ────────────────────────────────────────────────

def generate_api_key() -> str:
    return f"sk-{secrets.token_hex(32)}"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
