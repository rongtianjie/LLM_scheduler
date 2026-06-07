# LLM Scheduler

## Overview
An LLM API gateway proxy with priority queuing, concurrency control, queue timeout, rate limiting, token quota, log retention, API key authentication, structured logging, Prometheus metrics, CORS support, proxy server support, and an embedded admin dashboard with custom login page and Chart.js charts.

## Interaction Rules

在执行任务前，必须先问我问题。要求：
- 一次只问一个问题
- 根据我的回答，继续追问
- 直到你有 95% 的信心理解我的真实需求和目标，然后才给出方案

## Architecture
- **FastAPI** single-process application on port 8001
- **CORS middleware** for cross-origin support (configurable origins)
- **GZipMiddleware** for response compression (minimum_size=1000)
- **SessionMiddleware** for admin session/cookie authentication (24h expiry, https_only configurable)
- **Priority queue** with configurable concurrency and timeout (`asyncio.Condition`-based); supports `cancel()` and dynamic size/concurrency updates
- **In-memory rate limiter** (sliding window, per API key requests/minute; uses `deque` not `list`)
- **Token quota checker** (daily/monthly token limits per API key, SQL-based)
- **Log retention** (automatic cleanup on startup, by retention_days and max_records)
- **Unified backend configs** with model-level routing and health-aware selection
- **Health checker** background asyncio task: periodic HTTP GET /health probes, 3-failure threshold, auto-recovery
- **Shared httpx AsyncClient** connection pool via `app/core/http_client.py`
- **Global proxy** support (HTTP/HTTPS/SOCKS5) for backend requests via `httpx` + `httpx-socks`
- **SQLite** (WAL mode, indexed) for API key storage and request logging
- **Jinja2** admin dashboard with Chart.js charts (locally bundled), dark mode, responsive layout
- **Prometheus** metrics at `/metrics` (includes `gateway_backend_request_duration_seconds`, `gateway_tokens_total`)
- **structlog** for structured JSON logging with trace_id binding

## Key Files

| Path | Purpose |
|------|---------|
| `app/main.py` | App factory, startup/shutdown, CORS/SessionMiddleware, GZipMiddleware, password auto-hash, health ready |
| `app/config.py` | YAML config loading via Pydantic (includes Proxy/LogRetention/CorsConfig/RequestConfig fields) |
| `app/database.py` | SQLite init (WAL mode, indexes, migrations, log cleanup) |
| `app/core/queue.py` | Priority queue with asyncio.Condition + timeout + cancel + update_max_size/update_concurrency |
| `app/core/auth.py` | API Key Bearer auth (returns ApiKeyInfo dataclass) + Session-based admin auth (bcrypt) |
| `app/core/metrics.py` | Prometheus metric definitions (backend duration, tokens total) |
| `app/core/rate_limiter.py` | In-memory sliding window rate limiter (deque-based) |
| `app/core/quota_checker.py` | Token usage quota checker (daily/monthly) |
| `app/core/health_checker.py` | Background health checker (periodic probe, unhealthy skip, auto-recovery) |
| `app/core/password.py` | bcrypt password hashing + verify with legacy plaintext fallback |
| `app/core/http_client.py` | Shared httpx.AsyncClient connection pool |
| `app/adapters/base.py` | Base adapter with proxy_url + trace_id support |
| `app/adapters/openai.py` | OpenAI-format adapter (x-trace-id header injection) |
| `app/adapters/anthropic.py` | Anthropic-format adapter (x-trace-id header injection) |
| `app/strategies/*.py` | Priority strategy (api_key, accepts optional key_info to skip DB query) |
| `app/api/proxy.py` | Proxy endpoints (model routing, health-aware select, retry, body size limit, cancel endpoint) |
| `app/api/admin_api.py` | Admin REST API (includes password change, backend health, enhanced log filtering) |
| `app/api/admin_pages.py` | Admin page routes (bcrypt login, login lockout, reset_login_failures) |
| `app/static/chart.umd.min.js` | Chart.js v4 (locally bundled) |
| `app/static/style.css` | Light + dark theme CSS, responsive layout, column selector styles |

## Debug Mode

Enable via `debug.enabled: true` in config. Each proxy request saves:
- `data/debug/{timestamp}_{request_id}_{model}_request.json` — full client request body
- `data/debug/{timestamp}_{request_id}_{model}_response.json` — full backend response

Files are indented JSON for readability. Streaming responses are buffered in memory
and flushed when the stream completes.

## Running

```bash
# Create venv and install dependencies
uv sync

# Configure
cp config.yaml config.local.yaml  # edit as needed

# Run (dev, uses virtual environment automatically)
uv run python -m app.main

# Run (production with Docker)
docker-compose up -d
```

## Testing

```bash
uv run pytest tests/
```

## Configuration

`config.yaml` contains bootstrap-only settings (server, auth, admin, database, queue, logging,
log_retention, cors, proxy). Runtime settings (queue, backends, debug, metrics, proxy) are
managed through the admin page at `/admin/management`. Default values are in `app/config.py`.

### Key Config Sections
- **queue**: timeout, max_length, concurrency
- **request.max_body_size**: Reject requests exceeding this byte limit (413)
- **log_retention**: Auto-cleanup on startup (`retention_days`, `max_records`)
- **cors.origins**: Allowed CORS origins (default `["*"]`)
- **admin.session_https_only**: Set `Secure` flag on session cookies (default false)
- **backends[].models**: List of model names this backend handles (`["*"]` = wildcard)
- **backends[].enabled**: Per-backend enable/disable toggle

### Password Security
- Admin password is auto-hashed with bcrypt on first startup (plaintext → hash in config.yaml)
- `verify_password()` detects bcrypt prefix (`$2b$`/`$2a$`/`$2y$`) and falls back to plaintext comparison
- Password reset: create `reset_admin_password` file with new plaintext password, restart
- Login lockout: 5 failed attempts per IP → 300s lockout (in-memory, module-level dict)
- Password change API: `PUT /admin/api/admin/password` (requires current password)

### Proxy Configuration
Global proxy for all backend LLM requests. Supports HTTP, HTTPS, and SOCKS5 protocols.
Optional username/password authentication. Configurable via `config.yaml` or the Management
page (System tab). When changed via the admin API, http_client is recreated immediately.

## API Endpoints

### Proxy (port 8001)
| Path | Description |
|------|-------------|
| `GET /` | Redirect to admin dashboard |
| `GET /health` | Health check |
| `GET /health/ready` | Readiness probe (DB reachable + ≥1 backend healthy) |
| `POST /v1/chat/completions` | OpenAI-compatible proxy |
| `POST /v1/messages` | Anthropic-compatible proxy |
| `GET /v1/models` | List available models (forwarded to backend) |
| `GET /v1/queue` | Queue status (public, no auth required) |
| `DELETE /v1/queue/{request_id}` | Cancel a queued request (auth required) |
| `GET /metrics` | Prometheus metrics |

### Admin (port 8001)
| Path | Description |
|------|-------------|
| `GET /admin/login` | Login page |
| `POST /admin/login` | Login form submit (session cookie, bcrypt + lockout) |
| `GET /admin/logout` | Logout (clear session) |
| `GET /admin` | Dashboard (with Chart.js charts) |
| `GET /admin/api-keys` | API key management page |
| `GET /admin/logs` | Request logs page |
| `GET /admin/management` | Runtime config (Scheduling / Backend / System tabs) |
| `GET /admin/api/queue` | Queue status (JSON) |
| `GET /admin/api/keys` | List API keys |
| `POST /admin/api/keys` | Create API key |
| `PUT /admin/api/keys/{id}` | Update API key |
| `DELETE /admin/api/keys/{id}` | Delete API key |
| `GET /admin/api/stats` | Dashboard stats (supports `?period=24h&key_id=1`) |
| `GET /admin/api/stats/timeseries` | Time-series data for charts (`?period=24h`) |
| `GET /admin/api/logs` | Query logs (supports `?model=&status=&date_start=&date_end=`) |
| `GET /admin/api/config` | Get runtime config (queue, backends, debug, metrics, proxy) |
| `PUT /admin/api/config` | Update runtime config (applies immediately) |
| `GET /admin/api/backends/health` | Backend health statuses |
| `PUT /admin/api/admin/password` | Change admin password |

## Request Flow

1. Client sends request → auth check (returns ApiKeyInfo) → rate limit check → quota check → priority computation
2. Request enqueued (429 if queue full, 413 if body too large)
3. Waits via `asyncio.Condition` until at front + no active processing (408 on timeout)
4. Model routing: select backend (health-aware, model match > wildcard > protocol fallback)
5. Adapter forwards to backend (streaming or non-streaming, with proxy support, x-trace-id injection)
6. On failure (502/503): retry once to a different backend (non-streaming only)
7. On completion: signal next waiting request, log (with trace_id), record metrics

## Rate Limiting

- In-memory sliding window (60s), per API key
- Configured via `rate_limit` field on API keys (requests/minute, 0 = unlimited)
- Window uses `collections.deque` (not `list`) for O(1) popleft
- Exceeded requests receive HTTP 429 with descriptive error message

## Token Quota

- Daily and monthly token limits per API key
- Checked via SQL SUM query on `request_logs.prompt_tokens + completion_tokens`
- Configured via `token_quota_daily` / `token_quota_monthly` fields (0 = unlimited)
- Exceeded requests receive HTTP 429 with quota exceeded message

## Log Retention

- Automatic cleanup on application startup
- `retention_days`: Delete logs older than N days
- `max_records`: Trim oldest records if total exceeds this number

## Priority

- Lower number = higher priority
- `api_key` strategy: priority from API key record in SQLite; accepts optional `key_info` to skip redundant DB query
- Default priority: 100

## Stream Passthrough

SSE events are forwarded byte-for-byte without parsing or modification. OpenAI's `data: [DONE]` and Anthropic's `event: message_stop` pass through unaltered.

## Technical Notes

### 1. Password Hashing (bcrypt)
Passwords are stored as bcrypt hashes. `app/core/password.py` provides `hash_password()` and `verify_password()`. The verify function auto-detects bcrypt format (prefix `$2b$`/`$2a$`/`$2y$`) and falls back to plaintext comparison for legacy passwords. On startup, `app/main.py` auto-hashes any short (< 60 char) password in config.yaml.

### 2. HTTP Connection Pool
`app/core/http_client.py` manages a shared `httpx.AsyncClient` instance. Use `init_client()` / `get_client()` / `close_client()` lifecycle functions. The client is rebuilt when the proxy URL changes.

### 3. Health Checker
`app/core/health_checker.py` runs as a background asyncio task. It periodically HTTP GETs each backend's `/health` endpoint. Three consecutive failures mark a backend "unhealthy"; one success restores it. The proxy's `_select_backend()` skips unhealthy nodes. Access health status via `GET /admin/api/backends/health`.

### 4. Queue Cancel
`PriorityQueue.cancel(request_id, user_name)` removes a request from the heap and notifies waiting coroutines. Only the owning user can cancel their request. The endpoint is `DELETE /v1/queue/{request_id}`.

### 5. Model Routing
Each `BackendConfig` has a `models: list[str]` field. `_select_backend()` matches by: exact model name > wildcard `*` > protocol-only fallback. A backend with empty/null models matches all models. Use `exclude` parameter to skip already-tried backends for retry.

### 6. Trace ID
`RequestContext.trace_id` is set from the `x-trace-id` request header (client-provided) or auto-generated (uuid4 hex). It propagates to backend requests via `x-trace-id` header in adapters and is bound to structlog. All adapters accept `trace_id` as a constructor parameter.

### 7. Rate Limiter: deque
The rate limiter uses `collections.deque` instead of `list` for the sliding window. Use `popleft()` (O(1)) instead of `pop(0)` (O(n)). Tests must use `deque` for mock data.

### 8. Auth Returns ApiKeyInfo
`authenticate_request()` now returns an `ApiKeyInfo` dataclass (including `name`, `priority`, `rate_limit`, `token_quota_daily`, `token_quota_monthly`) instead of just a user name. This avoids redundant DB queries in the proxy and strategy layers. The `api_key_based` strategy accepts an optional `key_info` parameter to skip the DB lookup.
