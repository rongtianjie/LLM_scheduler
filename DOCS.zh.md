# LLM Scheduler — 详细文档

> [**English Documentation**](./DOCS.md) | [**中文文档**](./DOCS.zh.md)
>
> 快速概览请参见 [README.md](./README.md)。

---

## 目录

1. [架构概述](#架构概述)
2. [技术栈](#技术栈)
3. [项目结构](#项目结构)
4. [API 参考](#api-参考)
   - [代理端点](#代理端点)
   - [管理页面端点](#管理页面端点)
   - [管理 REST API](#管理-rest-api)
5. [配置参考](#配置参考)
6. [认证与授权](#认证与授权)
7. [请求处理流程](#请求处理流程)
8. [核心组件](#核心组件)
   - [优先级队列](#优先级队列)
   - [速率限制器](#速率限制器)
   - [Token 配额检查器](#token-配额检查器)
   - [适配器](#适配器)
   - [优先级策略](#优先级策略)
9. [代理支持](#代理支持)
10. [调试模式](#调试模式)
11. [日志与指标](#日志与指标)

---

## 架构概述

```
┌──────────────┐     ┌─────────────────────────────────────────────────────┐
│   客户端      │────▶│              LLM Scheduler (FastAPI)            │
│  (curl/SDK)  │     │                                                     │
└──────────────┘     │  ┌──────────┐  ┌───────────┐  ┌──────────────────┐  │
                     │  │   认证    │─▶│  速率限制  │─▶│  Token 配额检查  │  │
                     │  │  (API    │  │  (内存)   │  │  (基于SQL)       │  │
                     │  │   Key)   │  └───────────┘  └──────────────────┘  │
                     │  └──────────┘                          │            │
                     │  ┌─────────────────────────────────────▼──────────┐ │
                     │  │          优先级队列 (asyncio.Condition)        │ │
                     │  │   ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐    │ │
                     │  │   │ P1  │ │ P2  │ │ P3  │ │ P4  │ │ P5  │...  │ │
                     │  │   └─────┘ └─────┘ └─────┘ └─────┘ └─────┘    │ │
                     │  └──────────────────────┬──────────────────────────┘ │
                     │                         │                          │
                     │  ┌──────────────────────▼──────────────────────────┐ │
                     │  │     适配器 (轮询负载均衡)                        │ │
                     │  │  ┌────────────┐  ┌────────────┐  ┌───────────┐ │ │
                     │  │  │ OpenAI     │  │ Anthropic  │  │  (更多)   │ │ │
                     │  │  │ 适配器     │  │ 适配器     │  │           │ │ │
                     │  │  └─────┬──────┘  └──────┬─────┘  └───────────┘ │ │
                     │  └────────┼─────────────────┼──────────────────────┘ │
                     │           │                 │                        │
                     │           ▼                 ▼                        │
                     │   ┌──────────────┐  ┌──────────────┐                 │
                     │   │ OpenAI API   │  │ Anthropic API│                 │
                     │   │  后端(多实例) │  │  后端(多实例) │                 │
                     │   └──────────────┘  └──────────────┘                 │
                     │                                                     │
                     │  ┌──────────────────────────────────────────────┐   │
                     │  │  代理服务器 (HTTP/HTTPS/SOCKS5) — 可选隧道     │   │
                     │  └──────────────────────────────────────────────┘   │
                     └─────────────────────────────────────────────────────┘
```

网关是一个**单进程 FastAPI 应用**，作为 LLM API 请求的反向代理。所有传入请求依次经过：认证 → 速率限制 → 配额检查 → 优先级队列 → 适配器转发。

## 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| Web 框架 | **FastAPI** (Python 3.11+) | 异步 HTTP 服务器，支持自动 OpenAPI |
| ASGI 服务器 | **Uvicorn** | 生产级 ASGI 服务器 |
| 数据库 | **SQLite** (via **aiosqlite**) | API Key 和请求日志持久化存储 (WAL 模式) |
| HTTP 客户端 | **httpx** | 异步 HTTP 请求后端 |
| 配置 | **Pydantic** + **PyYAML** | Schema 验证的 YAML 配置 |
| 管理认证 | **Starlette SessionMiddleware** | 基于 Session/Cookie 的管理员认证 (24h 过期) |
| 代理认证 | Bearer token (API Key) | 基于 SQLite 的自定义 API Key 认证 |
| 队列 | **asyncio.Condition** | 带超时的优先级队列 |
| 日志 | **structlog** | 结构化 JSON 日志 |
| 指标 | **prometheus_client** | Prometheus 指标 (位于 `/metrics`) |
| 模板 | **Jinja2** | 管理面板服务端渲染 |
| 图表 | **Chart.js v4** (本地部署) | Dashboard 时间序列图表 |
| 代理 | **httpx-socks** | 通过 `socksio` 支持 SOCKS5 代理 |

## 项目结构

```
app/
├── main.py                  # 应用工厂、生命周期管理、CORS/SessionMiddleware、路由挂载
├── config.py                # 基于 Pydantic 模型的 YAML 配置加载
├── database.py              # SQLite 初始化 (WAL 模式、迁移、日志清理)
├── models.py                # Pydantic 数据模型 + dataclass 请求上下文
│
├── api/
│   ├── proxy.py             # 代理端点 (/v1/chat/completions, /v1/messages, /v1/models, /v1/queue)
│   ├── admin_api.py         # 管理 REST API (keys, stats, logs, config)
│   └── admin_pages.py       # 管理页面路由 (login, logout, dashboard 等)
│
├── core/
│   ├── queue.py             # 异步优先级队列 (heapq + asyncio.Condition)
│   ├── auth.py              # API Key 认证 + 管理员会话管理
│   ├── metrics.py           # Prometheus 指标定义
│   ├── rate_limiter.py      # 内存滑动窗口速率限制器
│   └── quota_checker.py     # 基于 SQL 的日/月 Token 配额检查器
│
├── adapters/
│   ├── base.py              # 抽象适配器基类 (含代理支持)
│   ├── openai.py            # OpenAI 格式适配器 (/chat/completions)
│   └── anthropic.py         # Anthropic 格式适配器 (/messages)
│
├── strategies/
│   ├── base.py              # 优先级策略抽象
│   ├── api_key_based.py     # 基于 API Key 的优先级策略
│   └── factory.py           # 策略工厂
│
├── templates/               # Jinja2 HTML 模板
│   ├── base.html            # 布局 (侧边栏、导航、401 拦截器)
│   ├── login.html           # 登录页面
│   ├── dashboard.html       # Dashboard (统计、图表、按 Key 明细)
│   ├── api_keys.html        # API Key 管理页面
│   ├── logs.html            # 请求日志页面
│   └── management.html      # 运行时配置 (Scheduling/Backend/System 三个 Tab)
│
├── static/
│   ├── style.css            # 科技感主题样式
│   └── chart.umd.min.js     # Chart.js v4 (本地部署)
│
data/                        # 运行时数据 (自动创建)
├── gateway.db               # SQLite 数据库
└── debug/                   # 调试模式的请求/响应内容保存
```

---

## API 参考

### 代理端点

#### `POST /v1/chat/completions` — OpenAI 兼容代理

将请求转发到 OpenAI 兼容后端。支持流式和非流式两种模式。

**认证方式：** 需要（除非 `auth.enabled = false`）

**请求头：**
- `Authorization: Bearer <api-key>` — API Key 认证
- `Content-Type: application/json`

**请求体：** 与 [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat) 一致。`stream` 字段决定流式或非流式。

**示例：**
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

**响应 (非流式)：** JSON — 与 OpenAI API 响应格式一致，包含 `usage` 对象记录 token 用量。

**响应 (流式)：** `text/event-stream` — SSE 事件原样透传。Token 用量从最终 `data: [DONE]` 之前的数据块中的 usage 信息提取。

**状态码：**
- `200 OK` — 成功
- `401 Unauthorized` — 缺少或无效的 API Key
- `403 Forbidden` — API Key 已禁用
- `429 Too Many Requests` — 队列满 / 速率超限 / 配额超限
- `408 Request Timeout` — 队列等待超时
- `502 Bad Gateway` — 未配置后端 / 后端不可达
- `504 Gateway Timeout` — 后端请求超时

---

#### `POST /v1/messages` — Anthropic 兼容代理

将请求转发到 Anthropic 兼容后端。支持流式和非流式两种模式。

**认证方式：** 需要（除非 `auth.enabled = false`）

**请求头：**
- `Authorization: Bearer <api-key>` — API Key 认证
- `Content-Type: application/json`

**请求体：** 与 [Anthropic Messages API](https://docs.anthropic.com/en/api/messages) 一致。`stream` 字段决定流式或非流式。

**示例：**
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

**响应 (非流式)：** JSON — 与 Anthropic API 响应格式一致，包含 `usage` 中的 `input_tokens` 和 `output_tokens`。

**响应 (流式)：** `text/event-stream` — SSE 事件原样透传。Token 用量从 `message_start` 事件（input_tokens）和 `message_delta` 事件（output_tokens）提取。

**状态码：** 同 `/v1/chat/completions`。

---

#### `GET /v1/queue` — 队列状态（公开）

返回当前队列状态。**无需认证。**

**示例：**
```bash
curl http://localhost:8001/v1/queue
```

**响应：**
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

**状态码：**
- `200 OK` — 成功返回队列状态

---

#### `GET /v1/models` — 查看模型列表

将请求转发到第一个可用的 OpenAI 后端的 `/models` 端点。**需要认证。**

**示例：**
```bash
curl http://localhost:8001/v1/models \
  -H "Authorization: Bearer sk-your-api-key"
```

**响应：** JSON — 从后端转发。

**状态码：**
- `200 OK` — 成功
- `401 Unauthorized` — 缺少或无效的 API Key
- `502 Bad Gateway` — 未配置 OpenAI 后端 / 后端不可达
- `504 Gateway Timeout` — 后端超时

---

#### `GET /health` — 健康检查

简单的健康检查端点。**无需认证。**

**示例：**
```bash
curl http://localhost:8001/health
```

**响应：**
```json
{
    "status": "ok"
}
```

**状态码：**
- `200 OK`

---

#### `GET /metrics` — Prometheus 指标

返回 Prometheus 格式的指标数据。仅在 `metrics.enabled = true` 时可用。**无需认证。**

**暴露的指标：**
- `gateway_requests_total` (Counter) — 按 endpoint、status_code、user 统计的总请求数
- `gateway_queue_length` (Gauge) — 当前队列等待数
- `gateway_requests_processing` (Gauge) — 当前处理状态 (0 或 1)
- `gateway_request_duration_seconds` (Histogram) — 端到端请求耗时（秒）
- `gateway_wait_time_seconds` (Histogram) — 队列等待耗时（秒）

**示例：**
```bash
curl http://localhost:8001/metrics
```

**状态码：**
- `200 OK` — Prometheus 文本格式

---

#### `GET /` — 根路径重定向

重定向到管理面板 `/admin`。

**状态码：**
- `302 Found` — 重定向到 `/admin`

---

### 管理页面端点

所有管理页面路由需要基于 Session 的认证。未认证请求会重定向到 `/admin/login`。

| 方法 | 路径 | 描述 |
|------|------|------|
| `GET` | `/admin/login` | 登录页面（已登录则跳转到 `/admin`） |
| `POST` | `/admin/login` | 登录表单提交 |
| `GET` | `/admin/logout` | 退出登录（清除 session，重定向到 login） |
| `GET` | `/admin` | Dashboard（实时队列状态、Chart.js 图表、按 Key 明细） |
| `GET` | `/admin/api-keys` | API Key 管理页面（创建/编辑/删除） |
| `GET` | `/admin/logs` | 请求日志页面（分页、筛选） |
| `GET` | `/admin/management` | 运行时配置管理页面（Scheduling/Backend/System 三个 Tab） |

**登录示例：**
```bash
curl -c cookies.txt -X POST http://localhost:8001/admin/login \
  -d "username=admin&password=admin123"
```

**静态文件：**
| 路径 | 内容 |
|------|------|
| `/static/style.css` | 科技感主题样式 |
| `/static/chart.umd.min.js` | Chart.js v4 包 |

---

### 管理 REST API

所有管理 REST API 端点需要基于 Session 的认证（通过 `/admin/login` 获取）。未认证请求返回 `401` JSON。

基础路径：`/admin/api`

#### `GET /admin/api/queue` — 队列状态

**参数：** 无

**响应：** 格式同 `/v1/queue`，但需要管理员认证。

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

**状态码：** `200 OK`、`401 Unauthorized`

---

#### `GET /admin/api/keys` — 列出 API Key

返回所有已配置的 API Key 及其设置。

**响应：**
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

**状态码：** `200 OK`、`401 Unauthorized`

---

#### `POST /admin/api/keys` — 创建 API Key

**请求体：**
```json
{
    "name": "alice",
    "priority": 50,
    "rate_limit": 30,
    "token_quota_daily": 100000,
    "token_quota_monthly": 500000
}
```

**字段说明：**
- `name` (string, 必填) — 可读名称
- `priority` (int, 默认: `100`) — 数值越小优先级越高
- `rate_limit` (int, 默认: `0`) — 每分钟请求限制 (0 = 不限制)
- `token_quota_daily` (int, 默认: `0`) — 每日 Token 上限 (0 = 不限制)
- `token_quota_monthly` (int, 默认: `0`) — 每月 Token 上限 (0 = 不限制)

**响应 (201 Created)：**
```json
{
    "id": 2,
    "key": "sk-...",          // 完整 Key — 仅创建时显示一次！
    "name": "alice",
    "priority": 50,
    "enabled": true,
    "created_at": "2024-01-01T00:00:00.000Z",
    "rate_limit": 30,
    "token_quota_daily": 100000,
    "token_quota_monthly": 500000
}
```

**状态码：** `201 Created`、`401 Unauthorized`

---

#### `PUT /admin/api/keys/{key_id}` — 更新 API Key

**路径参数：**
- `key_id` (int) — API Key ID

**请求体：** 所有字段可选，仅更新提供的字段。
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

**响应：** 更新后的 `ApiKeyResponse` 对象。

**状态码：** `200 OK`、`401 Unauthorized`、`404 Not Found`

---

#### `DELETE /admin/api/keys/{key_id}` — 删除 API Key

**路径参数：**
- `key_id` (int) — API Key ID

**响应：**
```json
{
    "ok": true
}
```

**状态码：** `200 OK`、`401 Unauthorized`、`404 Not Found`

---

#### `GET /admin/api/stats` — Dashboard 统计

**查询参数：**
- `period` (string, 默认: `"24h"`) — 时间范围。可选值：`1h`、`6h`、`24h`、`7d`、`30d`、`all`
- `key_id` (int, 可选) — 按特定 API Key ID 筛选

**响应：**
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

**状态码：** `200 OK`、`401 Unauthorized`

---

#### `GET /admin/api/stats/timeseries` — 时间序列数据

**查询参数：**
- `period` (string, 默认: `"24h"`) — 时间范围。可选值：`1h`、`6h`、`24h`、`7d`、`30d`、`all`

**桶间隔：**
| 时间范围 | 间隔 |
|----------|------|
| `1h`     | 5 分钟 |
| `6h`     | 30 分钟 |
| `24h`    | 1 小时 |
| `7d`     | 6 小时 |
| `30d`    | 1 天 |
| `all`    | 1 天 |

**响应：**
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

**状态码：** `200 OK`、`401 Unauthorized`

---

#### `GET /admin/api/logs` — 请求日志

**查询参数：**
- `page` (int, 默认: `1`) — 页码 (从 1 开始)
- `per_page` (int, 默认: `50`) — 每页条数
- `endpoint` (string, 可选) — 按端点筛选（如 `/v1/chat/completions`）
- `user` (string, 可选) — 按用户名筛选

**响应：**
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

**状态码：** `200 OK`、`401 Unauthorized`

---

#### `GET /admin/api/config` — 获取运行时配置

返回所有可运行时配置的设置。

**响应：**
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

**状态码：** `200 OK`、`401 Unauthorized`

---

#### `PUT /admin/api/config` — 更新运行时配置

立即在内存中应用配置更改，无需重启服务器。

**请求体：** 所有字段可选，仅更新提供的部分。

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

**响应：**
```json
{
    "ok": true,
    "changes": ["queue.max_length", "queue.concurrency", "proxy.enabled"]
}
```

**说明：**
- 后端配置是完全替换（非合并）——数组中必须包含所有后端。
- 代理更改对所有后续请求立即生效。
- 队列的 `max_length` 和 `concurrency` 会应用到正在运行的队列实例。
- 当 `priority.strategy` 更改时，策略会从工厂重新创建。

**状态码：** `200 OK`、`401 Unauthorized`、`422 Unprocessable Entity`（无效的代理协议）

---

## 配置参考

### 配置文件加载顺序

1. 主配置文件路径（默认：`config.yaml`，可通过 `LLM_GATEWAY_CONFIG` 环境变量覆盖）
2. 自动合并 `config.local.yaml`（如果存在，深度合并，local 值优先）

### 完整配置模型

```python
# app/config.py — Pydantic 模型

class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8001

class AuthConfig:
    enabled: bool = True              # API Key 认证开关

class AdminConfig:
    enabled: bool = True
    username: str = "admin"
    password: str = "admin123"
    secret_key: str = "llm-scheduler-default-secret"  # Session 加密密钥
    session_https_only: bool = False  # Secure cookie 标志

class DatabaseConfig:
    path: str = "data/gateway.db"

class QueueConfig:
    max_length: int = 5               # 最大等待请求数
    concurrency: int = 1              # 并发处理数
    timeout: int = 300                # 队列等待超时（秒），0=无限

class PriorityConfig:
    strategy: str = "api_key"         # 优先级策略名称
    default_priority: int = 100       # 匿名/未认证请求的默认优先级

class BackendConfig:
    name: str = ""
    base_url: str = ""                # 后端 API 基础 URL
    api_key: str = ""                 # 后端认证密钥
    timeout: int = 300                # 后端请求超时
    protocols: list[str] = ["openai"] # ["openai"] 或 ["anthropic"] 或两者
    enabled: bool = True

class LoggingConfig:
    level: str = "INFO"               # 日志级别
    format: str = "json"              # "json" | "text"

class DebugConfig:
    enabled: bool = False
    dir: str = "data/debug"           # 调试数据保存目录

class MetricsConfig:
    enabled: bool = True

class ProxyConfig:
    enabled: bool = False
    protocol: str = "http"            # "http" | "https" | "socks5"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    def to_url() -> str:              # 构建代理 URL 字符串
        ...

class LogRetentionConfig:
    retention_days: int = 90          # 日志保留天数
    max_records: int = 100000         # 最大日志记录数

class CorsConfig:
    origins: list[str] = ["*"]        # 允许的 CORS 来源
```

### 默认 config.yaml

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
  secret_key: "llm-scheduler-default-secret"
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

### 环境变量

| 变量 | 描述 |
|------|------|
| `LLM_GATEWAY_CONFIG` | 自定义配置文件路径（默认：`config.yaml`） |

---

## 认证与授权

### API Key 认证（代理请求）

当 `auth.enabled = true` 时，所有代理请求（`/v1/chat/completions`、`/v1/messages`、`/v1/models`）都需要有效的 API Key，以 Bearer token 形式发送。

**流程：**
1. 客户端发送 `Authorization: Bearer <api-key>` 请求头
2. 网关在 `api_keys` SQLite 表中查找该 key
3. 未找到 key → `401 Unauthorized`
4. Key 已禁用 → `403 Forbidden`
5. Key 有效 → 返回 key 的 `name` 字段作为用户名

当 `auth.enabled = false` 时，所有请求都视为 `"anonymous"`，使用默认优先级。

### 管理员 Session 认证

管理页面和 API 使用基于 Session/Cookie 的认证：

1. `POST /admin/login` — 验证凭据与 `admin.username`/`admin.password` 匹配
2. 成功后，设置 `session["admin"] = True` 和 `session["username"] = <username>`
3. Session 中间件使用 `secret_key` 进行加密，`max_age=86400`（24 小时）
4. `https_only` 标志控制 `Secure` cookie 属性
5. `GET /admin/logout` 清除 session

---

## 请求处理流程

```
客户端请求
    │
    ▼
┌─────────────────────┐
│ 1. 认证检查         │  authenticate_request() — 验证 Bearer token
│                     │  返回用户名或触发 401/403
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 2. 速率限制检查     │  内存滑动窗口 (60s)
│                     │  超限返回 429
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 3. Token 配额检查   │  SQL SUM 查询当日/当月用量
│                     │  超限返回 429
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 4. 优先级计算       │  策略从 API Key 计算优先级
│                     │  数值越小优先级越高
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 5. 入队             │  PriorityQueue.enqueue()
│                     │  队列满返回 429
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 6. 等待轮次         │  asyncio.Condition.wait() 带超时
│                     │  超时返回 408
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 7. 选择后端 (轮询)  │  从启用的后端中轮询选择
│                     │  匹配目标协议
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 8. 通过适配器转发   │  adapter.stream() 或 adapter.call()
│                     │  可选通过代理隧道
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 9. 记录与日志       │  - Prometheus 指标更新
│                     │  - 结构化日志记录
│                     │  - SQLite request_log 写入
│                     │  - 调试文件保存（如果启用）
│                     │  - 信号队列：完成 → 下一个请求
└─────────────────────┘
```

---

## 核心组件

### 优先级队列

**文件：** `app/core/queue.py`

队列使用 Python 的 `heapq` 进行优先级排序，使用 `asyncio.Condition` 实现协作等待。

**关键属性：**
- `max_size` — 最大等待请求数（队列满时返回 429）
- `max_concurrency` — 可同时处理的请求数
- `waiting_count` — 当前等待请求数
- `processing_count` — 当前处理中的请求数
- `is_full` / `is_processing` — 便捷布尔属性

**堆条目格式：** `(priority, timestamp, tiebreaker_id, context)`
- `priority` 数值越小优先级越高（先出队）
- `timestamp` 保证同一优先级内的 FIFO 顺序
- `tiebreaker_id` 保证唯一性

**状态追踪：** 使用 `set[str]` 记录正在处理中的 `request_id`。当请求到达堆顶且处理中的请求数低于 `max_concurrency` 时，获得处理资格。

**超时：** `asyncio.wait_for()` 包装条件等待。超时时捕获 `asyncio.TimeoutError`，请求离开队列不进行处理（返回 HTTP 408）。

### 速率限制器

**文件：** `app/core/rate_limiter.py`

内存滑动窗口实现：

- 每个 Key 的 60 秒滑动窗口
- 时间戳存储在以用户名为 key 的 `defaultdict(list)` 中
- 过期条目在每次检查时惰性清除
- 每 300 秒执行一次完整清理
- `limit_per_minute = 0` 表示不限速
- 通过 `get_rate_limiter()` 获取单例

### Token 配额检查器

**文件：** `app/core/quota_checker.py`

基于 SQL 的配额检查：

- 日检查：`SUM(prompt_tokens + completion_tokens)` 从 UTC 午夜开始
- 月检查：`SUM(prompt_tokens + completion_tokens)` 从当月 1 号 UTC 时间开始
- 两者都使用 `COALESCE` 处理 NULL 值
- `quota = 0` 表示不限制
- 超限时返回描述性错误信息

### 适配器

**文件：** `app/adapters/base.py`（抽象基类）、`app/adapters/openai.py`、`app/adapters/anthropic.py`

**BaseAdapter** 提供：
- `config`：BackendConfig 实例
- `proxy_url`：从 ProxyConfig 构建的代理 URL 字符串

**OpenAIAdapter**（`PATH = "/chat/completions"`）：
- 请求头：`Authorization: Bearer <api_key>`
- Token 提取：从响应 `usage` 对象（非流式）或从 `data: {...usage...}` 数据块（流式）
- 错误处理：`TimeoutException` → 504、`ConnectError/OSError` → 502

**AnthropicAdapter**（`PATH = "/messages"`）：
- 请求头：`x-api-key: <api_key>`、`anthropic-version: 2023-06-01`
- Token 提取：从 `message_start` 事件（input_tokens → prompt_tokens）和 `message_delta` 事件（output_tokens → completion_tokens）
- 错误处理：同 OpenAIAdapter

两个适配器每次请求都创建新的 `httpx.AsyncClient`，设置 `trust_env=False` 以避免系统代理设置干扰。

### 优先级策略

**文件：** `app/strategies/base.py`、`app/strategies/api_key_based.py`、`app/strategies/factory.py`

**策略接口：** `async get_priority(request, user_name) → int`

**ApiKeyPriorityStrategy：**
1. 如果认证禁用或用户是 anonymous → 返回 `default_priority`（100）
2. 从 `Authorization` 请求头提取 API Key
3. 在 SQLite 中查找 key 的优先级
4. 返回配置的优先级或回退到 `default_priority`

**工厂：**
- `strategy_name = "api_key"` → `ApiKeyPriorityStrategy()`
- 未知策略名称 → `ValueError`

---

## 代理支持

网关支持通过 HTTP、HTTPS 或 SOCKS5 代理服务器路由后端 LLM 请求。

**配置方式：**
- 全局设置，应用于所有后端请求
- 可通过 `config.yaml`（proxy 配置段）或管理页面（System Tab）配置
- 通过管理 API 更改后立即生效（适配器是无状态的，每次请求重新创建）

**代理 URL 格式：**
```
http://user:pass@host:port     # HTTP/HTTPS 带认证
socks5://user:pass@host:port   # SOCKS5 带认证
```

**注意：** 代理仅用于向 LLM 后端的出站请求。不适用于管理面板页面或 API 服务器本身。

**依赖：** 使用 `httpx` 原生代理支持 HTTP/HTTPS，使用 `httpx-socks`（封装 `socksio`）支持 SOCKS5。

---

## 调试模式

当 `debug.enabled = true` 时，完整的请求和响应数据会保存到磁盘。

**文件命名规则：**
```
data/debug/{YYYYMMDDHHMMSSmmm}_{request_id[:12]}_{model}_request.json
data/debug/{YYYYMMDDHHMMSSmmm}_{request_id[:12]}_{model}_response.json
```

**行为：**
- 请求体在接收后立即保存
- 非流式响应体在完成后保存
- 流式响应数据块在内存中缓存，流完成后写入文件
- 文件为带缩进的 JSON 格式
- 调试保存过程中的错误会被记录但不影响请求处理

---

## 日志与指标

### 结构化日志

使用 `structlog`，支持可配置的输出格式：

- **JSON 格式**（`logging.format: "json"`）：机器可解析的 JSON 行，适用于日志聚合系统
- **文本格式**（`logging.format: "text"`）：人类可读的彩色控制台输出

**日志字段（请求生命周期）：**
- `request_id`、`user`、`endpoint`、`model`、`priority`
- `wait_time_ms`、`processing_time_ms`
- `status_code`、`streamed`、`prompt_tokens`、`completion_tokens`
- `error`、`client_ip`

**日志清理（启动时）：**
- 删除早于 `log_retention.retention_days` 的日志
- 如果超过 `log_retention.max_records` 则截断
- 两者都在应用启动时自动执行

### Prometheus 指标

当 `metrics.enabled = true`（默认）时，可通过 `GET /metrics` 获取。

| 指标 | 类型 | 标签 | 描述 |
|------|------|------|------|
| `gateway_requests_total` | Counter | `endpoint`, `status_code`, `user` | 总代理请求数 |
| `gateway_queue_length` | Gauge | — | 当前队列等待数 |
| `gateway_requests_processing` | Gauge | — | 当前处理状态 |
| `gateway_request_duration_seconds` | Histogram | `endpoint` | 端到端耗时 (buckets: 0.1–120s) |
| `gateway_wait_time_seconds` | Histogram | `endpoint` | 队列等待耗时 (buckets: 0.01–30s) |

---

## SQLite 数据库 Schema

### `api_keys` 表

| 列名 | 类型 | 描述 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `key` | TEXT UNIQUE | API Key 值（`sk-` + 64 位十六进制字符） |
| `name` | TEXT | 可读名称 |
| `priority` | INTEGER | 优先级（默认 100，数值越小优先级越高） |
| `enabled` | INTEGER | 0 = 禁用, 1 = 启用 |
| `created_at` | TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 最后更新时间 |
| `rate_limit` | INTEGER | 每分钟请求限制（0 = 不限制） |
| `token_quota_daily` | INTEGER | 每日 Token 限制（0 = 不限制） |
| `token_quota_monthly` | INTEGER | 每月 Token 限制（0 = 不限制） |

### `request_logs` 表

| 列名 | 类型 | 描述 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `request_id` | TEXT UNIQUE | UUID 十六进制标识符 |
| `user_name` | TEXT | API Key 所有者名称 |
| `endpoint` | TEXT | 请求端点路径 |
| `model` | TEXT | 模型名称 |
| `priority` | INTEGER | 请求优先级 |
| `enqueue_time` | TIMESTAMP | 入队时间 |
| `dequeue_time` | TIMESTAMP | 出队时间 |
| `complete_time` | TIMESTAMP | 处理完成时间 |
| `wait_time_ms` | INTEGER | 队列等待时间（毫秒） |
| `processing_time_ms` | INTEGER | 处理时间（毫秒） |
| `status_code` | INTEGER | HTTP 响应状态码 |
| `streamed` | INTEGER | 1=流式, 0=非流式 |
| `prompt_tokens` | INTEGER | 输入 Token 数 |
| `completion_tokens` | INTEGER | 输出 Token 数 |
| `error` | TEXT | 错误信息（如有） |
| `client_ip` | TEXT | 客户端 IP 地址 |
| `created_at` | TIMESTAMP | 日志条目创建时间 |

**索引：** `idx_logs_created_at`、`idx_logs_user`、`idx_logs_endpoint`、`idx_keys_key`
