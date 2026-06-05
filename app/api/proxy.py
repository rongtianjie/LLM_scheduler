import asyncio
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.adapters.anthropic import AnthropicAdapter
from app.adapters.openai import OpenAIAdapter
from app.config import get_config
from app.core.auth import authenticate_request
from app.core.metrics import (
    metrics_enabled,
    queue_length,
    request_duration_seconds,
    requests_processing,
    requests_total,
    wait_time_seconds,
)
from app.core.queue import get_queue
from app.database import get_db
from app.models import RequestContext
from app.strategies.factory import create_strategy

router = APIRouter()

# Lazy-init adapters + strategy
_openai_adapter: Optional[OpenAIAdapter] = None
_anthropic_adapter: Optional[AnthropicAdapter] = None
_strategy = None


def _ensure_adapters():
    global _openai_adapter, _anthropic_adapter, _strategy
    if _openai_adapter is None:
        config = get_config()
        _openai_adapter = OpenAIAdapter(config.backend)
        _anthropic_adapter = AnthropicAdapter(config.backend)
        _strategy = create_strategy(config.priority.strategy)


async def _stream_wrapper(
    generator: AsyncGenerator[bytes, None],
    request_id: str,
    context: RequestContext,
) -> AsyncGenerator[bytes, None]:
    """Wrap a streaming generator to ensure signal_done is always called."""
    try:
        async for chunk in generator:
            yield chunk
    finally:
        queue = get_queue()
        await queue.signal_done(request_id)
        _record_completion(context)


def _record_completion(context: RequestContext):
    """Record metrics and log for a completed request."""
    now = datetime.now(timezone.utc)
    context.complete_time = now

    wait_ms = 0
    proc_ms = 0
    if context.enqueue_time and context.dequeue_time:
        wait_ms = int((context.dequeue_time - context.enqueue_time).total_seconds() * 1000)
    if context.dequeue_time and context.complete_time:
        proc_ms = int((context.complete_time - context.dequeue_time).total_seconds() * 1000)

    # Prometheus
    if metrics_enabled():
        status_label = str(context.response_status) if context.response_status else "error"
        requests_total.labels(
            endpoint=context.endpoint,
            status_code=status_label,
            user=context.user_name,
        ).inc()
        total_sec = (wait_ms + proc_ms) / 1000.0
        request_duration_seconds.labels(endpoint=context.endpoint).observe(total_sec)
        wait_time_seconds.labels(endpoint=context.endpoint).observe(wait_ms / 1000.0)

    # Structured log
    import structlog
    logger = structlog.get_logger()
    logger.info(
        "request.completed",
        request_id=context.request_id,
        user=context.user_name,
        endpoint=context.endpoint,
        model=context.model,
        priority=context.priority,
        wait_time_ms=wait_ms,
        processing_time_ms=proc_ms,
        status_code=context.response_status,
        streamed=context.streamed,
        prompt_tokens=context.prompt_tokens,
        completion_tokens=context.completion_tokens,
        error=context.error,
        client_ip=context.client_ip,
    )

    # Save to SQLite (fire-and-forget)
    asyncio.ensure_future(_save_log(context, wait_ms, proc_ms))


async def _save_log(context: RequestContext, wait_ms: int, proc_ms: int):
    try:
        db = await get_db()
        await db.execute(
            """INSERT OR IGNORE INTO request_logs
               (request_id, user_name, endpoint, model, priority,
                enqueue_time, dequeue_time, complete_time,
                wait_time_ms, processing_time_ms, status_code,
                streamed, prompt_tokens, completion_tokens, error, client_ip)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                context.request_id,
                context.user_name,
                context.endpoint,
                context.model,
                context.priority,
                context.enqueue_time.isoformat() if context.enqueue_time else None,
                context.dequeue_time.isoformat() if context.dequeue_time else None,
                context.complete_time.isoformat() if context.complete_time else None,
                wait_ms,
                proc_ms,
                context.response_status,
                1 if context.streamed else 0,
                context.prompt_tokens,
                context.completion_tokens,
                context.error,
                context.client_ip,
            ),
        )
        await db.commit()
    except Exception:
        pass  # Don't let logging failures affect the request


async def _process_request(request: Request, endpoint: str) -> Response:
    """Shared request processing for both OpenAI and Anthropic endpoints."""
    _ensure_adapters()
    config = get_config()
    queue = get_queue()

    # Authenticate
    user_name = await authenticate_request(request)

    # Parse body
    body = await request.json()
    model = body.get("model", "")
    is_stream = body.get("stream", False)

    # Compute priority
    priority = await _strategy.get_priority(request, user_name)

    # Build context
    context = RequestContext(
        priority=priority,
        user_name=user_name,
        body=body,
        endpoint=endpoint,
        client_ip=request.client.host if request.client else "unknown",
        timestamp=time.time(),
        model=model,
        streamed=is_stream,
    )

    # Enqueue
    enqueued = await queue.enqueue(context)
    if not enqueued:
        import structlog
        logger = structlog.get_logger()
        logger.warning("queue.full", request_id=context.request_id, user=user_name, endpoint=endpoint)
        return JSONResponse(status_code=429, content={"error": "Queue is full"})

    context.enqueue_time = datetime.now(timezone.utc)

    # Update queue metrics
    if metrics_enabled():
        queue_length.set(queue.waiting_count)
        requests_processing.set(1 if queue.is_processing else 0)

    # Wait for turn
    await queue.wait_for_turn(context.request_id)
    context.dequeue_time = datetime.now(timezone.utc)

    if metrics_enabled():
        queue_length.set(queue.waiting_count)

    # Determine adapter
    adapter = _openai_adapter if endpoint == "/v1/chat/completions" else _anthropic_adapter

    # Stream or non-stream
    if is_stream:
        generator = _stream_wrapper(adapter.stream(context), context.request_id, context)
        return StreamingResponse(generator, media_type="text/event-stream")
    else:
        result = await adapter.call(context)
        await queue.signal_done(context.request_id)
        _record_completion(context)
        if isinstance(result, dict):
            return JSONResponse(content=result)
        # bytes (error response from backend)
        return Response(content=result, media_type="application/json")


async def _models_list(request: Request) -> Response:
    """Forward GET /v1/models to backend, no queueing needed."""
    config = get_config()
    await authenticate_request(request)

    url = f"{config.backend.base_url}/models"

    # Forward client headers as-is, but override Host and add backend auth
    headers = dict(request.headers)
    # Remove hop-by-hop headers
    for key in ("host", "connection", "content-length", "content-encoding",
                "transfer-encoding", "x-forwarded-for", "x-forwarded-proto"):
        headers.pop(key, None)
    if config.backend.api_key:
        headers["Authorization"] = f"Bearer {config.backend.api_key}"

    import httpx
    import structlog
    logger = structlog.get_logger()
    try:
        async with httpx.AsyncClient(timeout=config.backend.timeout,
                                     trust_env=False) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning("backend.models_error", status=resp.status_code,
                               body=resp.text[:500])
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
    except (httpx.ConnectError, OSError) as e:
        logger.error("backend.models_unreachable", error=str(e))
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        logger.error("backend.models_timeout")
        return JSONResponse(status_code=504, content={"error": "Backend timeout"})


@router.get("/v1/models")
async def models(request: Request):
    return await _models_list(request)


@router.get("/v1/queue")
async def queue_status():
    """Public endpoint to check queue occupancy (no auth required)."""
    from app.models import QueueStatus as QueueStatusSchema
    q = get_queue()
    return QueueStatusSchema(
        max_length=q.max_size,
        current_waiting=q.waiting_count,
        current_processing=q.is_processing,
        queue_full=q.is_full,
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await _process_request(request, "/v1/chat/completions")


@router.post("/v1/messages")
async def messages(request: Request):
    return await _process_request(request, "/v1/messages")
