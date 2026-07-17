# RAGOps Hub 设计审查与修复记录

## 1. 本轮已经修复

- 身份模式分离：`AUTH_MODE=demo` 允许演示身份头，`AUTH_MODE=jwt` 从签名 Token 构造 Principal。
- Principal ID 格式校验，降低构造检索表达式的风险。
- `documents` 增加 `owner_user_id`，私有文档列表和删除统一执行 owner/管理员校验。
- Conversation 更新必须同时匹配 tenant 和 user，禁止其他用户覆盖 Pending Action。
- Pending Action 在工单成功写入后才清理，并使用 `action_id` 生成幂等键。
- 工单创建改为数据库原子 `INSERT OR IGNORE`，避免多 Worker 的先查后写竞态。
- 文档入库在元数据提交失败时补偿删除已写入的向量。
- 文档删除先标记 `deleting`，向量删除成功后再删除关系数据。
- Dense 召回结果使用关系库做存在性校验，孤立向量不会进入最终候选。
- Sparse Retrieval 改为 SQLite FTS5 持久倒排索引，不再每次扫描并重新分词全部 Chunk。
- 增加 Dense/词项证据门槛，无可靠候选时进入拒答。
- 检索结果中的高风险指令 Chunk 会被过滤，RAG 上下文明确标记为不可信数据。
- 修复结构化 Chunker 可能输出超过 `chunk_size` 的问题。
- 内存向量模式在重复 bootstrap 时从 SQLite Chunk 重新生成向量。
- Milvus I/O 放入线程，避免同步 SDK 直接阻塞事件循环。
- Milvus 新 Collection 保存 `document_version`，并在启动时检查向量维度。
- 上传增加大小限制，SSE 错误不再返回内部异常文本。
- 健康检查实际检查 SQLite 和 Vector Store。

## 2. BM25、Milvus 和 OpenSearch 的关系

Milvus 是向量数据库，但是否能执行 BM25 取决于 Collection 是否配置了 Sparse Vector、Analyzer 和
BM25 Function。原项目只创建了一个 `FLOAT_VECTOR` 字段，所以 Milvus 当时只承担 Dense Retrieval。
BM25 在 Python 中读取全部可访问 Chunk 后临时计算，因此存在 O(N) 全量扫描。

本轮选择 SQLite FTS5 作为本地 Sparse Retrieval：

- 不增加新的中间件。
- FTS5 保存持久倒排索引，查询时直接取 Top-K。
- 适合本地演示和中小规模部署。
- 可以继续在 SQL 中执行 tenant/department/owner 权限条件。

生产环境有两条合理路线：

1. **Milvus Dense + Milvus 原生 BM25**
   - 中间件更少。
   - Dense/Sparse 数据生命周期更统一。
   - 需要验证当前 Milvus/PyMilvus 版本、中文 Analyzer 和权限过滤能力。

2. **Milvus Dense + OpenSearch Sparse**
   - 适合复杂中文分词、同义词、字段权重、短语检索和运维团队已有搜索平台的情况。
   - 代价是增加一个服务和新的双写一致性问题。

所以使用 OpenSearch 不是因为 Milvus 做不了，而是两者在全文检索成熟度和运维生态上的取舍。当前
项目为了快速部署，不额外引入 OpenSearch。

## 3. 仍然属于生产增强项

- JWT 示例当前使用共享密钥 HS256，企业环境更适合 OIDC/JWKS 和短期 Access Token。
- 文档解析虽然移入线程，但完整生产链路仍应使用异步任务队列。
- SQLite FTS5 适合本地和中小规模，百万级 Chunk 应迁移到 Milvus BM25 或 OpenSearch。
- 双写已有补偿和孤立向量过滤，但完整方案仍建议使用 Outbox、重试和一致性巡检。
- PDF 还不支持 OCR、版面分析、病毒扫描和 PII 检测。
- Prompt Injection 是分层降低风险，不应表述为完全解决。
- 评测集仍需从 5 题扩展到至少 50～100 题。
- SSE 仍可继续增加心跳、断线恢复、取消和代理超时。

## 4. 推荐生产演进路线

```text
FastAPI / API Gateway
        |
        +-- OIDC/JWT -> Trusted Principal
        |
        +-- PostgreSQL: metadata, ACL, workflow, audit, outbox
        |
        +-- Object Storage: original documents
        |
        +-- Worker Queue: parse, chunk, embed, index
                |
                +-- Milvus Dense + BM25
                |        or
                +-- Milvus Dense + OpenSearch Sparse
```
