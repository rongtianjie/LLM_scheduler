> [**中文版本**](./README.zh.md) | [**English Version**](./README.md)

# LLM Gateway Proxy

A production-grade LLM API gateway proxy with priority queueing, concurrency control, API Key authentication, and an embedded admin dashboard.

## Features

- **Dual Protocol Support**: Compatible with both OpenAI and Anthropic API formats, with independently configurable backends
- **Load Balancing**: Round-robin across multiple backends supporting the same protocol; individual backends can be enabled/disabled independently
- **Priority Queue**: Requests are prioritized by API Key; high-priority requests jump the queue
- **Concurrency Control**: Configurable concurrency limit; returns 429 when queue is full
- **Queue Timeout**: Configurable wait timeout; returns 408 on timeout
- **Rate Limiting**: API Key-level request rate limiting (requests/minute); returns 429 when exceeded
- **Token Quota**: API Key-level daily/monthly token usage quotas; rejects requests when exceeded
- **Log Retention**: Automatic log cleanup with configurable retention days and maximum record count
- **Streaming Passthrough**: SSE event streams forwarded transparently without modification
- **Token Statistics**: Automatic recording of input/output token counts for both streaming and non-streaming requests
- **Dashboard Charts**: Chart.js time-series charts with 1h/6h/24h/7d/30d period switching
- **Debug Mode**: Saves full request/response bodies to disk for troubleshooting
- **API Key Authentication**: Independent API keys with configurable enable/disable
- **Admin Login**: Custom login page with Session/Cookie-based authentication
- **Proxy Support**: Forwards backend requests through HTTP/HTTPS/SOCKS5 proxy servers
- **CORS Support**: Configurable cross-origin request sources
- **Structured Logging**: JSON format with full request lifecycle records (including token usage)
- **Prometheus Metrics**: Queue length, request latency, processing time, etc.
- **Embedded Admin Panel**: Sci-fi themed UI for managing API Keys, viewing logs, statistics, and dashboard
- **Docker Deployment**: One-command startup with persistent data storage

## Quick Start

### Local Run

```bash
# 1. Create virtual environment and install dependencies
uv sync

# 2. Edit configuration (config.local.yaml will automatically override config.yaml)
cp config.yaml config.local.yaml
# Modify openai_backend.base_url, anthropic_backend.base_url, etc. (or configure via admin panel after startup)
# Unset values will use code defaults

# 3. Start (automatically uses virtual environment)
uv run python -m app.main
```

### Docker Compose

```bash
# 1. Edit config file
vim config.yaml  # Modify startup config (server, admin credentials, etc.)

# 2. Start
docker-compose up -d

# 3. View logs
docker-compose logs -f
```

### Bare Docker

```bash
# Build
docker build -t llm-gateway-proxy .

# Run
docker run -d \
  --name llm-gateway \
  -p 8001:8001 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v gateway-data:/app/data \
  llm-gateway-proxy
```

## Configuration

`config.yaml` contains only startup-level settings (server, auth, admin, database, queue, logging, log_retention, cors, proxy). Runtime configuration (queue, backend, debug, metrics, proxy) can also be managed through the admin panel. For default values, see `app/config.py`.

```yaml
server:
  host: "0.0.0.0"
  port: 8001

auth:
  enabled: true                  # API Key authentication toggle

admin:
  enabled: true
  username: "admin"
  password: "admin123"
  secret_key: "llm-gateway-default-secret"  # Session encryption key
  session_https_only: false      # Set to true in production

database:
  path: "data/gateway.db"

queue:
  max_length: 5
  concurrency: 1
  timeout: 300                   # Queue wait timeout (seconds), 0 = unlimited

logging:
  level: "INFO"
  format: "json"                 # "json" | "text"

log_retention:
  retention_days: 90             # Log retention days
  max_records: 100000            # Maximum log records

cors:
  origins:
    - "*"                        # Allowed CORS origins

proxy:
  enabled: false                 # Proxy toggle
  protocol: "http"               # "http" | "https" | "socks5"
  host: ""
  port: 0
  username: ""                   # Proxy authentication (optional)
  password: ""
```

## API Usage

### Proxy Requests

```bash
# OpenAI format
curl http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'

# Anthropic format
curl http://localhost:8001/v1/messages \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'

# Check queue status (no auth required)
curl http://localhost:8001/v1/queue
```

### Admin API

```bash
# Login to get session cookie
curl -c cookies.txt -X POST http://localhost:8001/admin/login \
  -d "username=admin&password=admin123"

# Get queue status
curl -b cookies.txt http://localhost:8001/admin/api/queue

# Create API Key (with rate limit and token quota support)
curl -b cookies.txt -X POST http://localhost:8001/admin/api/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "alice", "priority": 50, "rate_limit": 30, "token_quota_daily": 100000}'

# Query logs
curl -b cookies.txt "http://localhost:8001/admin/api/logs?page=1&per_page=20"

# Get statistics (supported time ranges: 1h, 6h, 24h, 7d, 30d, all)
curl -b cookies.txt "http://localhost:8001/admin/api/stats?period=24h"

# Get time-series data (for charts)
curl -b cookies.txt "http://localhost:8001/admin/api/stats/timeseries?period=24h"

# Update proxy config
curl -b cookies.txt -X PUT http://localhost:8001/admin/api/config \
  -H "Content-Type: application/json" \
  -d '{"proxy": {"enabled": true, "protocol": "socks5", "host": "127.0.0.1", "port": 1080}}'
```

## Admin Panel

Access `http://localhost:8001/admin` in your browser and log in with the configured admin credentials.

- **Login Page**: Custom login form with blue-purple gradient sci-fi design, based on Session/Cookie authentication (24-hour expiry)
- **Dashboard**: Real-time queue status, request statistics, Chart.js time-series charts (Requests/Tokens), time range selection (1h/6h/24h/7d/30d), request count and token usage by API Key
- **API Keys**: Create/edit/delete API Keys, full Key displayed on creation with one-click copy
- **Logs**: Request history with token usage column and status code color coding, filterable by user and endpoint with pagination
- **Management**: Runtime configuration with three tabs (Scheduling / Backend / System)
  - **Scheduling**: Queue config (Max Length, Concurrency) + priority strategy
  - **Backend**: Unified backend list with add/edit/delete, protocol selection (OpenAI/Anthropic), and enabled/disabled toggle
  - **System**: Debug mode, Prometheus Metrics, proxy server (HTTP/HTTPS/SOCKS5) configuration

## Queue Behavior

1. All requests are enqueued by priority (lower value = higher priority)
2. Up to `queue.concurrency` requests are processed simultaneously (default: 1, configurable for multi-concurrency)
3. High-priority requests are inserted at the queue head without interrupting the currently processing request
4. Returns HTTP 429 when the queue is full
5. Subsequent requests wait while a streaming request is in progress
6. Returns HTTP 408 on queue wait timeout (configurable via `queue.timeout`; 0 = unlimited)

## Rate Limiting & Quotas

- **Rate Limiting**: Controlled by the `rate_limit` field on API Key (requests/minute); returns 429 when exceeded
- **Token Quotas**: Set daily/monthly token limits via `token_quota_daily` / `token_quota_monthly`
- Quota checking is based on recorded token usage in SQLite (prompt_tokens + completion_tokens)

## Testing

```bash
uv run pytest tests/
```

## Development

Project structure:

```
app/
├── main.py              # Entry point, app factory, CORS/SessionMiddleware
├── config.py            # Config loading (including Proxy/LogRetention/CorsConfig)
├── database.py          # SQLite management (WAL mode, indexes, log cleanup)
├── models.py            # Data models
├── api/
│   ├── proxy.py         # Proxy endpoints (rate limiting, quota check, timeout)
│   ├── admin_api.py     # Admin API (including time-series)
│   └── admin_pages.py   # Admin pages (including login/logout)
├── core/
│   ├── queue.py         # Priority queue (with timeout support)
│   ├── auth.py          # Authentication (Session + API Key)
│   ├── metrics.py       # Metrics
│   ├── rate_limiter.py  # In-memory sliding window rate limiter
│   └── quota_checker.py # Token usage quota checker
├── adapters/
│   ├── base.py          # Adapter base class (with proxy support)
│   ├── openai.py        # OpenAI adapter
│   └── anthropic.py     # Anthropic adapter
├── strategies/
│   ├── base.py          # Strategy abstraction
│   └── api_key_based.py # API Key priority strategy
├── templates/           # Jinja2 page templates
└── static/
    ├── style.css        # Sci-fi theme styles
    └── chart.umd.min.js # Chart.js (locally deployed)
```

