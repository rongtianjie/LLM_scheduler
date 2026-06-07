> [**English Version**](./README.md) | [**中文版本**](./README.zh.md)
>
> **详细API文档请参见：[中文文档](./DOCS.zh.md) | [English Documentation](./DOCS.md)**

# LLM Gateway Proxy

生产级 LLM API 网关代理，支持优先级队列、并发控制、API Key 认证和嵌入式管理面板。

## 功能特性

- **双协议兼容**：同时支持 OpenAI 和 Anthropic API 格式，可独立配置后端地址
- **负载均衡**：多后端支持同一协议时自动轮询（round-robin），每条后端可独立启用/禁用
- **优先级队列**：根据 API Key 分配优先级，高优先级请求插队
- **并发控制**：可配置并发数（concurrency），队列满返回 429
- **队列超时**：可配置等待超时时间，超时返回 408
- **速率限制**：支持 API Key 级别请求速率限制（请求数/分钟），超限返回 429
- **Token 配额**：支持 API Key 级别日/月 Token 用量配额，超限拒绝请求
- **日志清理**：自动清理过期日志，支持按保留天数和最大记录数限制
- **流式透传**：SSE 事件流原样转发，不解析不修改
- **Token 统计**：自动记录每次请求的输入/输出 token 数（流式 + 非流式）
- **Dashboard 图表**：Chart.js 时间序列图表，支持 1h/6h/24h/7d/30d 周期切换
- **Debug 模式**：开启后将完整请求/响应体保存到磁盘，便于排查问题
- **API Key 认证**：独立 API Key，可配置开关
- **管理员登录**：自定义登录页面，基于 Session/Cookie 的认证机制
- **代理服务器**：支持通过 HTTP/HTTPS/SOCKS5 代理服务器转发后端请求
- **CORS 支持**：可配置跨域请求来源
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

`config.yaml` 包含启动级配置（server、auth、admin、database、queue、logging、log_retention、cors、proxy），运行时配置（队列、优先级、后端、Debug、Metrics、Proxy）可通过管理页面控制。

完整的配置参考（含所有默认值）请参见详细文档中的[配置参考](./DOCS.zh.md#配置参考)章节。

## 基本用法

```bash
# OpenAI 兼容请求
curl http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}], "stream": true}'

# Anthropic 兼容请求
curl http://localhost:8001/v1/messages \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-3-opus-20240229", "max_tokens": 1024, "messages": [{"role": "user", "content": "Hello"}], "stream": true}'

# 查看队列状态（无需认证）
curl http://localhost:8001/v1/queue
```

完整的 API 参考（含所有端点、请求/响应结构和示例）请参见详细文档中的 [API 参考](./DOCS.zh.md#api-参考) 章节。

## 管理面板

浏览器访问 `http://localhost:8001/admin`，使用配置的管理员账号登录。

- **登录页面**：自定义登录表单，蓝紫渐变科技感设计，基于 Session/Cookie 认证（24小时过期）
- **Dashboard**：实时队列状态、请求统计，Chart.js 时间序列图表（Requests/Tokens），支持时间范围选择（1h/6h/24h/7d/30d），按 API Key 展示请求数和 Token 用量
- **API Keys**：创建/编辑/删除 API Key，创建时显示完整 Key 并支持一键复制
- **Logs**：查看请求历史，含 Token 用量列和状态码颜色标记，支持按用户和端点筛选分页
- **Management**：运行时配置管理，分三个 Tab（Scheduling / Backend / System）
  - **Scheduling**：队列配置（Max Length、Concurrency）+ 优先级策略
  - **Backend**：统一后端列表，支持添加/编辑/删除、协议选择（OpenAI/Anthropic）和启用/禁用开关
  - **System**：Debug 模式、Prometheus Metrics、代理服务器（HTTP/HTTPS/SOCKS5）配置

## 队列行为

1. 所有请求按优先级入队（数值越小越优先）
2. 并发数由 `queue.concurrency` 控制（默认 1，可增加以实现多并发处理）
3. 高优先级请求插入队列头部，不中断当前正在处理的请求
4. 队列满时返回 HTTP 429
5. 流式请求持续期间，后续请求排队等待
6. 队列等待超时时返回 HTTP 408（可通过 `queue.timeout` 配置，0=无限等待）

## 速率限制与配额

- **速率限制**：通过 API Key 的 `rate_limit` 字段设置（请求数/分钟），超限返回 429
- **Token 配额**：通过 `token_quota_daily` / `token_quota_monthly` 设置日/月 Token 上限
- 配额检查基于 SQLite 中已记录的 token 用量（prompt_tokens + completion_tokens）

## 测试

```bash
uv run pytest tests/
```

## 项目结构

```
app/
├── main.py              # 入口，应用工厂，CORS/SessionMiddleware
├── config.py            # 配置加载（Pydantic + YAML）
├── database.py          # SQLite 管理（WAL模式、索引、日志清理）
├── models.py            # 数据模型（Pydantic + dataclass）
├── api/                 # API 路由处理（proxy、admin pages、admin REST）
├── core/                # 核心逻辑（queue、auth、metrics、rate limiter、quota）
├── adapters/            # LLM 后端适配器（OpenAI、Anthropic）
├── strategies/          # 优先级计算策略
├── templates/           # Jinja2 管理页面模板
└── static/              # 静态资源（CSS、Chart.js）
```

详细的逐文件项目结构说明请参见详细文档中的[项目结构](./DOCS.zh.md#项目结构)章节。

