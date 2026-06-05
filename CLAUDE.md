# LLM Gateway Proxy

## Overview
An LLM API gateway proxy with priority queuing, concurrency control, API key authentication, structured logging, Prometheus metrics, and an embedded admin dashboard.

## Architecture
- **FastAPI** single-process application on port 8001
- **Priority queue** with concurrency=1 (`asyncio.Condition`-based)
- **Single backend** supporting both OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`) formats
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
| `app/strategies/*.py` | Priority strategy (api_key, ip_based) |
| `app/api/proxy.py` | `/v1/chat/completions`, `/v1/messages` |
| `app/api/admin_api.py` | Admin REST API |
| `app/api/admin_pages.py` | Admin page routes |
| `config.yaml` | Default configuration |

## Running

```bash
# Install
pip install -r requirements.txt

# Configure
cp config.yaml config.local.yaml  # edit as needed

# Run (dev)
python -m app.main

# Run (production with Docker)
docker-compose up -d
```

## Testing

```bash
pip install pytest pytest-asyncio
python -m pytest tests/
```

## Configuration (config.yaml)

See `config.yaml` for all options. Key settings:
- `server.port` — listen port (default: 8001)
- `auth.enabled` — require API Key Bearer auth
- `queue.max_length` — max queue depth before 429
- `priority.strategy` — `"api_key"` or `"ip_based"`
- `backend.base_url` — single backend endpoint

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
| `GET /admin/api/queue` | Queue status (JSON) |
| `GET /admin/api/keys` | List API keys |
| `POST /admin/api/keys` | Create API key |
| `PUT /admin/api/keys/{id}` | Update API key |
| `DELETE /admin/api/keys/{id}` | Delete API key |
| `GET /admin/api/stats` | Dashboard stats (supports `?period=24h&key_id=1`) |
| `GET /admin/api/logs` | Query logs (paginated, includes token columns) |

## Request Flow

1. Client sends request → auth check → priority computation
2. Request enqueued (429 if queue full)
3. Waits via `asyncio.Condition` until at front + no active processing
4. Adapter forwards to backend (streaming or non-streaming)
5. On completion: signal next waiting request, log, record metrics

## Priority

- Lower number = higher priority
- `api_key` strategy: priority from API key record in SQLite
- `ip_based` strategy: priority from `priority.ip_mapping` (supports CIDR)
- Default priority: 100

## Stream Passthrough

SSE events are forwarded byte-for-byte without parsing or modification. OpenAI's `data: [DONE]` and Anthropic's `event: message_stop` pass through unaltered.
