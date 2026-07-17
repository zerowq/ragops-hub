# 架构与实现逻辑

## 1. 系统边界

RAGOps Hub 分为六层：

1. API 层：HTTP、文件上传、SSE 协议和身份头解析。
2. Agent 层：安全检查、意图路由、RAG 分支、工具分支和人工确认。
3. RAG 层：解析、结构化切分、Embedding、Dense/BM25、RRF 和重排。
4. 存储层：SQLite 保存业务元数据，Milvus 或内存适配器保存向量。
5. 模型层：离线 Hash Embedding/抽取式回答，或 OpenAI-compatible API。
6. 可观测层：结构化 SSE 事件、耗时、引用和审计日志。

## 2. 多租户安全

`X-Tenant-ID`、`X-Department-ID` 和 `X-User-ID` 组成当前 Principal。所有业务表都保存
`tenant_id`。向量检索不是“先搜再过滤”，而是在 Milvus Filter Expression 中先约束：

```text
tenant_id == current_tenant AND (
  visibility == public OR
  (visibility == department AND department_id == current_department) OR
  (visibility == private AND owner_user_id == current_user)
)
```

这种设计避免 Top-K 被无权数据占满，也降低数据泄漏风险。真实生产环境应由 JWT/SSO 生成
Principal，不能信任客户端直接传入身份头。

## 3. 文档生命周期

1. 计算 SHA-256，用于同租户内容去重。
2. 创建 processing 状态的文档元数据。
3. 按文件类型解析 PDF、Word、Markdown 或文本。
4. 清洗控制字符和多余空白。
5. 优先按标题和段落切分，超长段落再按句子切分。
6. 批量生成 Embedding。
7. 向量先写入 Vector Store，元数据与 Chunk 再提交到关系库。
8. 成功标记 ready，异常标记 failed。
9. 删除文档时同时删除业务 Chunk 和向量。

生产增强项：Outbox/Saga、一致性补偿任务、病毒扫描、PII 检测和异步任务队列。

## 4. Hybrid Retrieval

- Dense：捕获语义相似度。
- BM25：捕获订单号、产品名、专业术语和精确字面匹配。
- RRF：对两路排名进行无量纲融合，避免直接比较不可比的原始分数。
- 轻量重排：当前实现结合 Query Token Overlap 与归一化 BM25 分数，避免短语料中强关键词结果被
  双路候选挤出；生产配置可替换为 BGE Reranker。
- Citation：最终结果保留来源、标题、Chunk 位置、融合分数和重排分数。

## 5. Agent 状态流

```text
message_start
  -> guardrail
  -> intent_classified
     -> knowledge: retrieval -> generation -> citations
     -> query_order: tool -> result
     -> create_ticket: prepare -> human_confirmation_required
     -> confirm_ticket: idempotent tool execution
  -> message_end
```

订单工具按租户和用户查询，防止水平越权。工单工具使用二次确认和幂等键，体现有副作用工具
与只读工具的不同治理方式。

## 6. SSE 事件协议

事件类型包括 `message_start`、`intent_classified`、`retrieval_start`、
`retrieval_finished`、`tool_start`、`tool_finished`、`human_confirmation_required`、
`text_delta`、`citation`、`guard_blocked`、`error` 和 `message_end`。

生产部署需关闭 Nginx buffering，增加心跳、断线恢复、任务取消和客户端背压处理。
