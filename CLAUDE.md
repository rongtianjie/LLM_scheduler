# LLM Gateway Proxy

## Overview
An LLM API gateway proxy with priority queuing, concurrency control, API key authentication, structured logging, Prometheus metrics, and an embedded admin dashboard.

## Interaction Rules

在执行任务前，必须先问我问题。要求：
- 一次只问一个问题
- 根据我的回答，继续追问
- 直到你有 95% 的信心理解我的真实需求和目标，然后才给出方案

## Architecture
- **FastAPI** single-process application on port 8001
- **Priority queue** with configurable concurrency (`asyncio.Condition`-based)
- **Separate backend configs** for OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`)
- **SQLite** for API key storage and request logging
- **Jinja2** admin dashboard (`, /admin/api-keys`, `/admin/logs`)
- **Prometheus** metrics at `/metrics`
- **structlog** for structured JSON logging

## Key Files

| Path | Purpose |
|------|---------|
| `app/main.py` | App factory, startup/shutdown, route mounting |
| `app/config.py` | YAML config loading via Pydantic |
| `app/database.py` | SQLite init + connection management |
| `app/core/queue.py` | Priority queue with asyncio.Condition |
| `app/core/auth.py` | API Key Bearer auth + Admin Basic auth |
| `app/core/metrics.py` | Prometheus metric definitions |
| `app/adapters/openai.py` | OpenAI-format adapter |
| `app/adapters/anthropic.py` | Anthropic-format adapter |
| `app/strategies/*.py` | Priority strategy (api_key) |
| `app/api/proxy.py` | `/v1/chat/completions`, `/v1/messages` |
| `app/api/admin_api.py` | Admin REST API |
| `app/api/admin_pages.py` | Admin page routes |
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

`config.yaml` contains bootstrap-only settings (server, auth, admin, database, logging).
Runtime settings (queue, backends, debug, metrics) are managed through the admin page
at `/admin/management`. Default values are in `app/config.py`.

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
| `GET /admin` | Dashboard |
| `GET /admin/api-keys` | API key management page |
| `GET /admin/logs` | Request logs page |
| **`GET /admin/management`** | **Runtime configuration management page** |
| `GET /admin/api/queue` | Queue status (JSON) |
| `GET /admin/api/keys` | List API keys |
| `POST /admin/api/keys` | Create API key |
| `PUT /admin/api/keys/{id}` | Update API key |
| `DELETE /admin/api/keys/{id}` | Delete API key |
| `GET /admin/api/stats` | Dashboard stats (supports `?period=24h&key_id=1`) |
| `GET /admin/api/logs` | Query logs (paginated, includes token columns) |
| `GET /admin/api/config` | Get runtime config (queue, openai_backend, anthropic_backend, debug, metrics) |
| `PUT /admin/api/config` | Update runtime config (applies immediately) |
| `POST /admin/api/config/sync-openai-to-anthropic` | Copy OpenAI backend config to Anthropic |

## Request Flow

1. Client sends request → auth check → priority computation
2. Request enqueued (429 if queue full)
3. Waits via `asyncio.Condition` until at front + no active processing
4. Adapter forwards to backend (streaming or non-streaming)
5. On completion: signal next waiting request, log, record metrics

## Priority

- Lower number = higher priority
- `api_key` strategy: priority from API key record in SQLite
- Default priority: 100

## Stream Passthrough

SSE events are forwarded byte-for-byte without parsing or modification. OpenAI's `data: [DONE]` and Anthropic's `event: message_stop` pass through unaltered.
