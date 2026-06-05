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
async def get_stats(request: Request, _=Depends(_admin_auth)):
    db = await get_db()

    # Total requests
    cursor = await db.execute("SELECT COUNT(*) as c FROM request_logs")
    total_requests = (await cursor.fetchone())["c"]

    # Today's requests
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor = await db.execute(
        "SELECT COUNT(*) as c FROM request_logs WHERE created_at >= ?", (today,)
    )
    today_requests = (await cursor.fetchone())["c"]

    # Average wait time (last 100)
    cursor = await db.execute(
        "SELECT AVG(wait_time_ms) as avg_wait FROM (SELECT wait_time_ms FROM request_logs WHERE wait_time_ms IS NOT NULL ORDER BY id DESC LIMIT 100)"
    )
    row = await cursor.fetchone()
    avg_wait_ms = round(row["avg_wait"]) if row["avg_wait"] else 0

    # Average processing time (last 100)
    cursor = await db.execute(
        "SELECT AVG(processing_time_ms) as avg_proc FROM (SELECT processing_time_ms FROM request_logs WHERE processing_time_ms IS NOT NULL ORDER BY id DESC LIMIT 100)"
    )
    row = await cursor.fetchone()
    avg_proc_ms = round(row["avg_proc"]) if row["avg_proc"] else 0

    # Error count (last 1000)
    cursor = await db.execute(
        "SELECT COUNT(*) as c FROM request_logs WHERE error IS NOT NULL AND id > (SELECT MAX(id) - 1000 FROM request_logs)"
    )
    errors = (await cursor.fetchone())["c"]

    return {
        "total_requests": total_requests,
        "today_requests": today_requests,
        "avg_wait_time_ms": avg_wait_ms,
        "avg_processing_time_ms": avg_proc_ms,
        "errors_last_1000": errors,
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
                "error": r["error"],
                "client_ip": r["client_ip"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }
