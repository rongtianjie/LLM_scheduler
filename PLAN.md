# LLM Gateway Proxy — 实现方案

## 一、项目结构

```
llm-gateway-proxy/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口，应用生命周期管理
│   ├── config.py                  # YAML 配置加载与校验（pydantic-settings）
│   ├── database.py                # SQLite 初始化、迁移、连接管理（aiosqlite）
│   ├── models.py                  # SQLite 表模型定义 + Pydantic schemas
│   ├── api/
│   │   ├── __init__.py
│   │   ├── proxy.py               # POST /v1/chat/completions, /v1/messages
│   │   ├── admin_api.py           # Admin REST API（CRUD API keys + stats）
│   │   └── admin_pages.py         # Admin 页面路由（Jinja2 模板）
│   ├── core/
│   │   ├── __init__.py
│   │   ├── queue.py               # PriorityQueue（asyncio.Condition 驱动）
│   │   ├── auth.py                # API Key 认证依赖（可配置开关）
│   │   └── metrics.py             # Prometheus 指标定义
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                # BaseAdapter 抽象类
│   │   ├── openai.py              # OpenAI 格式适配器
│   │   └── anthropic.py           # Anthropic 格式适配器
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py                # PriorityStrategy 抽象类
│   │   ├── ip_based.py            # IP 优先级策略
│   │   └── api_key_based.py       # API Key 优先级策略
│   ├── templates/                 # Jinja2 管理页面模板
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── api_keys.html
│   │   └── logs.html
│   └── static/
│       └── style.css
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_queue.py
│   ├── test_adapters.py
│   ├── test_auth.py
│   └── test_strategies.py
├── config.yaml                    # 默认配置文件
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 二、核心架构设计

### 1. 请求生命周期

```
客户端 POST → API Key 认证 → 优先级计算 → 入队 (PriorityQueue)
                                              ↓
                                        等待轮次 (asyncio.Condition)
                                              ↓
                                        出队 → current_processing 锁定
                                              ↓
                                        适配器转发请求到后端
                                              ↓
                                  ┌── 流式：StreamingResponse + finally signal_done
                                  └── 非流式：等待响应 → signal_done → 返回
```

### 2. 队列与并发控制

核心类 `PriorityQueue`：

- 使用 `heapq` 存储 `(priority, timestamp, id, request_context)`，优先级数值越小越优先
- `concurrency=1` 通过 `current_processing` 字段控制：出队时检查是否有请求正在处理，无则锁定并出队
- `asyncio.Condition` 实现等待/通知：所有等待的 handler 在 condition 上 wait，当前请求完成后 notify_all
- 入队时若队列满（包含等待中 + 处理中），直接返回 429

```python
async def wait_for_turn(self, request_id):
    async with self.lock:
        while True:
            if self.current_processing is not None or not self.heap:
                await self.condition.wait()
                continue
            front = self.heap[0]
            if front.request_id == request_id:
                self.current_processing = request_id
                heapq.heappop(self.heap)
                return
            await self.condition.wait()
```

### 3. 流式透传

```python
async def stream_response(context: RequestContext):
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", backend_url, json=body, headers=headers) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
    finally:
        await request_queue.signal_done(context.request_id)
```

- 不做任何 SSE 事件解析或修改
- `finally` 中调用 `signal_done`，确保无论正常结束、客户端断开、还是后端异常都释放槽位

### 4. 优先级策略

```python
class PriorityStrategy(ABC):
    @abstractmethod
    async def get_priority(self, request: Request, user: Optional[User]) -> int: ...

class ApiKeyPriorityStrategy(PriorityStrategy):
    # 从 user.priority 获取，默认 default_priority

class IPPriorityStrategy(PriorityStrategy):
    # 从 config.ip_mapping 中匹配（支持 CIDR），默认 default_priority
```

通过 `config.priority.strategy` 切换。auth 开启时默认使用 `api_key`，关闭时使用 `ip_based`。

## 三、数据库设计 (SQLite)

```sql
CREATE TABLE api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    UNIQUE NOT NULL,       -- 生成的 API Key (UUID)
    name        TEXT    NOT NULL,              -- 用户名/标识
    priority    INTEGER NOT NULL DEFAULT 100,  -- 越小越优先
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE request_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT    UNIQUE NOT NULL,
    user_name        TEXT,
    endpoint         TEXT    NOT NULL,         -- /v1/chat/completions | /v1/messages
    model            TEXT,
    priority         INTEGER NOT NULL,
    enqueue_time     TIMESTAMP,
    dequeue_time     TIMESTAMP,
    complete_time    TIMESTAMP,
    wait_time_ms     INTEGER,
    processing_time_ms INTEGER,
    status_code      INTEGER,
    streamed         INTEGER,
    error            TEXT,
    client_ip        TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 四、管理后台

管理后台与 LLM API 共享 8001 端口，包含 Admin 认证、页面路由和 REST API。

### Admin 认证
- HTTP Basic Auth，凭据在 `config.yaml` 中配置 (`admin.username` / `admin.password`)
- 可独立启用/禁用 (`admin.enabled`)

### Admin 页面路由
| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 仪表盘：队列状态、请求统计、实时指标 |
| `/api-keys` | GET | API Key 管理页面 |
| `/logs` | GET | 请求日志查看页面 |

### Admin REST API
| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/queue` | GET | **队列状态**：max_length + current_waiting + current_processing |
| `/api/keys` | GET | 列出所有 API Key |
| `/api/keys` | POST | 创建新 API Key |
| `/api/keys/{id}` | PUT | 更新 API Key（名称、优先级、启用状态） |
| `/api/keys/{id}` | DELETE | 删除 API Key |
| `/api/stats` | GET | 仪表盘统计数据 |
| `/api/logs` | GET | 查询日志（分页、按时间筛选） |

## 五、可观测性

### 结构化日志 (structlog)
```json
{"event": "request_completed", "request_id": "...", "user": "alice", 
 "endpoint": "/v1/chat/completions", "model": "gpt-4", "wait_time_ms": 320,
 "processing_time_ms": 5200, "status": 200, "streamed": true,
 "timestamp": "2026-06-05T10:00:00Z", "level": "info"}
```

### Prometheus 指标
| 指标名 | 类型 | 标签 |
|--------|------|------|
| `gateway_requests_total` | Counter | endpoint, status, user |
| `gateway_queue_length` | Gauge | — |
| `gateway_requests_waiting` | Gauge | — |
| `gateway_request_duration_seconds` | Histogram | endpoint |
| `gateway_wait_time_seconds` | Histogram | priority_level |

指标暴露在 `GET /metrics`（标准 Prometheus 端点）。

## 六、统一端口架构（8001）

所有服务 — Proxy API、Admin 页面、Admin REST API、Prometheus 指标 — 统一监听在 **8001 端口**的单个 FastAPI 实例上。

| 路径 | 方法 | 说明 |
|------|------|------|
| `GET /health` | GET | 健康检查 |
| `POST /v1/chat/completions` | POST | OpenAI 兼容代理 |
| `POST /v1/messages` | POST | Anthropic 兼容代理 |
| `GET /metrics` | GET | Prometheus 指标 |
| `GET /` | GET | Admin 仪表盘 |
| `GET /api-keys` | GET | API Key 管理页面 |
| `GET /logs` | GET | 请求日志页面 |
| `GET /api/queue` | GET | 队列状态（max_length + current_waiting） |
| `GET /api/keys` | GET | 列出 API Key |
| `POST /api/keys` | POST | 创建 API Key |
| `PUT /api/keys/{id}` | PUT | 更新 API Key |
| `DELETE /api/keys/{id}` | DELETE | 删除 API Key |
| `GET /api/stats` | GET | 仪表盘统计数据 |
| `GET /api/logs` | GET | 查询日志（分页） |

`GET /api/queue` 响应示例：
```json
{
  "max_length": 5,
  "current_waiting": 2,
  "current_processing": 1,
  "queue_full": false
}
```

## 七、配置文件 (config.yaml)

```yaml
server:
  port: 8001
  host: "0.0.0.0"

auth:
  enabled: true

admin:
  enabled: true
  username: "admin"
  password: "admin123"

database:
  path: "data/gateway.db"

queue:
  max_length: 5
  concurrency: 1

priority:
  strategy: "api_key"  # "api_key" | "ip_based"
  default_priority: 100

backend:
  base_url: "http://localhost:11434/v1"
  api_key: "sk-backend-key"
  timeout: 300  # seconds

logging:
  level: "INFO"
  format: "json"  # "json" | "text"

metrics:
  enabled: true
```

## 八、实现顺序

| 阶段 | 内容 | 预估文件数 |
|------|------|-----------|
| 1 | 项目骨架：config.py, database.py, models.py, main.py 基础 | 5 |
| 2 | 优先级队列：core/queue.py 含 Condition 驱动逻辑 | 2 |
| 3 | 认证与策略：auth.py + strategies/ 目录 (api_key, ip_based) | 5 |
| 4 | 适配器层：base.py, openai.py, anthropic.py + 流式透传 | 4 |
| 5 | API 端点：proxy.py 整合 队列 + 认证 + 适配器 | 2 |
| 6 | 可观测性：metrics.py + structlog 配置 | 2 |
| 7 | 管理后台：admin_api.py + admin_pages.py + Jinja2 模板 | 6 |
| 8 | 测试：单元测试 + 集成测试 | 5 |
| 9 | CLAUDE.md + 部署：Dockerfile + docker-compose.yml + README.md | 4 |
