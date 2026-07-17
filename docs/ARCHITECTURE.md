# 架构与实现逻辑

## 1. 系统边界

RAGOps Hub 面向 B2B SaaS 售后客服，分为七层：

1. 体验层：客服工作台、知识运营和运行评测。
2. API 层：客户会话、HTTP、文件上传、SSE 协议和 Demo Header/JWT 身份解析。
3. Agent 层：安全检查、Case 会话记忆、意图路由、RAG 分支、工具分支和人工确认。
4. RAG 层：解析、结构化切分、Embedding、Milvus Dense、FTS5/BM25、RRF 和重排。
5. 存储层：SQLite 保存客户、订单、Case、工单、历史处理结果和知识元数据，Milvus 或内存适配器保存向量。
6. 模型层：离线 Hash Embedding/抽取式回答，或 OpenAI-compatible API。
7. 可观测层：结构化 SSE 事件、耗时、引用和审计日志。

## 2. 多租户安全

演示模式使用身份 Header，JWT 模式从签名 Claims 中读取 tenant、department、user 和 roles。
所有业务表都保存 `tenant_id`。向量检索不是“先搜再过滤”，而是在 Milvus Filter Expression 中
先约束：

```text
tenant_id == current_tenant AND (
  visibility == public OR
  (visibility == department AND department_id == current_department) OR
  (visibility == private AND owner_user_id == current_user)
)
```

这种设计避免 Top-K 被无权数据占满，也降低数据泄漏风险。Dense 结果还会与关系库中的 `ready`
Chunk 做存在性校验，避免双写失败留下的孤立向量进入结果。文档列表和删除复用 owner ACL。

## 3. 文档生命周期

1. 计算 SHA-256，用于同租户内容去重。
2. 创建 processing 状态的文档元数据。
3. 按文件类型解析 PDF、Word、Markdown 或文本。
4. 清洗控制字符和多余空白。
5. 优先按标题和段落切分，超长段落再按句子切分。
6. 批量生成 Embedding。
7. 向量先写入 Vector Store，元数据与 Chunk 再提交；元数据失败时补偿删除向量。
8. 成功标记 ready，异常标记 failed。
9. 删除先标记 `deleting`，向量删除成功后再删除业务 Chunk。

生产增强项：Outbox/Saga、一致性补偿任务、病毒扫描、PII 检测和异步任务队列。

## 4. Hybrid Retrieval

- Dense：捕获语义相似度。
- FTS5/BM25：持久倒排索引捕获订单号、产品名、专业术语和精确字面匹配。
- RRF：对两路排名进行无量纲融合，避免直接比较不可比的原始分数。
- 轻量重排：当前实现结合 Query Token Overlap 与归一化 BM25 分数，避免短语料中强关键词结果被
  双路候选挤出；生产配置可替换为 BGE Reranker。
- Citation：最终结果保留来源、标题、Chunk 位置、融合分数和重排分数。

## 5. Agent 状态流

```text
message_start
  -> guardrail
  -> memory_loaded: summary + recent messages + case context
  -> intent_classified
     -> knowledge: retrieval -> generation -> citations
     -> query_order: tool -> result
     -> create_ticket: prepare -> human_confirmation_required
     -> confirm_ticket: idempotent tool execution
  -> memory_updated: persist message + refresh summary
  -> message_end
```

普通客户身份查询订单时使用租户和订单所有人校验；客服身份还必须具备支持角色，并且订单关联的
Support Case 已分配给当前客服。主管和管理员可以访问本租户业务对象。工单工具使用二次确认和
幂等键，体现有副作用工具与只读工具的不同治理方式。Pending Action 与 tenant、user、conversation
绑定，其他用户不能覆盖。创建成功后，Ticket 会回写到 Support Case，并关联 Customer 与 Order。

## 6. Case 级会话记忆

`ConversationMemoryService` 将记忆限制在当前 Case 和当前 Principal：

- 最近消息使用滑动窗口，默认加载 8 条。
- 消息达到默认 12 条后，把窗口之前的内容压缩成确定性摘要。
- 只有疑似追问才扩展检索 Query，避免历史内容造成 Query Drift。
- 生成上下文包含历史摘要、最近消息和 Case 业务上下文，但这些内容不能覆盖系统安全规则。
- 客户、订单和套餐仍从业务表读取，摘要不是业务事实真源。
- 创建工单时冻结业务上下文、摘要和最近对话，写入 `handoff_summary` 供人工接管。
- 读取和更新同时匹配 conversation、tenant、user；Case 还必须通过 assignee 校验。

该设计不建立跨 Case 的自由长期用户画像。生产还需增加数据保留期限、PII 脱敏、删除接口、摘要
重算和客服转派策略。

## 7. 客户历史工单与相似问题

历史工单属于受控业务数据，不属于模型长期记忆。系统先验证当前客服对 Support Case 的分配权限，
再以 `tenant_id + customer_user_id` 读取同一客户的历史 Ticket，排除当前 Case。`tickets_fts` 对主题、
描述和已记录的 `resolution` 建立持久倒排索引，用于推荐相似问题：

- 工作台加载时以当前 Case 的主题和概况查询相似 Ticket。
- Agent 处理知识问题时把相似 Ticket 作为低信任参考上下文。
- 客户、订单、套餐和当前状态仍以业务表实时查询为准。
- 历史查询结果不会跨客户、跨租户或绕过 Case assignee 规则。

生产环境应从 Helpdesk 同步历史 Ticket，并补充字段脱敏、TTL、删除权、处理结果质量标注和效果评测。

## 8. 客服工作台上下文

工作台不是通用聊天页面，而是围绕 Support Case 组织：

```text
Case Queue -> Customer + Order + Entitlement -> RAG / Tool -> Reply -> Ticket Confirmation
```

队列按租户和 assignee 过滤；右侧上下文来自 CRM/订单领域数据；中间对话只消费结构化 Agent 事件。
本地实现使用 SQLite 适配器，生产可替换为 CRM、订单/计费和 Helpdesk 连接器。

## 9. SSE 事件协议

事件类型包括 `message_start`、`memory_loaded`、`memory_updated`、`customer_history_loaded`、`intent_classified`、`retrieval_start`、
`retrieval_finished`、`tool_start`、`tool_finished`、`human_confirmation_required`、
`text_delta`、`citation`、`guard_blocked`、`error` 和 `message_end`。

生产部署需关闭 Nginx buffering，增加心跳、断线恢复、任务取消和客户端背压处理。
