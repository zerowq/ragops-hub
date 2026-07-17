# 分步实现说明

## 第一步：定义身份和数据边界

先定义 `Principal(user_id, tenant_id, department_id, roles)`，所有文档、Chunk、订单、工单、会话和
审计记录都携带 tenant_id。这样多租户不是后补字段，而是系统的基本不变量。

知识点：身份上下文、水平越权、最小权限、租户隔离。

## 第二步：建立可替换基础设施

EmbeddingProvider 和 VectorStore 使用 Protocol 抽象。本地默认 HashEmbedding + InMemoryVectorStore，
真实模式切换 OpenAI-compatible Embedding + Milvus。业务代码不依赖某个具体数据库 SDK。

知识点：依赖倒置、适配器模式、离线可测试性、Embedding 维度约束。

## 第三步：文档处理流水线

解析器按扩展名处理 TXT、Markdown、PDF 和 DOCX；Chunker 先识别标题/章节，再按段落与句子切分；
IngestionService 负责哈希去重、状态、Embedding、向量写入、元数据提交和失败标记。

知识点：结构化切分、Chunk Overlap、增量索引、双写一致性、文档版本。

## 第四步：Hybrid Retrieval

Dense Search 从向量后端召回；Sparse Search 从 SQLite FTS5 持久倒排索引召回；RRF 根据排名融合；最后结合
Query Token Overlap 与归一化 BM25 分数做可解释的轻量重排。生产模式可直接替换 Cross-Encoder
Reranker。

知识点：语义检索、FTS5/BM25、倒排检索、RRF、Recall@K、MRR、Reranker。

## 第五步：检索前权限过滤

内存后端和 Milvus 后端都在候选召回阶段执行 Tenant、Department、Visibility 与 Owner 条件，而非
取回 Top-K 后再过滤。

知识点：Metadata Filter、Top-K 污染、数据泄漏风险、ABAC。

## 第六步：Agent 工作流

Agent 先经过 Prompt Injection 检查，再进行意图路由。知识问题进入 RAG；订单问题进入只读工具；
工单请求先保存 Pending Action，用户确认后才执行写工具。

知识点：确定性工作流、Tool Calling、Human-in-the-loop、副作用治理。

## 第七步：工具安全

普通用户查询订单同时使用 order_id、tenant_id 和 user_id；客服查询还校验 support role 和已分配
Support Case，防止通过修改订单号读取未授权客户数据。每个待确认动作有服务端 action_id，工单使用
action_id 生成幂等键并执行原子写入，同时关联 Case、Customer 和 Order。

知识点：对象级权限、幂等、审计、参数校验、工具最小权限。

## 第八步：SSE 协议

后端将 Agent 生命周期编码为具名 SSE 事件，而非只发送字符串。前端根据 event 类型分别展示检索、
工具、Token、引用和错误。

知识点：`text/event-stream`、POST 流式响应、代理缓冲、客户端断开、结构化事件。

## 第九步：离线评测

JSONL 数据集保存问题、预期来源和关键术语。脚本计算 Source Recall@K、MRR 和平均检索耗时，并输出
每题排名，便于比较 Dense、BM25、Hybrid 和 Reranker。

知识点：可重复评测、基线、检索指标、错误分析、参数选择。

## 第十步：双运行模式和交付

离线模式不需要外部服务，方便开发测试；Docker Compose 模式启动 etcd、MinIO、Milvus 和可选应用
容器，模拟独立向量服务、持久化和健康检查。

知识点：开发/生产配置分离、容器编排、健康检查、持久化卷、资源规划。
