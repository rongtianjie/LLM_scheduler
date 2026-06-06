# LLM Gateway Proxy

生产级 LLM API 网关代理，支持优先级队列、并发控制、API Key 认证和嵌入式管理面板。

## 功能特性

- **双协议兼容**：同时支持 OpenAI 和 Anthropic API 格式，可独立配置后端地址
- **优先级队列**：根据 API Key 分配优先级，高优先级请求插队
- **并发控制**：可配置并发数（concurrency），队列满返回 429
- **流式透传**：SSE 事件流原样转发，不解析不修改
- **Token 统计**：自动记录每次请求的输入/输出 token 数（流式 + 非流式）
- **Dashboard 图表**：Chart.js 时间序列图表，支持 1h/6h/24h/7d/30d 周期切换
- **Debug 模式**：开启后将完整请求/响应体保存到磁盘，便于排查问题
- **API Key 认证**：独立 API Key，可配置开关
- **管理员登录**：自定义登录页面，基于 Session/Cookie 的认证机制
- **代理服务器**：支持通过 HTTP/HTTPS/SOCKS5 代理服务器转发后端请求
- **结构化日志**：JSON 格式，完整请求生命周期记录（含 token 用量）
- **Prometheus 指标**：队列长度、请求延迟、处理时间等
- **嵌入式管理面板**：科技感 UI，Web 界面管理 API Key、查看日志、统计和仪表盘
- **Docker 部署**：一键启动，数据持久化

## 快速开始

### 本地运行

```bash
# 1. 创建虚拟环境并安装依赖
uv sync

# 2. 编辑配置（config.local.yaml 会自动覆盖 config.yaml）
cp config.yaml config.local.yaml
# 修改 openai_backend.base_url、anthropic_backend.base_url 等（也可启动后在管理页面配置）
# 不配置的值将使用代码默认值

# 3. 启动（自动使用虚拟环境）
uv run python -m app.main
```

### Docker Compose 部署

```bash
# 1. 编辑配置文件
vim config.yaml  # 修改启动配置（server、admin 账号等）

# 2. 启动
docker-compose up -d

# 3. 查看日志
docker-compose logs -f
```

### 裸机 Docker 部署

```bash
# 构建
docker build -t llm-gateway-proxy .

# 运行
docker run -d \
  --name llm-gateway \
  -p 8001:8001 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v gateway-data:/app/data \
  llm-gateway-proxy
```

## 配置说明

`config.yaml` 仅包含启动级配置（server、auth、admin、database、logging、proxy），运行时配置（队列、后端、Debug、Metrics、Proxy）通过管理页面控制。默认值见 `app/config.py`。

```yaml
server:
  host: "0.0.0.0"
  port: 8001

auth:
  enabled: true                  # API Key 认证开关

admin:
  enabled: true
  username: "admin"
  password: "admin123"
  secret_key: "llm-gateway-default-secret"  # Session 加密密钥

database:
  path: "data/gateway.db"

logging:
  level: "INFO"
  format: "json"                 # "json" | "text"

proxy:
  enabled: false                 # 代理服务器开关
  protocol: "http"               # "http" | "https" | "socks5"
  host: ""
  port: 0
  username: ""                   # 代理认证（可选）
  password: ""
```

## API 使用

### 代理请求

```bash
# OpenAI 格式
curl http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'

# Anthropic 格式
curl http://localhost:8001/v1/messages \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'

# 查看队列状态（无需认证）
curl http://localhost:8001/v1/queue
```

### 管理接口

```bash
# 登录获取 session cookie
curl -c cookies.txt -X POST http://localhost:8001/admin/login \
  -d "username=admin&password=admin123"

# 获取队列状态
curl -b cookies.txt http://localhost:8001/admin/api/queue

# 创建 API Key
curl -b cookies.txt -X POST http://localhost:8001/admin/api/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "alice", "priority": 50}'

# 查询日志
curl -b cookies.txt "http://localhost:8001/admin/api/logs?page=1&per_page=20"

# 获取统计（支持时间范围: 1h, 6h, 24h, 7d, 30d, all）
curl -b cookies.txt "http://localhost:8001/admin/api/stats?period=24h"

# 获取时间序列数据（用于图表）
curl -b cookies.txt "http://localhost:8001/admin/api/stats/timeseries?period=24h"

# 更新代理配置
curl -b cookies.txt -X PUT http://localhost:8001/admin/api/config \
  -H "Content-Type: application/json" \
  -d '{"proxy": {"enabled": true, "protocol": "socks5", "host": "127.0.0.1", "port": 1080}}'
```

## 管理面板

浏览器访问 `http://localhost:8001/admin`，使用配置的管理员账号登录。

- **登录页面**：自定义登录表单，蓝紫渐变科技感设计，基于 Session/Cookie 认证（24小时过期）
- **Dashboard**：实时队列状态、请求统计，Chart.js 时间序列图表（Requests/Tokens），支持时间范围选择（1h/6h/24h/7d/30d），按 API Key 展示请求数和 Token 用量
- **API Keys**：创建/编辑/删除 API Key，创建时显示完整 Key 并支持一键复制
- **Logs**：查看请求历史，含 Token 用量列和状态码颜色标记，支持按用户和端点筛选分页
- **Management**：运行时配置管理，分三个 Tab（Scheduling / Backend / System）
  - **Scheduling**：队列配置（Max Length、Concurrency）+ 优先级策略
  - **Backend**：OpenAI 后端 + Anthropic 后端配置，支持一键同步
  - **System**：Debug 模式、Prometheus Metrics、代理服务器（HTTP/HTTPS/SOCKS5）配置

## 队列行为

1. 所有请求按优先级入队（数值越小越优先）
2. 同一时间只处理 1 个请求
3. 高优先级请求插入队列头部，不中断当前正在处理的请求
4. 队列满时返回 HTTP 429
5. 流式请求持续期间，后续请求排队等待

## 测试

```bash
uv run pytest tests/
```

## 开发

项目结构：

```
app/
├── main.py              # 入口，应用工厂，SessionMiddleware
├── config.py            # 配置加载（含 ProxyConfig）
├── database.py          # SQLite 管理
├── models.py            # 数据模型
├── api/
│   ├── proxy.py         # 代理端点
│   ├── admin_api.py     # 管理 API（含 timeseries）
│   └── admin_pages.py   # 管理页面（含登录/登出）
├── core/
│   ├── queue.py         # 优先级队列
│   ├── auth.py          # 认证（Session + API Key）
│   └── metrics.py       # 指标
├── adapters/
│   ├── base.py          # 适配器基类（含代理支持）
│   ├── openai.py        # OpenAI 适配器
│   └── anthropic.py     # Anthropic 适配器
├── strategies/
│   ├── base.py               # 策略抽象
│   └── api_key_based.py      # API Key 优先级
├── templates/           # Jinja2 页面模板
└── static/
    ├── style.css        # 科技感主题样式
    └── chart.umd.min.js # Chart.js（本地部署）
```

## 扩展点

- **优先级策略**：实现 `PriorityStrategy` 接口，通过配置切换
- **多并发**：修改 `queue.concurrency` > 1，改造为工作协程池
- **负载均衡**：在适配器层增加 upstream 选择逻辑
