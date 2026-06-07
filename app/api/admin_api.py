import secrets
from datetime import datetime, timezone
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import BackendConfig, get_config
from app.core.queue import get_queue
from app.database import get_db
from app.models import (
    ApiKeyCreate,
    ApiKeyResponse,
    ApiKeyUpdate,
    QueueStatus,
    generate_api_key,
    utcnow,
)

router = APIRouter()


async def _admin_auth(request: Request):
    from app.core.auth import require_admin_api
    return await require_admin_api(request=request)


@router.get("/queue", response_model=QueueStatus)
async def get_queue_status(request: Request, _=Depends(_admin_auth)):
    """Get current queue occupancy and capacity."""
    queue = get_queue()
    return QueueStatus(
        max_length=queue.max_size,
        current_waiting=queue.waiting_count,
        current_processing=queue.is_processing,
        processing_count=queue.processing_count,
        max_concurrency=queue.max_concurrency,
        queue_full=queue.is_full,
    )


@router.get("/keys")
async def list_api_keys(request: Request, _=Depends(_admin_auth)):
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, key, name, priority, enabled, created_at, "
        "COALESCE(rate_limit, 0) as rate_limit, "
        "COALESCE(token_quota_daily, 0) as token_quota_daily, "
        "COALESCE(token_quota_monthly, 0) as token_quota_monthly "
        "FROM api_keys ORDER BY id"
    )
    rows = await cursor.fetchall()
    return [
        ApiKeyResponse(
            id=r["id"],
            key=r["key"],
            name=r["name"],
            priority=r["priority"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
            rate_limit=r["rate_limit"],
            token_quota_daily=r["token_quota_daily"],
            token_quota_monthly=r["token_quota_monthly"],
        )
        for r in rows
    ]


@router.post("/keys", status_code=201)
async def create_api_key(body: ApiKeyCreate, request: Request, _=Depends(_admin_auth)):
    db = await get_db()
    key = generate_api_key()
    now = utcnow()
    await db.execute(
        "INSERT INTO api_keys (key, name, priority, rate_limit, token_quota_daily, "
        "token_quota_monthly, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (key, body.name, body.priority, body.rate_limit, body.token_quota_daily,
         body.token_quota_monthly, now, now),
    )
    await db.commit()
    cursor = await db.execute("SELECT last_insert_rowid()")
    row = await cursor.fetchone()
    return ApiKeyResponse(
        id=row[0],
        key=key,
        name=body.name,
        priority=body.priority,
        enabled=True,
        created_at=utcnow(),
        rate_limit=body.rate_limit,
        token_quota_daily=body.token_quota_daily,
        token_quota_monthly=body.token_quota_monthly,
    )


@router.put("/keys/{key_id}")
async def update_api_key(key_id: int, body: ApiKeyUpdate, request: Request,
                         _=Depends(_admin_auth)):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    existing = await cursor.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="API key not found")

    new_name = body.name if body.name is not None else existing["name"]
    new_priority = body.priority if body.priority is not None else existing["priority"]
    new_enabled = body.enabled if body.enabled is not None else bool(existing["enabled"])
    new_rate_limit = body.rate_limit if body.rate_limit is not None else (existing["rate_limit"] if existing["rate_limit"] is not None else 0)
    new_quota_daily = body.token_quota_daily if body.token_quota_daily is not None else (existing["token_quota_daily"] if existing["token_quota_daily"] is not None else 0)
    new_quota_monthly = body.token_quota_monthly if body.token_quota_monthly is not None else (existing["token_quota_monthly"] if existing["token_quota_monthly"] is not None else 0)
    now = utcnow()

    await db.execute(
        "UPDATE api_keys SET name=?, priority=?, enabled=?, rate_limit=?, "
        "token_quota_daily=?, token_quota_monthly=?, updated_at=? WHERE id=?",
        (new_name, new_priority, int(new_enabled), new_rate_limit,
         new_quota_daily, new_quota_monthly, now, key_id),
    )
    await db.commit()

    return ApiKeyResponse(
        id=key_id,
        key=existing["key"],
        name=new_name,
        priority=new_priority,
        enabled=new_enabled,
        created_at=existing["created_at"],
        rate_limit=new_rate_limit,
        token_quota_daily=new_quota_daily,
        token_quota_monthly=new_quota_monthly,
    )


@router.delete("/keys/{key_id}")
async def delete_api_key(key_id: int, request: Request, _=Depends(_admin_auth)):
    db = await get_db()
    cursor = await db.execute("SELECT id FROM api_keys WHERE id = ?", (key_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="API key not found")
    await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    await db.commit()
    return {"ok": True}


@router.get("/stats")
async def get_stats(request: Request, _=Depends(_admin_auth),
                    period: str = "24h", key_id: int = None):
    db = await get_db()

    # Compute time threshold
    period_map = {
        "1h": 3600, "6h": 21600, "24h": 86400,
        "7d": 604800, "30d": 2592000, "all": 0,
    }
    cutoff_seconds = period_map.get(period, 86400)
    if cutoff_seconds and period != "all":
        cutoff = datetime.now(timezone.utc).timestamp() - cutoff_seconds
        from_clause = "WHERE rl.created_at >= datetime(?, 'unixepoch')"
        period_params = [cutoff]
    else:
        from_clause = ""
        period_params = []

    key_filter = ""
    key_params = []
    if key_id is not None:
        key_filter = "AND ak.id = ?"
        key_params = [key_id]

    # Total requests and tokens
    cursor = await db.execute(
        f"SELECT COUNT(*) as c, COALESCE(SUM(rl.prompt_tokens),0) as pt, "
        f"COALESCE(SUM(rl.completion_tokens),0) as ct "
        f"FROM request_logs rl {from_clause}",
        period_params,
    )
    row = await cursor.fetchone()
    total_requests = row["c"]
    total_prompt_tokens = row["pt"] or 0
    total_completion_tokens = row["ct"] or 0

    # Per-key breakdown
    query = f"""
        SELECT COALESCE(ak.name, rl.user_name, 'anonymous') as name,
               ak.id as key_id,
               COUNT(*) as requests,
               SUM(rl.prompt_tokens) as prompt_tokens,
               SUM(rl.completion_tokens) as completion_tokens
        FROM request_logs rl
        LEFT JOIN api_keys ak ON rl.user_name = ak.name
        {from_clause}
        GROUP BY name
        ORDER BY requests DESC
    """
    cursor = await db.execute(query, period_params)
    rows = await cursor.fetchall()
    per_key = [
        {
            "name": r["name"],
            "key_id": r["key_id"],
            "requests": r["requests"],
            "prompt_tokens": r["prompt_tokens"] or 0,
            "completion_tokens": r["completion_tokens"] or 0,
        }
        for r in rows
    ]

    # Errors in period
    if from_clause:
        cursor = await db.execute(
            f"SELECT COUNT(*) as c FROM request_logs rl "
            f"WHERE rl.error IS NOT NULL AND {from_clause[6:]}",
            period_params,
        )
        errors = (await cursor.fetchone())["c"]
    else:
        cursor = await db.execute(
            "SELECT COUNT(*) as c FROM request_logs WHERE error IS NOT NULL"
        )
        errors = (await cursor.fetchone())["c"]

    return {
        "period": period,
        "total_requests": total_requests,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "errors": errors,
        "per_key": per_key,
    }


# ── Timeseries stats ────────────────────────────────────────────────

# Bucket interval per period
_INTERVAL_MAP = {
    "1h":  ("%Y-%m-%dT%H:%M:00Z", 300),     # 5 minutes
    "6h":  ("%Y-%m-%dT%H:00:00Z", 1800),    # 30 minutes
    "24h": ("%Y-%m-%dT%H:00:00Z", 3600),    # 1 hour
    "7d":  ("%Y-%m-%dT%H:00:00Z", 21600),   # 6 hours
    "30d": ("%Y-%m-%dT00:00:00Z", 86400),   # 1 day
    "all": ("%Y-%m-%dT00:00:00Z", 86400),   # 1 day
}


@router.get("/stats/timeseries")
async def get_stats_timeseries(request: Request, _=Depends(_admin_auth),
                                period: str = "24h"):
    db = await get_db()

    period_map = {
        "1h": 3600, "6h": 21600, "24h": 86400,
        "7d": 604800, "30d": 2592000, "all": 0,
    }
    cutoff_seconds = period_map.get(period, 86400)
    if cutoff_seconds and period != "all":
        cutoff = datetime.now(timezone.utc).timestamp() - cutoff_seconds
        where_clause = "WHERE created_at >= datetime(?, 'unixepoch')"
        params = [cutoff]
    else:
        where_clause = ""
        params = []

    cursor = await db.execute(
        f"SELECT created_at, prompt_tokens, completion_tokens, error "
        f"FROM request_logs {where_clause} ORDER BY created_at",
        params,
    )
    rows = await cursor.fetchall()

    _, interval_seconds = _INTERVAL_MAP.get(period, ("%Y-%m-%dT%H:00:00Z", 3600))

    # Bucket rows in Python
    from datetime import datetime as dt
    buckets_dict: dict[str, dict] = {}

    for row in rows:
        ts_str = row["created_at"]
        try:
            ts = dt.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        ts_epoch = ts.timestamp()
        bucket_epoch = (ts_epoch // interval_seconds) * interval_seconds
        bucket_ts = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if bucket_ts not in buckets_dict:
            buckets_dict[bucket_ts] = {
                "timestamp": bucket_ts,
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "errors": 0,
            }
        b = buckets_dict[bucket_ts]
        b["requests"] += 1
        b["prompt_tokens"] += row["prompt_tokens"] or 0
        b["completion_tokens"] += row["completion_tokens"] or 0
        if row["error"]:
            b["errors"] += 1

    buckets = sorted(buckets_dict.values(), key=lambda x: x["timestamp"])
    interval_labels = {"1h": "5m", "6h": "30m", "24h": "1h", "7d": "6h", "30d": "1d", "all": "1d"}

    return {
        "period": period,
        "interval": interval_labels.get(period, "1h"),
        "buckets": buckets,
    }


@router.get("/logs")
async def get_logs(request: Request, _=Depends(_admin_auth),
                   page: int = 1, per_page: int = 50,
                   endpoint: str = None, user: str = None):
    db = await get_db()
    conditions = []
    params = []
    if endpoint:
        conditions.append("endpoint = ?")
        params.append(endpoint)
    if user:
        conditions.append("user_name = ?")
        params.append(user)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    # Count
    cursor = await db.execute(f"SELECT COUNT(*) as c FROM request_logs {where}", params)
    total = (await cursor.fetchone())["c"]

    # Fetch page
    offset = (page - 1) * per_page
    cursor = await db.execute(
        f"SELECT * FROM request_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    rows = await cursor.fetchall()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": r["id"],
                "request_id": r["request_id"],
                "user_name": r["user_name"],
                "endpoint": r["endpoint"],
                "model": r["model"],
                "priority": r["priority"],
                "wait_time_ms": r["wait_time_ms"],
                "processing_time_ms": r["processing_time_ms"],
                "status_code": r["status_code"],
                "streamed": bool(r["streamed"]),
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "error": r["error"],
                "client_ip": r["client_ip"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


# ── Management config ──────────────────────────────────────────────

class MutableQueue(BaseModel):
    max_length: Optional[int] = None
    concurrency: Optional[int] = None
    timeout: Optional[int] = None


class MutablePriority(BaseModel):
    strategy: Optional[str] = None
    default_priority: Optional[int] = None


class MutableDebug(BaseModel):
    enabled: Optional[bool] = None
    dir: Optional[str] = None


class MutableMetrics(BaseModel):
    enabled: Optional[bool] = None


class MutableProxy(BaseModel):
    enabled: Optional[bool] = None
    protocol: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class MutableConfig(BaseModel):
    queue: Optional[MutableQueue] = None
    priority: Optional[MutablePriority] = None
    backends: Optional[list[BackendConfig]] = None
    debug: Optional[MutableDebug] = None
    metrics: Optional[MutableMetrics] = None
    proxy: Optional[MutableProxy] = None


class MutableLogRetention(BaseModel):
    retention_days: Optional[int] = None
    max_records: Optional[int] = None


class MutableCors(BaseModel):
    origins: Optional[list[str]] = None


def _recreate_adapter(adapter_name: str, backend_config: BackendConfig):
    """No-op: adapters are created on-the-fly per request."""
    pass


@router.get("/config")
async def get_config_admin(request: Request, _=Depends(_admin_auth)):
    """Return current runtime configuration sections."""
    cfg = get_config()
    return {
        "queue": {"max_length": cfg.queue.max_length, "concurrency": cfg.queue.concurrency, "timeout": cfg.queue.timeout},
        "priority": {
            "strategy": cfg.priority.strategy,
            "default_priority": cfg.priority.default_priority,
        },
        "backends": [b.model_dump() for b in cfg.backends],
        "debug": {"enabled": cfg.debug.enabled, "dir": cfg.debug.dir},
        "metrics": {"enabled": cfg.metrics.enabled},
        "proxy": {
            "enabled": cfg.proxy.enabled,
            "protocol": cfg.proxy.protocol,
            "host": cfg.proxy.host,
            "port": cfg.proxy.port,
            "username": cfg.proxy.username,
            "password": cfg.proxy.password,
        },
        "log_retention": {
            "retention_days": cfg.log_retention.retention_days,
            "max_records": cfg.log_retention.max_records,
        },
        "cors": {
            "origins": cfg.cors.origins,
        },
    }


@router.put("/config")
async def update_config_admin(body: MutableConfig, request: Request,
                               _=Depends(_admin_auth)):
    """Apply config changes in-memory immediately."""
    cfg = get_config()
    changed = []

    # Queue
    if body.queue:
        if body.queue.max_length is not None:
            cfg.queue.max_length = body.queue.max_length
            q = get_queue()
            q._max_size = body.queue.max_length
            changed.append("queue.max_length")
        if body.queue.concurrency is not None:
            cfg.queue.concurrency = body.queue.concurrency
            q = get_queue()
            q._max_concurrency = body.queue.concurrency
            changed.append("queue.concurrency")
        if body.queue.timeout is not None:
            cfg.queue.timeout = body.queue.timeout
            changed.append("queue.timeout")

    # Priority
    if body.priority:
        if body.priority.strategy is not None:
            cfg.priority.strategy = body.priority.strategy
            changed.append("priority.strategy")
        if body.priority.default_priority is not None:
            cfg.priority.default_priority = body.priority.default_priority
            changed.append("priority.default_priority")
        # Recreate strategy if strategy name changed
        if body.priority.strategy is not None:
            from app.strategies.factory import create_strategy
            import app.api.proxy as proxy_module
            proxy_module._strategy = create_strategy(cfg.priority.strategy)

    # Backends — full replacement
    if body.backends is not None:
        cfg.backends = body.backends
        import app.api.proxy as proxy_module
        proxy_module._backend_indices.clear()
        changed.append("backends")

    # Debug
    if body.debug:
        if body.debug.enabled is not None:
            cfg.debug.enabled = body.debug.enabled
            changed.append("debug.enabled")
        if body.debug.dir is not None:
            cfg.debug.dir = body.debug.dir
            changed.append("debug.dir")

    # Metrics
    if body.metrics:
        if body.metrics.enabled is not None:
            cfg.metrics.enabled = body.metrics.enabled
            changed.append("metrics.enabled")

    # Proxy
    if body.proxy:
        if body.proxy.enabled is not None:
            cfg.proxy.enabled = body.proxy.enabled
            changed.append("proxy.enabled")
        if body.proxy.protocol is not None:
            if body.proxy.protocol not in ("http", "https", "socks5"):
                raise HTTPException(status_code=422, detail="proxy.protocol must be http, https, or socks5")
            cfg.proxy.protocol = body.proxy.protocol
            changed.append("proxy.protocol")
        if body.proxy.host is not None:
            cfg.proxy.host = body.proxy.host
            changed.append("proxy.host")
        if body.proxy.port is not None:
            cfg.proxy.port = body.proxy.port
            changed.append("proxy.port")
        if body.proxy.username is not None:
            cfg.proxy.username = body.proxy.username
            changed.append("proxy.username")
        if body.proxy.password is not None:
            cfg.proxy.password = body.proxy.password
            changed.append("proxy.password")

    import structlog
    logger = structlog.get_logger()
    logger.info("config.updated", changes=changed)

    return {"ok": True, "changes": changed}

