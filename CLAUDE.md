# LLM Gateway Proxy

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
- **SessionMiddleware** for admin session/cookie authentication (24h expiry, https_only configurable)
- **Priority queue** with configurable concurrency and timeout (`asyncio.Condition`-based)
- **In-memory rate limiter** (sliding window, per API key requests/minute)
- **Token quota checker** (daily/monthly token limits per API key, SQL-based)
- **Log retention** (automatic cleanup on startup, by retention_days and max_records)
- **Separate backend configs** for OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`)
- **Global proxy** support (HTTP/HTTPS/SOCKS5) for backend requests via `httpx` + `httpx-socks`
- **SQLite** (WAL mode, indexed) for API key storage and request logging
- **Jinja2** admin dashboard with Chart.js charts (locally bundled)
- **Prometheus** metrics at `/metrics`
- **structlog** for structured JSON logging

## Key Files

| Path | Purpose |
|------|---------|
| `app/main.py` | App factory, startup/shutdown, CORS/SessionMiddleware, route mounting |
| `app/config.py` | YAML config loading via Pydantic (includes Proxy/LogRetention/CorsConfig, Field validators) |
| `app/database.py` | SQLite init (WAL mode, indexes, migrations, log cleanup) |
| `app/core/queue.py` | Priority queue with asyncio.Condition + timeout support |
| `app/core/auth.py` | API Key Bearer auth + Session-based admin auth |
| `app/core/metrics.py` | Prometheus metric definitions |
| `app/core/rate_limiter.py` | In-memory sliding window rate limiter |
| `app/core/quota_checker.py` | Token usage quota checker (daily/monthly) |
| `app/adapters/base.py` | Base adapter with proxy_url support |
| `app/adapters/openai.py` | OpenAI-format adapter |
| `app/adapters/anthropic.py` | Anthropic-format adapter |
| `app/strategies/*.py` | Priority strategy (api_key) |
| `app/api/proxy.py` | Proxy endpoints (rate limit, quota check, timeout) |
| `app/api/admin_api.py` | Admin REST API (includes timeseries stats, config management) |
| `app/api/admin_pages.py` | Admin page routes (login/logout/dashboard) |
| `app/static/chart.umd.min.js` | Chart.js v4 (locally bundled) |
| `app/static/style.css` | Light sci-fi theme CSS |
| `config.yaml` | Default configuration |

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

### New Config Sections
- **queue.timeout**: Max seconds a request waits before 408 (0 = unlimited, default 300)
- **log_retention**: Auto-cleanup on startup (`retention_days`, `max_records`)
- **cors.origins**: Allowed CORS origins (default `["*"]`)
- **admin.session_https_only**: Set `Secure` flag on session cookies (default false)

### Proxy Configuration
Global proxy for all backend LLM requests. Supports HTTP, HTTPS, and SOCKS5 protocols.
Optional username/password authentication. Configurable via `config.yaml` or the Management
page (System tab). When changed via the admin API, both adapters are recreated immediately.

## API Endpoints

### Proxy (port 8001)
| Path | Description |
|------|-------------|
| `GET /health` | Health check |
| `POST /v1/chat/completions` | OpenAI-compatible proxy |
| `POST /v1/messages` | Anthropic-compatible proxy |
| `GET /v1/models` | List available models (forwarded to backend) |
| **`GET /v1/queue`** | **Queue status (public, no auth required)** |
| `GET /metrics` | Prometheus metrics |

### Admin (port 8001)
| Path | Description |
|------|-------------|
| `GET /admin/login` | Login page |
| `POST /admin/login` | Login form submit (session cookie) |
| `GET /admin/logout` | Logout (clear session) |
| `GET /admin` | Dashboard (with Chart.js charts) |
| `GET /admin/api-keys` | API key management page |
| `GET /admin/logs` | Request logs page |
| **`GET /admin/management`** | **Runtime config (Scheduling / Backend / System tabs)** |
| `GET /admin/api/queue` | Queue status (JSON) |
| `GET /admin/api/keys` | List API keys |
| `POST /admin/api/keys` | Create API key |
| `PUT /admin/api/keys/{id}` | Update API key |
| `DELETE /admin/api/keys/{id}` | Delete API key |
| `GET /admin/api/stats` | Dashboard stats (supports `?period=24h&key_id=1`) |
| `GET /admin/api/stats/timeseries` | Time-series data for charts (`?period=24h`) |
| `GET /admin/api/logs` | Query logs (paginated, includes token columns) |
| `GET /admin/api/config` | Get runtime config (queue, backends, debug, metrics, proxy) |
| `PUT /admin/api/config` | Update runtime config (applies immediately) |
| `POST /admin/api/config/sync-openai-to-anthropic` | Copy OpenAI backend config to Anthropic |

## Request Flow

1. Client sends request → auth check → rate limit check → quota check → priority computation
2. Request enqueued (429 if queue full)
3. Waits via `asyncio.Condition` until at front + no active processing (408 on timeout)
4. Adapter forwards to backend (streaming or non-streaming, with proxy support)
5. On completion: signal next waiting request, log, record metrics

## Rate Limiting

- In-memory sliding window (60s), per API key
- Configured via `rate_limit` field on API keys (requests/minute, 0 = unlimited)
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
- `api_key` strategy: priority from API key record in SQLite
- Default priority: 100

## Stream Passthrough

SSE events are forwarded byte-for-byte without parsing or modification. OpenAI's `data: [DONE]` and Anthropic's `event: message_stop` pass through unaltered.
