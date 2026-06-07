import asyncio
import json as json_module
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.adapters.anthropic import AnthropicAdapter
from app.adapters.openai import OpenAIAdapter
from app.config import BackendConfig, get_config
from app.core.auth import authenticate_request
from app.core.health_checker import get_health_checker
from app.core.http_client import get_client
from app.core.metrics import (
    backend_request_duration_seconds,
    gateway_tokens_total,
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

# Lazy-init strategy + backend round-robin indices
_strategy = None
_backend_indices: dict[str, int] = {}
_backend_indices_lock = asyncio.Lock()


def _ensure_strategy():
    global _strategy
    if _strategy is None:
        _strategy = create_strategy(get_config().priority.strategy)


def reset_strategy(strategy_name: str | None = None):
    """Reset or update the priority strategy."""
    global _strategy
    if strategy_name is None:
        _strategy = None
    else:
        _strategy = create_strategy(strategy_name)


def reset_backend_indices():
    """Reset round-robin indices (e.g. after backend config changes)."""
    global _backend_indices
    _backend_indices.clear()


async def _select_backend(protocol: str, model: str = "",
                           exclude: set[str] | None = None) -> Optional[BackendConfig]:
    """Select the next backend that supports the given protocol and model.

    Selection priority:
    1. Exact model match
    2. Wildcard "*" in models list (or empty models list)
    3. Protocol match (fallback if no model info)

    Skips disabled and unhealthy backends.
    """
    config = get_config()
    hc = get_health_checker()
    exclude = exclude or set()
    eligible = []
    for b in config.backends:
        if b.base_url in exclude:
            continue
        if not b.enabled:
            continue
        if protocol not in b.protocols:
            continue
        if not hc.is_healthy(b.base_url):
            continue
        # Model routing
        if not model:
            eligible.append(b)
        elif not b.models or "*" in b.models:
            eligible.append(b)
        elif model in b.models:
            eligible.append(b)
    if not eligible:
        return None
    async with _backend_indices_lock:
        idx = _backend_indices.get(protocol, 0) % len(eligible)
        _backend_indices[protocol] = idx + 1
    return eligible[idx]


# ── Debug request/response dump ────────────────────────────────────

_debug_buffer: dict = {}  # request_id -> list of bytes chunks (streaming only)


def _debug_enabled() -> bool:
    try:
        return get_config().debug.enabled
    except AssertionError:
        return False


def _debug_dir() -> str:
    return get_config().debug.dir


def _save_debug_file(request_id: str, label: str, data: str, ctx: RequestContext):
    """Write a debug payload to disk. Fire-and-forget via asyncio."""
    if not _debug_enabled():
        return
    asyncio.ensure_future(_do_save_debug(request_id, label, data, ctx))


async def _do_save_debug(request_id: str, label: str, data: str, ctx: RequestContext):
    try:
        d = Path(_debug_dir())
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]
        model = ctx.model.replace("/", "_") if ctx.model else "unknown"
        fname = d / f"{ts}_{request_id[:12]}_{model}_{label}.json"
        fname.write_text(data, encoding="utf-8")
    except Exception as e:
        import structlog
        structlog.get_logger().error(
            "debug.save_failed", request_id=request_id, label=label, error=str(e)
        )


async def _stream_wrapper(
    generator: AsyncGenerator[bytes, None],
    request_id: str,
    context: RequestContext,
) -> AsyncGenerator[bytes, None]:
    """Wrap a streaming generator to ensure signal_done is always called."""
    buf = []
    try:
        async for chunk in generator:
            if _debug_enabled():
                buf.append(chunk)
            yield chunk
    finally:
        queue = get_queue()
        await queue.signal_done(request_id)
        _record_completion(context)
        if _debug_enabled() and buf:
            _save_debug_file(request_id, "response", b"".join(buf).decode("utf-8", errors="replace"), context)


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

        # Backend-level metrics
        if context.backend_name:
            backend_request_duration_seconds.labels(
                backend=context.backend_name,
                protocol=context.protocol,
            ).observe(proc_ms / 1000.0)

        # Token metrics
        model_label = context.model or "unknown"
        if context.prompt_tokens:
            gateway_tokens_total.labels(model=model_label, type="prompt").inc(context.prompt_tokens)
        if context.completion_tokens:
            gateway_tokens_total.labels(model=model_label, type="completion").inc(context.completion_tokens)

    # Structured log
    import structlog
    logger = structlog.get_logger()
    logger.info(
        "request.completed",
        request_id=context.request_id,
        trace_id=context.trace_id,
        user=context.user_name,
        endpoint=context.endpoint,
        model=context.model,
        backend=context.backend_name,
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
    except Exception as e:
        import structlog
        structlog.get_logger().error(
            "log.save_failed", request_id=context.request_id, error=str(e)
        )


async def _execute_with_retry(protocol: str, model: str, proxy_url: str,
                               trace_id: str, context: RequestContext):
    """Execute non-streaming request with retry on 502/503 to different backends."""
    MaxRetries = 1
    tried_urls: set[str] = set()
    last_result = None

    for attempt in range(MaxRetries + 1):
        backend = await _select_backend(protocol, model=model, exclude=tried_urls)
        if backend is None:
            break
        tried_urls.add(backend.base_url)

        adapter = (
            OpenAIAdapter(backend, proxy_url=proxy_url, trace_id=trace_id)
            if protocol == "openai"
            else AnthropicAdapter(backend, proxy_url=proxy_url, trace_id=trace_id)
        )
        context.backend_name = backend.name or backend.base_url
        result = await adapter.call(context)
        last_result = result

        if context.response_status not in (502, 503):
            return result

        import structlog
        structlog.get_logger().info(
            "request.retry", attempt=attempt + 1, backend=context.backend_name
        )

    return last_result


async def _process_request(request: Request, endpoint: str) -> Response:
    """Shared request processing for both OpenAI and Anthropic endpoints."""
    _ensure_strategy()
    config = get_config()
    queue = get_queue()

    # Determine protocol from endpoint
    protocol = "openai" if endpoint == "/v1/chat/completions" else "anthropic"

    # Authenticate — returns ApiKeyInfo or None (auth disabled)
    key_info = await authenticate_request(request)
    user_name = key_info.name if key_info else "anonymous"

    # Body size check
    content_length = request.headers.get("content-length", "0")
    if int(content_length) > config.request.max_body_size:
        return JSONResponse(
            status_code=413,
            content={"error": f"Request body too large (max {config.request.max_body_size} bytes)"},
        )

    # Parse body
    body = await request.json()
    model = body.get("model", "")
    is_stream = body.get("stream", False)

    # Compute priority — from key_info or default
    if key_info:
        priority = key_info.priority
    else:
        priority = config.priority.default_priority

    # Rate limit check — use data from auth
    if key_info and key_info.rate_limit > 0:
        from app.core.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        if not limiter.check(user_name, key_info.rate_limit):
            import structlog
            structlog.get_logger().warning(
                "rate_limit.exceeded", user=user_name, rate_limit=key_info.rate_limit
            )
            return JSONResponse(
                status_code=429,
                content={"error": f"Rate limit exceeded ({key_info.rate_limit} req/min)"},
            )

    # Token quota check — use data from auth
    if key_info and (key_info.token_quota_daily > 0 or key_info.token_quota_monthly > 0):
        from app.core.quota_checker import check_quota
        quota_error = await check_quota(
            user_name, key_info.token_quota_daily, key_info.token_quota_monthly
        )
        if quota_error:
            import structlog
            structlog.get_logger().warning("quota.exceeded", user=user_name, detail=quota_error)
            return JSONResponse(status_code=429, content={"error": quota_error})

    # Select backend via round-robin (with model routing and health filter)
    backend = await _select_backend(protocol, model=model)
    if backend is None:
        return JSONResponse(
            status_code=502,
            content={"error": f"No healthy {protocol} backend available"},
        )

    # Trace ID — accept from client or generate
    trace_id = request.headers.get("x-trace-id") or ""
    if not trace_id:
        trace_id = uuid.uuid4().hex

    proxy_url = config.proxy.to_url()

    # Build context
    context = RequestContext(
        trace_id=trace_id,
        priority=priority,
        user_name=user_name,
        body=body,
        endpoint=endpoint,
        client_ip=request.client.host if request.client else "unknown",
        timestamp=time.time(),
        model=model,
        backend_name=backend.name or backend.base_url,
        protocol=protocol,
        streamed=is_stream,
    )

    # Debug: dump request body
    if _debug_enabled():
        _save_debug_file(context.request_id, "request",
                         json_module.dumps(body, ensure_ascii=False, indent=2), context)

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
    try:
        queue_timeout = config.queue.timeout if config.queue.timeout > 0 else None
        await queue.wait_for_turn(context.request_id, timeout=queue_timeout)
    except asyncio.TimeoutError:
        import structlog
        structlog.get_logger().warning(
            "queue.timeout", request_id=context.request_id, user=user_name
        )
        return JSONResponse(
            status_code=408,
            content={"error": f"Queue wait timeout ({config.queue.timeout}s)"},
        )
    context.dequeue_time = datetime.now(timezone.utc)

    if metrics_enabled():
        queue_length.set(queue.waiting_count)

    # Stream or non-stream
    if is_stream:
        adapter = (
            OpenAIAdapter(backend, proxy_url=proxy_url, trace_id=trace_id)
            if protocol == "openai"
            else AnthropicAdapter(backend, proxy_url=proxy_url, trace_id=trace_id)
        )
        generator = _stream_wrapper(adapter.stream(context), context.request_id, context)
        return StreamingResponse(generator, media_type="text/event-stream")
    else:
        result = await _execute_with_retry(protocol, model, proxy_url, trace_id, context)
        await queue.signal_done(context.request_id)
        _record_completion(context)
        if _debug_enabled():
            _save_debug_file(context.request_id, "response",
                             json_module.dumps(result, ensure_ascii=False, indent=2)
                             if isinstance(result, dict) else result.decode("utf-8", errors="replace"),
                             context)
        if isinstance(result, dict):
            return JSONResponse(content=result)
        return Response(content=result, media_type="application/json")


async def _models_list(request: Request) -> Response:
    """Forward GET /v1/models to backend, no queueing needed."""
    config = get_config()
    await authenticate_request(request)

    # Use the first OpenAI-capable backend
    backend = next((b for b in config.backends if "openai" in b.protocols), None)
    if backend is None:
        return JSONResponse(status_code=502, content={"error": "No OpenAI backend configured"})

    url = f"{backend.base_url}/models"

    # Forward client headers as-is, but override Host and add backend auth
    headers = dict(request.headers)
    for key in ("host", "connection", "content-length", "content-encoding",
                "transfer-encoding", "x-forwarded-for", "x-forwarded-proto"):
        headers.pop(key, None)
    if backend.api_key:
        headers["Authorization"] = f"Bearer {backend.api_key}"

    import httpx
    import structlog
    logger = structlog.get_logger()
    proxy_url = config.proxy.to_url() or None
    try:
        client = await get_client(timeout=backend.timeout, proxy_url=proxy_url)
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


@router.delete("/v1/queue/{request_id}")
async def cancel_request(request_id: str, request: Request):
    """Cancel a queued request. Requires authentication."""
    key_info = await authenticate_request(request)
    user_name = key_info.name if key_info else "anonymous"

    q = get_queue()
    cancelled = await q.cancel(request_id, user_name)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Request not found in queue")
    return {"ok": True, "request_id": request_id}


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
        processing_count=q.processing_count,
        max_concurrency=q.max_concurrency,
        queue_full=q.is_full,
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await _process_request(request, "/v1/chat/completions")


@router.post("/v1/messages")
async def messages(request: Request):
    return await _process_request(request, "/v1/messages")
