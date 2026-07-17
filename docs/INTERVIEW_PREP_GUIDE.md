# RAGOps Hub 面试准备手册

这份手册用于把项目讲成一个“可验证的企业 RAG 工程系统”，而不是只介绍聊天页面。

## 一、项目一句话定位

我独立设计并实现了一个多租户企业知识库 RAG + 客服 Agent：支持多格式文档入库、权限前置的 Dense/BM25 混合检索、引用回答、订单只读查询、工单确认与幂等写入，并通过 SSE、离线评测和自动化测试验证核心链路。

面试时要主动说明：这是独立设计的工程项目/技术验证，不表述为已经承载大型生产业务。

## 二、完整知识清单

### 1. 系统设计与工程分层

- 分层架构：API、Agent、RAG、Storage、Model、Security/Observability。
- 高内聚低耦合、依赖倒置、Protocol/接口抽象、适配器模式。
- 领域模型：Principal、Chunk、SearchHit、Intent、AgentEvent。
- 配置管理：环境变量、开发/离线/Milvus/真实模型配置切换。
- 有状态服务：会话、Pending Action、消息和审计记录。
- 失败边界：处理状态、失败状态、异常传播和资源清理。
- 可测试性：内存向量库、离线 Embedding、依赖注入和临时数据库。

### 2. 多租户、身份与权限

- Tenant/Department/User 三层身份上下文。
- 行级权限和对象级权限：订单必须同时匹配 tenant_id + user_id。
- ABAC 思路：public、department、private + 当前部门/用户。
- 权限前置过滤：在 Milvus filter 和 SQL 查询中召回前限制范围。
- Top-K 污染：先召回再过滤可能导致无权数据占满候选集。
- 水平越权、跨租户数据泄漏、最小权限、服务端二次校验。
- 当前项目限制：身份来自请求头，仅用于演示；生产应使用 JWT/SSO 生成可信 Principal。

### 3. 文档入库与数据生命周期

- TXT/Markdown/PDF/DOCX 解析，表格文本抽取。
- 清洗控制字符、空白和不可检索内容。
- 结构感知切分：标题、章节、段落、句子、超长文本。
- Chunk size、overlap 的召回/上下文/成本权衡。
- SHA-256 内容哈希去重、文档版本、增量索引。
- 文档 processing/ready/failed 生命周期。
- Embedding 批量生成、向量库与关系库双写。
- 删除时同时删除 Chunk 和向量。
- 生产增强：对象存储、病毒扫描、PII 检测、异步队列、Outbox/Saga 和补偿任务。

### 4. Embedding 与向量检索

- Embedding 把文本映射到向量空间；语义相似度与字面相似度的区别。
- Cosine similarity、向量归一化和维度一致性。
- Hash Embedding 只用于离线演示/测试，不等同于真实语义模型。
- OpenAI-compatible Embedding API、超时、异常和返回顺序。
- Milvus collection schema、FLOAT_VECTOR、COSINE、HNSW。
- HNSW 的 M、efConstruction、efSearch 对精度/内存/延迟的影响。
- 更换模型时不能混用同一 collection；需重新向量化、验证并切换 Alias。

### 5. Hybrid Retrieval

- Dense Retrieval：同义表达、语义召回。
- 倒排检索与 BM25：产品名、订单号、术语和精确关键词。
- BM25 的 TF、IDF、文档长度归一化、k1 和 b。
- 中文分词的简化处理：单字 + 中文 bigram；生产可换专业分词器。
- RRF：按排名融合，避免直接比较 Dense 与 BM25 不可比的原始分数。
- 召回 Top-K 与最终上下文 Final-K 的分离。
- 轻量重排：Query Token Overlap + 归一化 BM25，解决短语料强关键词被挤出。
- 生产可替换 Cross-Encoder/BGE Reranker，但要评估吞吐与延迟。
- Citation：source、title、chunk position、融合分数、重排分数。

### 6. RAG 生成与幻觉控制

- 检索增强生成的基本链路：Query → Retrieve → Rerank → Context → Generate。
- 无命中时拒答，避免“看起来合理”的编造。
- 抽取式回答用于离线演示；真实 LLM 用受控上下文和低 temperature。
- System Prompt 约束：只能依据上下文、无依据明确拒答、引用来源。
- Context 长度、上下文稀释、上下文注入和来源可信度。
- 引用不是事实正确性的充分证明，仍需要数据集和人工评估。

### 7. Agent 与工具安全

- 确定性 Intent Router：knowledge、query_order、create_ticket、confirm_ticket。
- Agent 状态流：安全检查 → 意图识别 → RAG/工具 → 生成 → 结束。
- LLM 只负责理解/选择受控工具，不直接执行 SQL 或访问数据库。
- 工具参数校验、权限校验、超时、审计、错误返回。
- 只读工具和有副作用工具的治理差异。
- Human-in-the-loop：工单先 prepare，用户明确确认后 create。
- Pending Action 与 conversation_id、tenant_id、user_id 绑定。
- 幂等键：租户 + 用户 + 会话 + 操作内容哈希；数据库唯一约束兜底。
- Prompt Injection 规则防线、输入长度限制、风险分数。
- 当前限制：规则防护不是完整安全方案，生产需策略服务/分类器、输出过滤和工具沙箱。

### 8. API、SSE 与可观测性

- FastAPI 路由、依赖注入、UploadFile、Schema、HTTP 状态码。
- SSE 的 event/data 格式与 `text/event-stream`。
- 结构化事件：message_start、intent_classified、retrieval_*、tool_*、citation、error、message_end。
- 流式 Token、工具状态和引用的前端增量展示。
- Nginx buffering、心跳、客户端断开、取消、背压和断线恢复。
- 延迟拆分：检索耗时、总链路耗时、工具耗时。
- 审计日志和可追溯性。
- 生产增强：OpenTelemetry、Prometheus、结构化日志、告警和 trace_id。

### 9. 数据库、并发与一致性

- SQLite 关系模型、外键、WAL、唯一约束和事务。
- documents/chunks/orders/tickets/conversations/messages/audit_logs 的职责。
- 关系库保存业务元数据，向量库保存向量和检索字段。
- 向量库与关系库双写不是天然原子操作；当前项目有失败状态，但缺少完整补偿机制。
- Pending Action 弹出和工单创建的竞态问题；生产需事务、锁或状态机保证一次性消费。
- 幂等解决重复请求，不等于解决所有并发一致性问题。
- 生产迁移到 PostgreSQL、连接池、迁移工具和 Outbox。

### 10. 评测、测试与交付

- Source Recall@K：目标来源是否出现在前 K。
- MRR：正确来源排名越靠前越好。
- 延迟：平均值只是基线，生产还应关注 P95/P99。
- 评测集应覆盖事实题、同义改写、编号/术语、无答案、越权问题。
- 单元测试、集成测试、API/SSE 测试、安全边界测试、回归测试。
- 当前 5 题、10 passed 只能证明基线链路正确，不能代表生产效果。
- Docker Compose、健康检查、持久化卷、资源规划和开发环境复现。

## 三、最可能被问的问题与回答主线

### 高频基础题

1. **为什么做这个项目？**
   - 展示企业 RAG 中真正影响落地的能力：权限、混合检索、工具安全、可观测和评测，而不是只调用模型 API。

2. **项目整体链路是什么？**
   - Principal → Guardrail → Intent → RAG/Tool → Citation/Confirmation → SSE → Audit。

3. **为什么使用 Milvus？**
   - 它适合作为独立向量检索基础设施；项目用 VectorStore 抽象保留内存模式，避免业务代码绑定 Milvus。

4. **为什么不能只用向量检索？**
   - 向量对同义表达强，但订单号、产品名、版本号等精确词可能不稳定；BM25 补足字面匹配。

5. **RRF 为什么不直接加两个分数？**
   - 两路原始分数的量纲和分布不同；RRF 使用排名，降低分数不可比问题。

6. **Top-K 怎么定？**
   - 用离线 Recall@K、MRR、最终命中率、上下文长度和延迟共同决定；召回 K 可大，送模型的 Final-K 应小。

7. **Chunk 为什么要 overlap？**
   - 防止边界切断语义；但 overlap 太大增加重复、索引量和上下文噪声，需要用评测集调参。

8. **Embedding 模型换了怎么办？**
   - 新 collection 重新入库，校验维度和评测结果，双写或灰度验证后用 alias 切换，不能混用向量空间。

9. **如何防止跨租户检索？**
   - tenant_id 是所有业务实体的不变量；Dense 在 Milvus filter 前置限制，BM25 在 SQL 中只取可访问 Chunk，工具再次校验。

10. **为什么 Agent 不能直接访问数据库？**
    - 模型输出不可信；必须通过受控工具执行参数校验、对象权限、超时、幂等和审计。

11. **为什么创建工单要二次确认？**
    - 创建工单有外部副作用，避免误触、提示词误导和模型误判；先保存待执行动作，明确确认后才写入。

12. **如何避免重复创建工单？**
    - 生成确定性幂等键，并在 tickets 上建立 tenant_id + idempotency_key 唯一约束；重复请求返回已有工单。

13. **Prompt Injection 怎么防？**
    - 当前先做可解释的规则拦截和长度限制；同时通过工具隔离、最小权限、上下文边界和拒答策略降低影响。承认规则不是完整防线。

14. **SSE 为什么不只返回最终文本？**
    - 企业场景需要展示检索、工具、确认、引用和错误状态，结构化事件比纯 Token 更可观测、可恢复和可审计。

15. **如何证明检索有效？**
    - 固定 JSONL 评测集，比较 Recall@K、MRR、延迟和每题排名；当前 5 题结果是基线，不夸大为生产效果。

### 深挖题

16. **向量库和关系库双写失败怎么办？**
    - 当前用 processing/failed 标记暴露失败状态；生产增加 Outbox、重试、补偿任务、版本状态和一致性巡检。

17. **用户确认后请求重放会怎样？**
    - 幂等键保证同一动作返回原工单；Pending Action 还应设计为原子消费，生产可用事务/状态机防并发双消费。

18. **为什么当前身份头不安全？**
    - 客户端可伪造 header；正式系统由网关校验 JWT/SSO，把 claims 映射为服务端 Principal，客户端不能自定义租户和用户。

19. **HNSW 参数怎么调？**
    - M 提高连接数和召回但增加内存；efConstruction 影响建库质量和构建时间；efSearch 提高召回但增加查询延迟，用 Recall/延迟曲线调。

20. **BM25 中文实现有什么问题？**
    - 单字和 bigram 是演示级方案，可能产生噪声；生产应使用中文分词、同义词、编号规范化，并通过领域数据评估。

21. **为什么不直接用 LangChain/LangGraph？**
    - 本项目重点是理解边界和控制流，因此采用轻量确定性编排；框架可以减少样板，但不能替代权限、幂等、评测和审计设计。

22. **如何处理无答案问题？**
    - 设相似度/重排阈值和证据充分性规则；没有可靠上下文就明确拒答或转人工，不让模型凭常识补全。

23. **如何做生产级监控？**
    - 记录 trace_id、tenant、intent、召回数量、命中来源、P50/P95/P99、模型 Token/错误率、工具成功率、确认转化率和拒答率；注意脱敏。

24. **这个项目离生产还差什么？**
    - JWT/SSO、PostgreSQL、对象存储与异步队列、双写补偿、专业重排、限流熔断、模型配额、观测告警、SSE 断线恢复、至少 50+ 题评测和人工反馈闭环。

## 四、面试讲项目的 90 秒版本

我做了一个多租户企业知识库 RAG 和客服 Agent。系统支持 PDF、Word、Markdown、TXT 入库，先做哈希去重、结构化切分和 Embedding，再进入 Milvus。检索不是单一向量检索，而是 Dense + BM25，通过 RRF 融合，再用词项覆盖率和归一化 BM25 做轻量重排。安全上，我把 tenant、department、user 放进数据模型，在向量召回和 SQL 召回阶段前置权限过滤，订单工具还会做用户归属校验。Agent 采用可解释的确定性路由：知识问题走 RAG，订单问题走只读工具，创建工单必须人工确认，并用幂等键和唯一约束避免重复写入。接口通过 SSE 返回检索、工具、引用、确认和结束事件。最后用 Recall@K、MRR、延迟和自动化测试验证基线。这个项目是独立设计的工程技术验证，生产化还需要可信身份、异步入库、双写补偿和更大评测集。

## 五、简历建议表述

**RAGOps Hub｜独立设计与实现**

- 设计并实现多租户企业知识库 RAG + 客服 Agent，支持 PDF/DOCX/Markdown/TXT 入库、SHA-256 去重、版本和引用溯源。
- 实现 Dense + BM25 + RRF + 轻量重排，并在 Milvus 召回阶段执行 tenant/department/user 权限过滤。
- 实现订单只读查询、工单人工确认、幂等写入和审计日志，降低水平越权与重复提交风险。
- 设计结构化 SSE 事件协议，覆盖检索、工具、确认、引用、错误和结束状态；使用 Recall@K、MRR、延迟及自动化测试验证核心链路。

不要写“生产承载”“准确率 100%”或“全面防御 Prompt Injection”。更准确的说法是“5 题离线基线中 Source Recall@K=1.0、MRR=1.0，10 项自动化测试通过”。

## 六、复习顺序

1. 先背 90 秒版本和整体时序。
2. 再掌握权限前置过滤、Hybrid Retrieval、RRF、Rerank。
3. 再掌握工单确认、幂等、审计和工具隔离。
4. 最后准备 Milvus 参数、SSE、双写一致性和生产化差距。
5. 每个模块都能回答“为什么这样设计、怎么验证、生产还缺什么”。
