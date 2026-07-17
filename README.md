# RAGOps Hub

面向求职展示和工程学习的多租户企业知识库 RAG + 客服 Agent。项目强调可解释的工程取舍：
权限前置过滤、Hybrid Retrieval、引用溯源、有副作用工具确认、幂等与审计，而不是只做一个聊天页面。

## 能力概览

- 多格式文档：PDF、DOCX、Markdown、TXT。
- 文档生命周期：SHA-256 去重、版本、状态、增量删除。
- Hybrid RAG：Milvus Dense + SQLite FTS5/BM25 + RRF + 轻量 Rerank。
- 向量后端：零依赖内存模式 / Milvus Standalone。
- 多租户：Tenant、Department、Owner、Public/Department/Private 可见范围。
- Agent：知识问答、订单查询、工单创建、Human-in-the-loop。
- 安全：Prompt Injection 规则防线、工具权限校验、工单幂等和审计。
- SSE：结构化检索、工具、Token、引用和结束事件。
- 模型：离线 Hash Embedding + 抽取式回答，或 OpenAI-compatible API。
- 评测：Source Recall@K、MRR 和检索延迟。

## 架构

```text
Web / API Client
       |
       v
FastAPI + Principal(X-Tenant/User/Department)
       |
       v
Prompt Guard -> Intent Router
       |             |                    |
       |             |                    +-> Ticket Tool -> Confirm -> Idempotent Write
       |             +-> Order Tool -> Owner Check -> Audit
       v
Hybrid Retriever
  |          |
  v          v
Dense      FTS5/BM25
Milvus     SQLite Persistent Inverted Index
  \          /
   RRF Fusion -> Lightweight Rerank -> Grounded Answer -> SSE + Citations
```

更完整的设计解释见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，面试知识点见
[docs/INTERVIEW_KNOWLEDGE.md](docs/INTERVIEW_KNOWLEDGE.md)，逐步实现逻辑见
[docs/IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md)。设计审查与修复记录见
[docs/DESIGN_REVIEW.md](docs/DESIGN_REVIEW.md)，完整口语化问答见
[docs/INTERVIEW_QA.md](docs/INTERVIEW_QA.md)。

## 1. 最快启动：离线演示模式

该模式不需要 Docker、Milvus 或模型密钥。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/python -m scripts.bootstrap_demo
.venv/bin/uvicorn app.main:app --reload
```

访问：

- 演示页面：http://127.0.0.1:8000
- Swagger：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/v1/health

演示订单：`ORD-1001`，默认身份为：

```text
Tenant: demo-company
User: demo-user
Department: customer-service
```

注意：内存向量模式本身不持久化 Dense 向量，但业务 Chunk 和 FTS5 索引保存在 SQLite。应用每次
启动时会读取所有 `ready` Chunk、重新生成 Embedding 并装载内存向量库，因此运行一次
`python -m scripts.bootstrap_demo` 完成样例入库后，后续只需重启应用。

## 2. Milvus Standalone 模式

Mac 16GB 建议只启动 Milvus 依赖，FastAPI 在宿主机运行：

```bash
docker compose up -d etcd minio milvus
```

`.env` 修改：

```dotenv
VECTOR_BACKEND=milvus
MILVUS_URI=http://localhost:19530
EMBEDDING_PROVIDER=hash
EMBEDDING_DIMENSION=384
LLM_ENABLED=false
```

然后：

```bash
.venv/bin/python -m scripts.bootstrap_demo
.venv/bin/uvicorn app.main:app --reload
```

Milvus WebUI：http://127.0.0.1:9091/webui/  
MinIO Console：http://127.0.0.1:19001（宿主机使用 19001，避免常见的 9001 端口冲突）

完整容器模式会自动启动 etcd、MinIO、Milvus，执行样例知识入库，然后启动 API：

```bash
docker compose --profile full up -d --build
```

查看状态：

```bash
docker compose --profile full ps
docker compose --profile full logs bootstrap app
```

应用健康后可访问演示页面 `http://127.0.0.1:8000`、Swagger `http://127.0.0.1:8000/docs`、
Milvus WebUI `http://127.0.0.1:9091/webui/` 和 MinIO Console `http://127.0.0.1:19001`。

停止完整环境：

```bash
docker compose --profile full down
```

## 3. 接入真实模型

任何支持 OpenAI-compatible API 的服务都可以使用：

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_DIMENSION=1024
EMBEDDING_MODEL=your-embedding-model
LLM_ENABLED=true
CHAT_MODEL=your-chat-model
OPENAI_BASE_URL=https://your-endpoint/v1
OPENAI_API_KEY=your-key
```

切换 Embedding 模型前必须更换 Milvus Collection 名称并重新入库，不能把不同向量空间的数据混在
同一个 Collection 中。

## 4. API 示例

上传文档：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents \
  -H 'X-Tenant-ID: demo-company' \
  -H 'X-User-ID: demo-user' \
  -H 'X-Department-ID: customer-service' \
  -F 'visibility=department' \
  -F 'version=1' \
  -F 'file=@samples/knowledge/refund-policy.md'
```

直接检索：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-ID: demo-company' \
  -H 'X-User-ID: demo-user' \
  -H 'X-Department-ID: customer-service' \
  -d '{"query":"首次购买后多久能退款"}'
```

SSE Agent：

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-ID: demo-company' \
  -H 'X-User-ID: demo-user' \
  -H 'X-Department-ID: customer-service' \
  -d '{"message":"查询订单 ORD-1001","conversation_id":"demo-c1"}'
```

工单流程：

```text
1. 发送“产品无法登录，请帮我创建工单”
2. Agent 返回 human_confirmation_required
3. 同一 conversation_id 发送“确认”
4. Agent 使用幂等键创建工单并返回编号
```

## 5. 运行评测

先在同一个进程使用 Milvus 模式完成样例入库，或在测试脚本中使用本地容器：

```bash
.venv/bin/python -m scripts.evaluate
```

输出包括 Source Recall@K、MRR、平均检索延迟和每题排名。样例只有 5 题，正式面试展示应扩展到
至少 50 题，并覆盖事实题、同义改写、术语、无答案和越权问题。

## 6. 测试

```bash
.venv/bin/pytest -q
.venv/bin/ruff check app tests scripts
```

测试覆盖：结构化 Chunk、Prompt Injection、租户隔离、私有文档 ACL、会话归属、双写补偿、
混合检索回归、订单越权边界和工单二次确认。

## 7. 项目目录

```text
app/
  agent/       意图路由、Workflow 和受控工具
  api/         FastAPI、身份依赖、SSE 和 Schema
  core/        配置
  domain/      领域实体
  embeddings/  离线与 OpenAI-compatible Embedding
  llm/         抽取式与真实 LLM 回答
  rag/         解析、Chunk、入库、BM25、RRF、Rerank
  security/    Prompt Injection 防线
  storage/     SQLite 与 Milvus 适配器
docs/          架构和面试知识点
frontend/      最小演示页面
samples/       示例知识和评测集
scripts/       入库与评测
tests/         自动化测试
```

## 8. 生产化差距

该仓库是企业工程样板，不冒充真实大规模生产系统。进一步生产化需要：

- 生产使用 OIDC/JWKS 替换演示 Header 和示例 HS256 JWT。
- PostgreSQL 替换 SQLite，并增加迁移工具和连接池。
- 对象存储、病毒扫描、PII 检测和异步解析队列。
- 在现有补偿和孤立向量过滤基础上增加完整 Outbox 与一致性巡检。
- 专业 Reranker、模型路由、限流、熔断、Token 配额。
- OpenTelemetry、Prometheus、结构化日志和告警。
- SSE 心跳、断线续传、取消和代理超时。
- 线上评测、人工反馈和知识库回滚。

这些差距被明确列出，是为了面试时能诚实地区分“已实现”“技术验证”和“生产方案”。
