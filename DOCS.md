# LLM Gateway Proxy — Detailed Documentation

> [**中文文档**](./DOCS.zh.md) | [**English Documentation**](./DOCS.md)
>
> For a quick overview, see [README.md](./README.md).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Project Structure](#project-structure)
4. [API Reference](#api-reference)
   - [Proxy Endpoints](#proxy-endpoints)
   - [Admin Page Endpoints](#admin-page-endpoints)
   - [Admin REST API](#admin-rest-api)
5. [Configuration Reference](#configuration-reference)
6. [Authentication & Authorization](#authentication--authorization)
7. [Request Processing Flow](#request-processing-flow)
8. [Core Components](#core-components)
   - [Priority Queue](#priority-queue)
   - [Rate Limiter](#rate-limiter)
   - [Token Quota Checker](#token-quota-checker)
   - [Adapters](#adapters)
   - [Priority Strategies](#priority-strategies)
9. [Proxy Support](#proxy-support)
10. [Debug Mode](#debug-mode)
11. [Logging & Metrics](#logging--metrics)

---

## Architecture Overview

```
┌──────────────┐     ┌─────────────────────────────────────────────────────┐
│   Client     │────▶│              LLM Gateway Proxy (FastAPI)            │
│  (curl/SDK)  │     │                                                     │
└──────────────┘     │  ┌──────────┐  ┌───────────┐  ┌──────────────────┐  │
                     │  │   Auth   │─▶│  Rate     │─▶│  Token Quota     │  │
                     │  │  (API    │  │  Limiter  │  │  Checker         │  │
                     │  │   Key)   │  │  (in-mem) │  │  (SQL-based)     │  │
                     │  └──────────┘  └───────────┘  └──────────────────┘  │
                     │                                          │          │
                     │  ┌───────────────────────────────────────▼────────┐ │
                     │  │         Priority Queue (asyncio.Condition)     │ │
                     │  │   ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐    │ │
                     │  │   │ P1  │ │ P2  │ │ P3  │ │ P4  │ │ P5  │...  │ │
                     │  │   └─────┘ └─────┘ └─────┘ └─────┘ └─────┘    │ │
                     │  └──────────────────────┬──────────────────────────┘ │
                     │                         │                          │
                     │  ┌──────────────────────▼──────────────────────────┐ │
                     │  │       Adapter (Round-Robin Load Balancing)      │ │
                     │  │  ┌────────────┐  ┌────────────┐  ┌───────────┐ │ │
                     │  │  │ OpenAI     │  │ Anthropic  │  │  (more)   │ │ │
                     │  │  │ Adapter    │  │ Adapter    │  │           │ │ │
                     │  │  └─────┬──────┘  └──────┬─────┘  └───────────┘ │ │
                     │  └────────┼─────────────────┼──────────────────────┘ │
                     │           │                 │                        │
                     │           ▼                 ▼                        │
                     │   ┌──────────────┐  ┌──────────────┐                 │
                     │   │ OpenAI API   │  │ Anthropic API│                 │
                     │   │  Backend(s)  │  │  Backend(s)  │                 │
                     │   └──────────────┘  └──────────────┘                 │
                     │                                                     │
                     │  ┌──────────────────────────────────────────────┐   │
                     │  │  Proxy (HTTP/HTTPS/SOCKS5) — optional tunnel │   │
                     │  └──────────────────────────────────────────────┘   │
                     └─────────────────────────────────────────────────────┘
```

The gateway is a **single-process FastAPI application** that acts as a reverse proxy for LLM API requests. All incoming requests pass through authentication → rate limiting → quota checking → priority queueing → adapter forwarding in sequence.

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Web Framework | **FastAPI** (Python 3.11+) | Async HTTP server with automatic OpenAPI support |
| ASGI Server | **Uvicorn** | Production-grade ASGI server |
| Database | **SQLite** via **aiosqlite** | Persistent storage for API keys and request logs (WAL mode) |
| HTTP Client | **httpx** | Async HTTP client for backend requests |
| Configuration | **Pydantic** + **PyYAML** | Schema-validated YAML configuration |
| Auth (Admin) | **Starlette SessionMiddleware** | Session/Cookie-based admin authentication (24h expiry) |
| Auth (Proxy) | Bearer token (API Key) | Custom API key authentication against SQLite |
| Queue | **asyncio.Condition** | Priority queue with configurable concurrency and timeout |
| Logging | **structlog** | Structured JSON logging |
| Metrics | **prometheus_client** | Prometheus metrics at `/metrics` |
| Templates | **Jinja2** | Server-side rendering for admin dashboard |
| Charts | **Chart.js v4** (local bundle) | Time-series charts on dashboard |
| Proxy | **httpx-socks** | SOCKS5 proxy support via `socksio` |

## Project Structure

```
app/
├── main.py                  # App factory, lifespan, CORS/SessionMiddleware, route mounting
├── config.py                # YAML config loading via Pydantic models
├── database.py              # SQLite initialization (WAL mode, migrations, log cleanup)
├── models.py                # Pydantic schemas + dataclass models
│
├── api/
│   ├── proxy.py             # Proxy endpoints (/v1/chat/completions, /v1/messages, /v1/models, /v1/queue)
│   ├── admin_api.py         # Admin REST API (keys, stats, logs, config management)
│   └── admin_pages.py       # Admin page routes (login, logout, dashboard, api-keys, logs, management)
│
├── core/
│   ├── queue.py             # Async priority queue (heapq + asyncio.Condition)
│   ├── auth.py              # API key authentication + admin session management
│   ├── metrics.py           # Prometheus metric definitions
│   ├── rate_limiter.py      # In-memory sliding window rate limiter
│   └── quota_checker.py     # SQL-based daily/monthly token quota checker
│
├── adapters/
│   ├── base.py              # Abstract base adapter with proxy support
│   ├── openai.py            # OpenAI-format adapter (/chat/completions)
│   └── anthropic.py         # Anthropic-format adapter (/messages)
│
├── strategies/
│   ├── base.py              # Priority strategy abstraction
│   ├── api_key_based.py     # API-key-based priority strategy
│   └── factory.py           # Strategy factory
│
├── templates/               # Jinja2 HTML templates
│   ├── base.html            # Layout (sidebar, nav, 401 interceptor)
│   ├── login.html           # Login page
│   ├── dashboard.html       # Dashboard (stats, charts, per-key table)
│   ├── api_keys.html        # API key management page
│   ├── logs.html            # Request logs page
│   └── management.html      # Runtime configuration (Scheduling/Backend/System tabs)
│
├── static/
│   ├── style.css            # Sci-fi theme stylesheet
│   └── chart.umd.min.js     # Chart.js v4 (local deployment)
│
data/                        # Runtime data (auto-created)
├── gateway.db               # SQLite database
└── debug/                   # Debug mode request/response dumps
```

---

## API Reference

### Proxy Endpoints

#### `POST /v1/chat/completions` — OpenAI-compatible proxy

Forwards a request to an OpenAI-compatible backend. Supports both streaming and non-streaming modes.

**Authentication:** Required (unless `auth.enabled = false`)

**Headers:**
- `Authorization: Bearer <api-key>` — API key authentication
- `Content-Type: application/json`

**Request Body:** Same as [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat). The `stream` field determines streaming vs non-streaming.

**Example:**
```bash
curl http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

**Response (non-streaming):** JSON — same format as OpenAI API response, including `usage` object for token counts.

**Response (streaming):** `text/event-stream` — SSE events forwarded transparently. Token usage is extracted from the final `data: [DONE]` chunk's usage info.

**Status Codes:**
- `200 OK` — Successful response
- `401 Unauthorized` — Missing or invalid API key
- `403 Forbidden` — API key is disabled
- `429 Too Many Requests` — Queue full / rate limited / quota exceeded
- `408 Request Timeout` — Queue wait timeout
- `502 Bad Gateway` — No backend configured / backend unreachable
- `504 Gateway Timeout` — Backend request timeout

---

#### `POST /v1/messages` — Anthropic-compatible proxy

Forwards a request to an Anthropic-compatible backend. Supports both streaming and non-streaming modes.

**Authentication:** Required (unless `auth.enabled = false`)

**Headers:**
- `Authorization: Bearer <api-key>` — API key authentication
- `Content-Type: application/json`

**Request Body:** Same as [Anthropic Messages API](https://docs.anthropic.com/en/api/messages). The `stream` field determines streaming vs non-streaming.

**Example:**
```bash
curl http://localhost:8001/v1/messages \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

**Response (non-streaming):** JSON — same format as Anthropic API response, including `usage` with `input_tokens` and `output_tokens`.

**Response (streaming):** `text/event-stream` — SSE events forwarded transparently. Token usage is extracted from `message_start` (input tokens) and `message_delta` (output tokens) events.

**Status Codes:** Same as `/v1/chat/completions`.

---

#### `GET /v1/queue` — Queue status (public)

Returns the current queue state. **No authentication required.**

**Example:**
```bash
curl http://localhost:8001/v1/queue
```

**Response:**
```json
{
    "max_length": 5,
    "current_waiting": 0,
    "current_processing": false,
    "processing_count": 0,
    "max_concurrency": 1,
    "queue_full": false
}
```

**Status Codes:**
- `200 OK` — Queue status returned

---

#### `GET /v1/models` — List available models

Forwards to the first available OpenAI-capable backend's `/models` endpoint. **Authentication required.**

**Example:**
```bash
curl http://localhost:8001/v1/models \
  -H "Authorization: Bearer sk-your-api-key"
```

**Response:** JSON — forwarded from backend.

**Status Codes:**
- `200 OK` — Model list returned
- `401 Unauthorized` — Missing or invalid API key
- `502 Bad Gateway` — No OpenAI backend configured / backend unreachable
- `504 Gateway Timeout` — Backend timeout

---

#### `GET /health` — Health check

Simple health check endpoint. **No authentication required.**

**Example:**
```bash
curl http://localhost:8001/health
```

**Response:**
```json
{
    "status": "ok"
}
```

**Status Codes:**
- `200 OK`

---

#### `GET /metrics` — Prometheus metrics

Returns Prometheus-format metrics. Only available when `metrics.enabled = true`. **No authentication required.**

**Exposed metrics:**
- `gateway_requests_total` (Counter) — Total requests by endpoint, status code, user
- `gateway_queue_length` (Gauge) — Current queue waiting count
- `gateway_requests_processing` (Gauge) — Currently processing (0 or 1)
- `gateway_request_duration_seconds` (Histogram) — End-to-end duration in seconds
- `gateway_wait_time_seconds` (Histogram) — Queue wait time in seconds

**Example:**
```bash
curl http://localhost:8001/metrics
```

**Status Codes:**
- `200 OK` — Prometheus text format

---

#### `GET /` — Root redirect

Redirects to the admin dashboard at `/admin`.

**Status Codes:**
- `302 Found` — Redirect to `/admin`

---

### Admin Page Endpoints

All admin page routes are behind session-based authentication. Unauthenticated requests redirect to `/admin/login`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/login` | Login page (redirects to `/admin` if already logged in) |
| `POST` | `/admin/login` | Login form submission |
| `GET` | `/admin/logout` | Logout (clears session, redirects to login) |
| `GET` | `/admin` | Dashboard (real-time queue stats, Chart.js charts, per-key breakdown) |
| `GET` | `/admin/api-keys` | API key management page (create/edit/delete) |
| `GET` | `/admin/logs` | Request logs page (paginated, filterable) |
| `GET` | `/admin/management` | Runtime configuration page (Scheduling/Backend/System tabs) |

**Login:**
```bash
curl -c cookies.txt -X POST http://localhost:8001/admin/login \
  -d "username=admin&password=admin123"
```

**Static files:**
| Path | Content |
|------|---------|
| `/static/style.css` | Sci-fi theme stylesheet |
| `/static/chart.umd.min.js` | Chart.js v4 bundle |

---

### Admin REST API

All admin REST API endpoints require session-based authentication (obtained via `/admin/login`). Unauthenticated requests return `401` JSON.

Base path: `/admin/api`

#### `GET /admin/api/queue` — Queue status

**Parameters:** None

**Response:** Same format as `/v1/queue` but requires admin auth.

```json
{
    "max_length": 5,
    "current_waiting": 0,
    "current_processing": false,
    "processing_count": 0,
    "max_concurrency": 1,
    "queue_full": false
}
```

**Status Codes:** `200 OK`, `401 Unauthorized`

---

#### `GET /admin/api/keys` — List API keys

Returns all configured API keys with their settings.

**Response:**
```json
[
    {
        "id": 1,
        "key": "sk-abc123...",
        "name": "alice",
        "priority": 50,
        "enabled": true,
        "created_at": "2024-01-01T00:00:00.000Z",
        "rate_limit": 30,
        "token_quota_daily": 100000,
        "token_quota_monthly": 500000
    }
]
```

**Status Codes:** `200 OK`, `401 Unauthorized`

---

#### `POST /admin/api/keys` — Create API key

**Request Body:**
```json
{
    "name": "alice",
    "priority": 50,
    "rate_limit": 30,
    "token_quota_daily": 100000,
    "token_quota_monthly": 500000
}
```

**Fields:**
- `name` (string, required) — Human-readable name
- `priority` (int, default: `100`) — Lower = higher priority
- `rate_limit` (int, default: `0`) — Requests per minute limit (0 = unlimited)
- `token_quota_daily` (int, default: `0`) — Daily token limit (0 = unlimited)
- `token_quota_monthly` (int, default: `0`) — Monthly token limit (0 = unlimited)

**Response (201 Created):**
```json
{
    "id": 2,
    "key": "sk-...",          // Full key — shown only once!
    "name": "alice",
    "priority": 50,
    "enabled": true,
    "created_at": "2024-01-01T00:00:00.000Z",
    "rate_limit": 30,
    "token_quota_daily": 100000,
    "token_quota_monthly": 500000
}
```

**Status Codes:** `201 Created`, `401 Unauthorized`

---

#### `PUT /admin/api/keys/{key_id}` — Update API key

**Path Parameters:**
- `key_id` (int) — API key ID

**Request Body:** All fields are optional; only provided fields are updated.
```json
{
    "name": "alice-updated",
    "priority": 60,
    "enabled": true,
    "rate_limit": 50,
    "token_quota_daily": 200000,
    "token_quota_monthly": 1000000
}
```

**Response:** Updated `ApiKeyResponse` object.

**Status Codes:** `200 OK`, `401 Unauthorized`, `404 Not Found`

---

#### `DELETE /admin/api/keys/{key_id}` — Delete API key

**Path Parameters:**
- `key_id` (int) — API key ID

**Response:**
```json
{
    "ok": true
}
```

**Status Codes:** `200 OK`, `401 Unauthorized`, `404 Not Found`

---

#### `GET /admin/api/stats` — Dashboard statistics

**Query Parameters:**
- `period` (string, default: `"24h"`) — Time range. Supported values: `1h`, `6h`, `24h`, `7d`, `30d`, `all`
- `key_id` (int, optional) — Filter stats by specific API key ID

**Response:**
```json
{
    "period": "24h",
    "total_requests": 150,
    "total_prompt_tokens": 50000,
    "total_completion_tokens": 100000,
    "errors": 3,
    "per_key": [
        {
            "name": "alice",
            "key_id": 1,
            "requests": 100,
            "prompt_tokens": 30000,
            "completion_tokens": 60000
        }
    ]
}
```

**Status Codes:** `200 OK`, `401 Unauthorized`

---

#### `GET /admin/api/stats/timeseries` — Time-series data

**Query Parameters:**
- `period` (string, default: `"24h"`) — Time range. Supported values: `1h`, `6h`, `24h`, `7d`, `30d`, `all`

**Bucket intervals:**
| Period | Interval |
|--------|----------|
| `1h`   | 5 minutes |
| `6h`   | 30 minutes |
| `24h`  | 1 hour |
| `7d`   | 6 hours |
| `30d`  | 1 day |
| `all`  | 1 day |

**Response:**
```json
{
    "period": "24h",
    "interval": "1h",
    "buckets": [
        {
            "timestamp": "2024-01-01T10:00:00Z",
            "requests": 10,
            "prompt_tokens": 5000,
            "completion_tokens": 10000,
            "errors": 0
        }
    ]
}
```

**Status Codes:** `200 OK`, `401 Unauthorized`

---

#### `GET /admin/api/logs` — Request logs

**Query Parameters:**
- `page` (int, default: `1`) — Page number (1-indexed)
- `per_page` (int, default: `50`, max: typically limited by query) — Items per page
- `endpoint` (string, optional) — Filter by endpoint (e.g., `/v1/chat/completions`)
- `user` (string, optional) — Filter by username

**Response:**
```json
{
    "total": 500,
    "page": 1,
    "per_page": 50,
    "items": [
        {
            "id": 123,
            "request_id": "a1b2c3d4e5...",
            "user_name": "alice",
            "endpoint": "/v1/chat/completions",
            "model": "gpt-4",
            "priority": 50,
            "wait_time_ms": 120,
            "processing_time_ms": 3500,
            "status_code": 200,
            "streamed": true,
            "prompt_tokens": 150,
            "completion_tokens": 300,
            "error": null,
            "client_ip": "192.168.1.1",
            "created_at": "2024-01-01T10:00:00"
        }
    ]
}
```

**Status Codes:** `200 OK`, `401 Unauthorized`

---

#### `GET /admin/api/config` — Get runtime config

Returns all runtime-configurable settings.

**Response:**
```json
{
    "queue": {
        "max_length": 5,
        "concurrency": 1,
        "timeout": 300
    },
    "priority": {
        "strategy": "api_key",
        "default_priority": 100
    },
    "backends": [
        {
            "name": "openai-main",
            "base_url": "https://api.openai.com",
            "api_key": "sk-...",
            "timeout": 300,
            "protocols": ["openai"],
            "enabled": true
        }
    ],
    "debug": {
        "enabled": false,
        "dir": "data/debug"
    },
    "metrics": {
        "enabled": true
    },
    "proxy": {
        "enabled": false,
        "protocol": "http",
        "host": "",
        "port": 0,
        "username": "",
        "password": ""
    },
    "log_retention": {
        "retention_days": 90,
        "max_records": 100000
    },
    "cors": {
        "origins": ["*"]
    }
}
```

**Status Codes:** `200 OK`, `401 Unauthorized`

---

#### `PUT /admin/api/config` — Update runtime config

Applies configuration changes immediately in-memory without restarting the server.

**Request Body:** All fields are optional; only provided sections/fields are updated.

```json
{
    "queue": {
        "max_length": 10,
        "concurrency": 2,
        "timeout": 600
    },
    "priority": {
        "strategy": "api_key",
        "default_priority": 100
    },
    "backends": [
        {
            "name": "openai-main",
            "base_url": "https://api.openai.com",
            "api_key": "sk-...",
            "timeout": 300,
            "protocols": ["openai"],
            "enabled": true
        }
    ],
    "debug": {
        "enabled": true,
        "dir": "data/debug"
    },
    "metrics": {
        "enabled": true
    },
    "proxy": {
        "enabled": true,
        "protocol": "socks5",
        "host": "127.0.0.1",
        "port": 1080,
        "username": "",
        "password": ""
    }
}
```

**Response:**
```json
{
    "ok": true,
    "changes": ["queue.max_length", "queue.concurrency", "proxy.enabled"]
}
```

**Notes:**
- Backends are fully replaced (not merged) — all backends must be sent in the array.
- Proxy changes take effect immediately for all subsequent requests.
- Queue `max_length` and `concurrency` are applied to the running queue instance.
- When `priority.strategy` changes, the strategy is recreated from the factory.

**Status Codes:** `200 OK`, `401 Unauthorized`, `422 Unprocessable Entity` (invalid proxy protocol)

---

## Configuration Reference

### Config file loading order

1. Primary config file path (default: `config.yaml`, overridable via `LLM_GATEWAY_CONFIG` env var)
2. Auto-merge `config.local.yaml` if it exists alongside the primary file (deep merge, local values take precedence)

### Full Configuration Model

```python
# app/config.py — Pydantic models

class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8001

class AuthConfig:
    enabled: bool = True              # API key authentication toggle

class AdminConfig:
    enabled: bool = True
    username: str = "admin"
    password: str = "admin123"
    secret_key: str = "llm-gateway-default-secret"  # Session encryption
    session_https_only: bool = False  # Secure cookie flag

class DatabaseConfig:
    path: str = "data/gateway.db"

class QueueConfig:
    max_length: int = 5               # Max waiting requests
    concurrency: int = 1              # Parallel processing count
    timeout: int = 300                # Queue wait timeout (seconds), 0=unlimited

class PriorityConfig:
    strategy: str = "api_key"         # Priority strategy name
    default_priority: int = 100       # Default priority for anonymous/unauthenticated

class BackendConfig:
    name: str = ""
    base_url: str = ""                # Backend API base URL
    api_key: str = ""                 # Backend authentication key
    timeout: int = 300                # Backend request timeout
    protocols: list[str] = ["openai"] # ["openai"] or ["anthropic"] or both
    enabled: bool = True

class LoggingConfig:
    level: str = "INFO"               # Log level
    format: str = "json"              # "json" | "text"

class DebugConfig:
    enabled: bool = False
    dir: str = "data/debug"           # Debug dump directory

class MetricsConfig:
    enabled: bool = True

class ProxyConfig:
    enabled: bool = False
    protocol: str = "http"            # "http" | "https" | "socks5"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    def to_url() -> str:              # Builds proxy URL string
        ...

class LogRetentionConfig:
    retention_days: int = 90          # Delete logs older than N days
    max_records: int = 100000         # Trim to max N records

class CorsConfig:
    origins: list[str] = ["*"]        # Allowed CORS origins
```

### Default config.yaml

```yaml
server:
  host: "0.0.0.0"
  port: 8001

auth:
  enabled: true

admin:
  enabled: true
  username: "admin"
  password: "admin123"
  secret_key: "llm-gateway-default-secret"
  session_https_only: false

database:
  path: "data/gateway.db"

queue:
  max_length: 5
  concurrency: 1
  timeout: 300

logging:
  level: "INFO"
  format: "json"

log_retention:
  retention_days: 90
  max_records: 100000

cors:
  origins:
    - "*"

proxy:
  enabled: false
  protocol: "http"
  host: ""
  port: 0
  username: ""
  password: ""
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LLM_GATEWAY_CONFIG` | Path to custom config file (default: `config.yaml`) |

---

## Authentication & Authorization

### API Key Authentication (Proxy Requests)

When `auth.enabled = true`, all proxy requests (`/v1/chat/completions`, `/v1/messages`, `/v1/models`) require a valid API key sent as a Bearer token.

**Flow:**
1. Client sends `Authorization: Bearer <api-key>` header
2. Gateway looks up the key in the `api_keys` SQLite table
3. If key not found → `401 Unauthorized`
4. If key is disabled → `403 Forbidden`
5. If key is valid → returns the key's `name` field as the username

When `auth.enabled = false`, all requests are treated as `"anonymous"` with the default priority.

### Admin Session Authentication

Admin pages and API use Session/Cookie-based authentication:

1. `POST /admin/login` — validates credentials against `admin.username`/`admin.password`
2. On success, sets `session["admin"] = True` and `session["username"] = <username>`
3. Session middleware uses `secret_key` for encryption, `max_age=86400` (24 hours)
4. `https_only` flag controls the `Secure` cookie attribute
5. `GET /admin/logout` clears the session

---

## Request Processing Flow

```
Client Request
    │
    ▼
┌─────────────────────┐
│ 1. Auth Check       │  authenticate_request() — validates Bearer token
│                     │  Returns username or raises 401/403
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 2. Rate Limit       │  In-memory sliding window (60s)
│    Check            │  Returns 429 if exceeded
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 3. Token Quota      │  SQL SUM query on today's/month's usage
│    Check            │  Returns 429 if exceeded
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 4. Priority         │  Strategy computes priority from API key
│    Computation      │  Lower number = higher priority
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 5. Enqueue          │  PriorityQueue.enqueue()
│                     │  Returns 429 if queue full
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 6. Wait for Turn    │  asyncio.Condition.wait() with timeout
│                     │  Returns 408 if timeout exceeds
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 7. Select Backend   │  Round-robin across enabled backends
│    (Round-Robin)    │  matching the target protocol
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 8. Forward Request  │  Adapter.stream() or adapter.call()
│    via Adapter      │  With optional proxy tunneling
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 9. Record & Log     │  - Prometheus metrics update
│                     │  - Structured log entry
│                     │  - SQLite request_log insert
│                     │  - Debug file dump (if enabled)
│                     │  - Signal queue: done → next request
└─────────────────────┘
```

---

## Core Components

### Priority Queue

**File:** `app/core/queue.py`

The queue uses Python's `heapq` for priority ordering and `asyncio.Condition` for cooperative waiting.

**Key properties:**
- `max_size` — Max waiting requests (returns 429 when full)
- `max_concurrency` — How many requests can be processed simultaneously
- `waiting_count` — Current number of waiting requests
- `processing_count` — Current number of requests being processed
- `is_full` / `is_processing` — Convenience booleans

**Heap entry format:** `(priority, timestamp, tiebreaker_id, context)`
- Lower `priority` = higher priority (popped first)
- `timestamp` ensures FIFO ordering within the same priority
- `tiebreaker_id` guarantees uniqueness

**State tracking:** A `set[str]` tracks `request_id`s currently being processed. A request acquires its turn when it reaches the heap front AND processing count is below `max_concurrency`.

**Timeout:** `asyncio.wait_for()` wraps the condition wait. When timeout fires, `asyncio.TimeoutError` is caught and the request leaves without being processed (returns 408 HTTP).

### Rate Limiter

**File:** `app/core/rate_limiter.py`

In-memory sliding window implementation:

- Per-key sliding window of 60 seconds
- Timestamps are stored in a `defaultdict(list)` keyed by username
- Expired entries are lazily purged on each check
- Periodic full cleanup every 300 seconds
- `limit_per_minute = 0` means unlimited
- Singleton accessed via `get_rate_limiter()`

### Token Quota Checker

**File:** `app/core/quota_checker.py`

SQL-based quota checking:

- Daily check: `SUM(prompt_tokens + completion_tokens)` since midnight UTC
- Monthly check: `SUM(prompt_tokens + completion_tokens)` since 1st of month UTC
- Both checks use `COALESCE` to handle NULL values
- `quota = 0` means unlimited
- Returns descriptive error message when quota exceeded

### Adapters

**File:** `app/adapters/base.py` (abstract base), `app/adapters/openai.py`, `app/adapters/anthropic.py`

**BaseAdapter** provides:
- `config`: BackendConfig instance
- `proxy_url`: Proxy URL string built from ProxyConfig

**OpenAIAdapter** (`PATH = "/chat/completions"`):
- Headers: `Authorization: Bearer <api_key>`
- Token extraction: From response `usage` object (non-streaming) or from `data: {...usage...}` chunks (streaming)
- Error handling: `TimeoutException` → 504, `ConnectError/OSError` → 502

**AnthropicAdapter** (`PATH = "/messages"`):
- Headers: `x-api-key: <api_key>`, `anthropic-version: 2023-06-01`
- Token extraction: From `message_start` event (input_tokens → prompt_tokens) and `message_delta` event (output_tokens → completion_tokens)
- Error handling: Same as OpenAIAdapter

Both adapters create a new `httpx.AsyncClient` per request with `trust_env=False` to avoid interference from system proxy settings.

### Priority Strategies

**File:** `app/strategies/base.py`, `app/strategies/api_key_based.py`, `app/strategies/factory.py`

**Strategy interface:** `async get_priority(request, user_name) → int`

**ApiKeyPriorityStrategy:**
1. If auth disabled or user is anonymous → returns `default_priority` (100)
2. Extracts API key from `Authorization` header
3. Looks up key's priority in SQLite
4. Returns configured priority or falls back to `default_priority`

**Factory:**
- `strategy_name = "api_key"` → `ApiKeyPriorityStrategy()`
- Unknown strategy name → `ValueError`

---

## Proxy Support

The gateway supports routing backend LLM requests through HTTP, HTTPS, or SOCKS5 proxy servers.

**Configuration:**
- Global setting applied to all backend requests
- Configurable via `config.yaml` (proxy section) or admin Management page (System tab)
- When changed via admin API, changes take effect immediately (adapters are stateless, recreated per request)

**Proxy URL format:**
```
http://user:pass@host:port     # HTTP/HTTPS with auth
socks5://user:pass@host:port   # SOCKS5 with auth
```

**Note:** Proxy is only used for outbound requests to LLM backends. It does not apply to admin dashboard pages or the API server itself.

**Dependencies:** Uses `httpx` native proxy support for HTTP/HTTPS, and `httpx-socks` (which wraps `socksio`) for SOCKS5.

---

## Debug Mode

When `debug.enabled = true`, full request and response payloads are saved to disk.

**File naming convention:**
```
data/debug/{YYYYMMDDHHMMSSmmm}_{request_id[:12]}_{model}_request.json
data/debug/{YYYYMMDDHHMMSSmmm}_{request_id[:12]}_{model}_response.json
```

**Behavior:**
- Request body is saved immediately upon reception
- Non-streaming response body is saved after completion
- Streaming response chunks are buffered in memory and flushed when the stream completes
- Files are indented JSON for readability
- Errors during debug save are logged but do not affect request processing

---

## Logging & Metrics

### Structured Logging

Uses `structlog` with configurable output format:

- **JSON format** (`logging.format: "json"`): Machine-parseable JSON lines, suitable for log aggregation systems
- **Text format** (`logging.format: "text"`): Human-readable colored console output

**Log fields (request lifecycle):**
- `request_id`, `user`, `endpoint`, `model`, `priority`
- `wait_time_ms`, `processing_time_ms`
- `status_code`, `streamed`, `prompt_tokens`, `completion_tokens`
- `error`, `client_ip`

**Log cleanup (startup):**
- Deletes logs older than `log_retention.retention_days`
- Trims to `log_retention.max_records` if exceeded
- Both checks run automatically on application startup

### Prometheus Metrics

Available at `GET /metrics` when `metrics.enabled = true` (default).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `gateway_requests_total` | Counter | `endpoint`, `status_code`, `user` | Total proxy requests |
| `gateway_queue_length` | Gauge | — | Current queue waiting count |
| `gateway_requests_processing` | Gauge | — | Currently processing (0 or 1) |
| `gateway_request_duration_seconds` | Histogram | `endpoint` | End-to-end duration (buckets: 0.1–120s) |
| `gateway_wait_time_seconds` | Histogram | `endpoint` | Queue wait time (buckets: 0.01–30s) |

---

## SQLite Database Schema

### `api_keys` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `key` | TEXT UNIQUE | API key value (`sk-` + 64 hex chars) |
| `name` | TEXT | Human-readable name |
| `priority` | INTEGER | Priority (default 100, lower = higher) |
| `enabled` | INTEGER | 0 = disabled, 1 = enabled |
| `created_at` | TIMESTAMP | Creation time |
| `updated_at` | TIMESTAMP | Last update time |
| `rate_limit` | INTEGER | Requests/minute limit (0 = unlimited) |
| `token_quota_daily` | INTEGER | Daily token limit (0 = unlimited) |
| `token_quota_monthly` | INTEGER | Monthly token limit (0 = unlimited) |

### `request_logs` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `request_id` | TEXT UNIQUE | UUID hex identifier |
| `user_name` | TEXT | API key owner name |
| `endpoint` | TEXT | Request endpoint path |
| `model` | TEXT | Model name |
| `priority` | INTEGER | Request priority |
| `enqueue_time` | TIMESTAMP | Queue entry time |
| `dequeue_time` | TIMESTAMP | Queue exit time |
| `complete_time` | TIMESTAMP | Processing completion time |
| `wait_time_ms` | INTEGER | Queue wait time in ms |
| `processing_time_ms` | INTEGER | Processing time in ms |
| `status_code` | INTEGER | HTTP response status code |
| `streamed` | INTEGER | 1 if streaming, 0 otherwise |
| `prompt_tokens` | INTEGER | Input token count |
| `completion_tokens` | INTEGER | Output token count |
| `error` | TEXT | Error message (if any) |
| `client_ip` | TEXT | Client IP address |
| `created_at` | TIMESTAMP | Log entry creation time |

**Indexes:** `idx_logs_created_at`, `idx_logs_user`, `idx_logs_endpoint`, `idx_keys_key`
