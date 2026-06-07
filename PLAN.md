# LLM Scheduler — 实现方案

## 一、项目结构

```
llm-scheduler/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口，应用生命周期管理（GZip、健康检查、密码哈希）
│   ├── config.py                  # YAML 配置加载与校验（pydantic-settings）
│   ├── database.py                # SQLite 初始化、迁移、连接管理（aiosqlite）
│   ├── models.py                  # SQLite 表模型定义 + Pydantic schemas（ApiKeyInfo, RequestContext）
│   ├── api/
│   │   ├── __init__.py
│   │   ├── proxy.py               # POST /v1/chat/completions, /v1/messages, DELETE /v1/queue/{id}
│   │   ├── admin_api.py           # Admin REST API（CRUD API keys + stats + password change + health）
│   │   └── admin_pages.py         # Admin 页面路由（Jinja2 模板，bcrypt 登录，锁定保护）
│   ├── core/
│   │   ├── __init__.py
│   │   ├── queue.py               # PriorityQueue（asyncio.Condition，支持 cancel、动态调整）
│   │   ├── auth.py                # API Key 认证（返回 ApiKeyInfo）+ Session 认证
│   │   ├── metrics.py             # Prometheus 指标定义（backend_duration, tokens_total）
│   │   ├── rate_limiter.py        # 速率限制器（deque 滑动窗口）
│   │   ├── quota_checker.py       # Token 配额检查（日/月）
│   │   ├── health_checker.py      # 后端健康检查（定时探活、故障转移）
│   │   ├── password.py            # bcrypt 密码哈希与验证（兼容明文回退）
│   │   └── http_client.py         # 共享 httpx.AsyncClient 连接池
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                # BaseAdapter 抽象类（proxy_url + trace_id）
│   │   ├── openai.py              # OpenAI 格式适配器（x-trace-id 头注入）
│   │   └── anthropic.py           # Anthropic 格式适配器（x-trace-id 头注入）
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py                # PriorityStrategy 抽象类
│   │   ├── ip_based.py            # IP 优先级策略
│   │   └── api_key_based.py       # API Key 优先级策略（接受 key_info 跳过重复查询）
│   ├── templates/                 # Jinja2 管理页面模板
│   │   ├── base.html              # 基础布局（暗色模式、汉堡菜单、响应式）
│   │   ├── login.html             # 登录页面
│   │   ├── dashboard.html         # 仪表盘（定时刷新、Processing X/N 格式）
│   │   ├── api_keys.html          # API Key 管理（列配置、编辑 modal、Key 可复制）
│   │   ├── logs.html              # 日志查看（模型/状态/日期筛选、自动刷新）
│   │   └── management.html        # 运行时配置（标签栏 Save 按钮、健康状态、密码修改）
│   └── static/
│       ├── style.css              # 亮色+暗色主题、响应式布局、列选择器样式
│       └── chart.umd.min.js       # Chart.js v4
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # 共享 fixtures（登录失败清理、配置预设）
│   ├── test_api.py                # API 集成测试（含模型路由、重试、body size、配置 API）
│   ├── test_queue.py              # 队列测试（含 cancel、update_max_size、update_concurrency）
│   ├── test_queue_timeout.py      # 队列超时测试
│   ├── test_adapters.py           # 适配器测试
│   ├── test_auth.py               # 认证测试
│   ├── test_strategies.py         # 策略测试
│   ├── test_rate_limit.py         # 速率限制测试（deque 兼容）
│   ├── test_quota.py              # 配额测试
│   ├── test_backend_health.py     # 健康检查测试（9 用例）
│   ├── test_request_cancel.py     # 请求取消测试（7 用例）
│   ├── test_model_routing.py      # 模型路由测试（8 用例）
│   ├── test_admin_password.py     # 密码哈希/修改/锁定测试（11 用例）
│   └── test_trace_id.py           # Trace ID 测试（6 用例）
├── config.yaml                    # 默认配置文件
├── pyproject.toml                 # Python 项目配置（含 bcrypt 依赖）
├── Dockerfile
├── docker-compose.yml
├── README.md / README.zh.md       # 项目说明
├── DOCS.md / DOCS.zh.md           # 详细文档
├── PLAN.md                        # 实现方案（本文件）
└── CLAUDE.md                      # AI 助手指南
```

## 二、核心架构设计

### 1. 请求生命周期

```
客户端 POST → API Key 认证 (ApiKeyInfo) → 速率限制 → 配额检查 → 优先级计算
                                              ↓
                                        入队 (PriorityQueue)
                                              ↓
                                  等待轮次 (asyncio.Condition + timeout)
                                              ↓
                                  模型路由 (健康检查 > 精确匹配 > 通配符 > 协议回退)
                                              ↓
                                        出队 → current_processing 锁定
                                              ↓
                                  适配器转发请求到后端 (共享连接池, x-trace-id 注入)
                                              ↓
                               ┌── 失败 (502/503): 排除后端重试 1 次
                               ├── 流式：StreamingResponse + finally signal_done
                               └── 非流式：等待响应 → signal_done → 返回
```

### 2. 队列与并发控制

核心类 `PriorityQueue`：

- 使用 `heapq` 存储 `(priority, timestamp, id, request_context)`，优先级数值越小越优先
- `concurrency` 通过 `current_processing` 集合控制多并发
- `asyncio.Condition` 实现等待/通知
- `cancel(request_id, user_name)` 取消排队请求（仅所有者可取消）
- `update_max_size()` / `update_concurrency()` 动态调整
- 入队时若队列满（含等待中 + 处理中），直接返回 429

### 3. 后端健康检查

`HealthChecker` 后台 asyncio 任务：
- 定时 HTTP GET 每个后端的 `/health` 端点
- 连续 3 次失败标记 unhealthy，1 次成功恢复
- `_select_backend()` 跳过 unhealthy 节点
- 状态通过 `GET /admin/api/backends/health` 暴露

### 4. 模型级别路由

`BackendConfig.models` 字段支持：
- 精确模型名：`["gpt-4", "gpt-4-turbo"]`
- 通配符：`["*"]` 匹配所有模型
- 空列表/null：匹配所有模型
- 选择优先级：精确匹配 > 通配符 `*` > 协议 fallback
- `exclude` 参数排除已尝试的后端（用于重试）

### 5. 流式透传

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

- 不做 SSE 事件解析或修改
- `finally` 中调用 `signal_done`，确保释放槽位

### 6. 优先级策略

```python
class PriorityStrategy(ABC):
    @abstractmethod
    async def get_priority(self, request: Request, user: Optional[User], key_info: Optional[ApiKeyInfo] = None) -> int: ...

class ApiKeyPriorityStrategy(PriorityStrategy):
    # 从 key_info.priority 获取（若提供则跳过 DB 查询），默认 default_priority

class IPPriorityStrategy(PriorityStrategy):
    # 从 config.ip_mapping 中匹配（支持 CIDR），默认 default_priority
```

### 7. 请求取消

```
DELETE /v1/queue/{request_id} (需认证)
    → PriorityQueue.cancel(request_id, user_name)
    → 从堆中移除 + 通知等待协程
    → 仅请求所有者可取消，其他用户返回 404
```

### 8. 重试机制

非流式请求遇到 502/503 时自动重试到不同后端：
- `_select_backend(exclude=[failed_backend])` 排除已试后端
- 最多 1 次额外尝试
- 流式请求不重试（避免部分数据已发送）

## 三、数据库设计 (SQLite)

```sql
CREATE TABLE api_keys (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    key              TEXT    UNIQUE NOT NULL,
    name             TEXT    NOT NULL,
    priority         INTEGER NOT NULL DEFAULT 100,
    rate_limit       INTEGER NOT NULL DEFAULT 0,        -- 0 = unlimited
    token_quota_daily   INTEGER NOT NULL DEFAULT 0,     -- 0 = unlimited
    token_quota_monthly INTEGER NOT NULL DEFAULT 0,     -- 0 = unlimited
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE request_logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         TEXT    UNIQUE NOT NULL,
    user_name          TEXT,
    endpoint           TEXT    NOT NULL,
    model              TEXT,
    priority           INTEGER NOT NULL,
    enqueue_time       TIMESTAMP,
    dequeue_time       TIMESTAMP,
    complete_time      TIMESTAMP,
    wait_time_ms       INTEGER,
    processing_time_ms INTEGER,
    status_code        INTEGER,
    prompt_tokens      INTEGER DEFAULT 0,
    completion_tokens  INTEGER DEFAULT 0,
    streamed           INTEGER,
    trace_id           TEXT,
    error              TEXT,
    client_ip          TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 四、管理后台

管理后台与 LLM API 共享 8001 端口，包含 Admin 认证、页面路由和 REST API。

### Admin 认证
- Session/Cookie 认证，凭据在 `config.yaml` 中配置
- bcrypt 密码哈希存储，启动时自动将明文密码升级为哈希
- 密码重置：创建 `reset_admin_password` 文件，写入新密码，重启
- 登录锁定：同一 IP 5 次失败后锁定 300 秒（内存记录）
- 可独立启用/禁用 (`admin.enabled`)

### Admin 页面路由
| 路径 | 方法 | 说明 |
|------|------|------|
| `/admin/login` | GET/POST | 登录页面与表单提交 |
| `/admin/logout` | GET | 登出（清除 session） |
| `/admin` | GET | 仪表盘 |
| `/admin/api-keys` | GET | API Key 管理页面 |
| `/admin/logs` | GET | 日志查看页面 |
| `/admin/management` | GET | 运行时配置页面 |

### Admin REST API
| 路径 | 方法 | 说明 |
|------|------|------|
| `/admin/api/queue` | GET | 队列状态 |
| `/admin/api/keys` | GET/POST | API Key 列表与创建 |
| `/admin/api/keys/{id}` | PUT/DELETE | API Key 更新与删除 |
| `/admin/api/stats` | GET | 仪表盘统计数据 |
| `/admin/api/stats/timeseries` | GET | 时间序列图表数据 |
| `/admin/api/logs` | GET | 查询日志（支持模型/状态/日期筛选） |
| `/admin/api/config` | GET/PUT | 获取/更新运行时配置 |
| `/admin/api/backends/health` | GET | 后端健康状态 |
| `/admin/api/admin/password` | PUT | 修改管理员密码 |

## 五、可观测性

### 结构化日志 (structlog)
```json
{"event": "request_completed", "request_id": "...", "user": "alice",
 "trace_id": "a1b2c3d4...", "endpoint": "/v1/chat/completions", "model": "gpt-4",
 "wait_time_ms": 320, "processing_time_ms": 5200, "status": 200, "streamed": true,
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
| `gateway_backend_request_duration_seconds` | Histogram | backend, endpoint |
| `gateway_tokens_total` | Counter | type (prompt/completion), user |

## 六、统一端口架构（8001）

所有服务统一监听在 **8001 端口**。

| 路径 | 方法 | 说明 |
|------|------|------|
| `GET /health` | GET | 基础健康检查 |
| `GET /health/ready` | GET | 就绪检查（DB + 后端可达） |
| `POST /v1/chat/completions` | POST | OpenAI 兼容代理 |
| `POST /v1/messages` | POST | Anthropic 兼容代理 |
| `GET /v1/models` | GET | 模型列表（转发到后端） |
| `GET /v1/queue` | GET | 队列状态 |
| `DELETE /v1/queue/{id}` | DELETE | 取消排队请求（需认证） |
| `GET /metrics` | GET | Prometheus 指标 |
| Admin REST API | — | 见第四章 |

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
  password: "admin123"                      # 明文密码，首次启动自动哈希为 bcrypt
  secret_key: "change-me-to-a-random-string"
  session_https_only: false

database:
  path: "data/gateway.db"

queue:
  max_length: 5
  concurrency: 1
  timeout: 300                              # 队列等待超时秒数（0=无限制）

request:
  max_body_size: 0                          # 请求体大小限制字节（0=不限制）

priority:
  strategy: "api_key"                       # "api_key" | "ip_based"
  default_priority: 100

backends:
  - name: "openai-primary"
    base_url: "http://localhost:11434/v1"
    api_key: "sk-backend-key"
    protocols: ["openai"]
    models: ["gpt-4", "gpt-4-turbo"]       # 可选：模型路由列表（"*" 匹配所有）
    timeout: 300
    enabled: true
  - name: "anthropic-primary"
    base_url: "https://api.anthropic.com"
    api_key: "sk-ant-backend-key"
    protocols: ["anthropic"]
    models: ["*"]                           # 通配符匹配所有模型
    timeout: 300
    enabled: true

logging:
  level: "INFO"
  format: "json"                            # "json" | "text"

log_retention:
  retention_days: 30
  max_records: 100000

metrics:
  enabled: true

cors:
  origins: ["*"]

proxy:
  url: ""                                   # 全局代理 URL，如 socks5://127.0.0.1:1080
  username: ""
  password: ""

debug:
  enabled: false
```

## 八、密码安全

- **密码哈希**：bcrypt 自动哈希，`verify_password()` 识别 `$2b$`/`$2a$`/`$2y$` 前缀
- **自动升级**：启动时检测明文密码（< 60 字符），自动哈希写回 config.yaml
- **密码重置**：创建 `reset_admin_password` 文件写入新密码，重启后生效并删除文件
- **登录锁定**：同一 IP 5 次失败后锁定 300 秒（内存记录，重启清除）
- **密码修改 API**：`PUT /admin/api/admin/password`，需提供当前密码

## 九、Trace ID 传播

```
客户端 (x-trace-id: "xxx" 或自动生成 uuid4)
    → Gateway (RequestContext.trace_id)
    → 适配器 (注入 x-trace-id 头)
    → 后端服务
    → 结构化日志绑定 trace_id
```

## 十、实现顺序

| 阶段 | 内容 | 预估文件数 |
|------|------|-----------|
| 1 | 项目骨架：config.py, database.py, models.py, main.py 基础 | 5 |
| 2 | 优先级队列：core/queue.py 含 Condition 驱动逻辑 | 2 |
| 3 | 认证与策略：auth.py + strategies/ 目录 (api_key, ip_based) | 5 |
| 4 | 适配器层：base.py, openai.py, anthropic.py + 流式透传 | 4 |
| 5 | API 端点：proxy.py 整合 队列 + 认证 + 适配器 | 2 |
| 6 | 可观测性：metrics.py + structlog 配置 | 2 |
| 7 | 管理后台：admin_api.py + admin_pages.py + Jinja2 模板 | 6 |
| 8 | 测试：单元测试 + 集成测试 | 15 |
| 9 | CLAUDE.md + 部署：Dockerfile + docker-compose.yml + README.md | 7 |
| 10 | 全面优化：健康检查、密码哈希、模型路由、暗色模式等 | 10 |
