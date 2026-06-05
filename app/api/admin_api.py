import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

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
    from app.core.auth import verify_admin
    return await verify_admin(request=request)


@router.get("/queue", response_model=QueueStatus)
async def get_queue_status(request: Request, _=Depends(_admin_auth)):
    """Get current queue occupancy and capacity."""
    queue = get_queue()
    return QueueStatus(
        max_length=queue.max_size,
        current_waiting=queue.waiting_count,
        current_processing=queue.is_processing,
        queue_full=queue.is_full,
    )


@router.get("/keys")
async def list_api_keys(request: Request, _=Depends(_admin_auth)):
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, key, name, priority, enabled, created_at FROM api_keys ORDER BY id"
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
        )
        for r in rows
    ]


@router.post("/keys", status_code=201)
async def create_api_key(body: ApiKeyCreate, request: Request, _=Depends(_admin_auth)):
    db = await get_db()
    key = generate_api_key()
    now = utcnow()
    await db.execute(
        "INSERT INTO api_keys (key, name, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (key, body.name, body.priority, now, now),
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
    now = utcnow()

    await db.execute(
        "UPDATE api_keys SET name=?, priority=?, enabled=?, updated_at=? WHERE id=?",
        (new_name, new_priority, int(new_enabled), now, key_id),
    )
    await db.commit()

    return ApiKeyResponse(
        id=key_id,
        key=existing["key"],
        name=new_name,
        priority=new_priority,
        enabled=new_enabled,
        created_at=existing["created_at"],
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
