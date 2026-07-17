# 面试知识点映射

## 为什么用 Milvus

向量数据库负责高维向量索引和相似度搜索。项目通过 VectorStore Protocol 隔离业务层与底层实现，
本地可以使用内存，演示和生产配置可以切换 Milvus。选择 Milvus 不是因为“RAG 必须用 Milvus”，
而是当数据规模、并发和向量索引能力超过关系数据库经济边界时，它更适合作为独立检索基础设施。

## Embedding 维度为什么不能随意修改

Collection Schema 在创建时固定向量维度。更换 Embedding 模型后，维度和向量空间通常都会改变，
即使维度相同也不能混用。因此需要创建新 Collection、重新向量化、双写验证，再切换 Alias。

## Top-K 如何确定

Top-K 不是经验常数。项目通过离线数据集比较 Recall@K、MRR、重排后命中率、上下文 Token 和延迟。
召回 K 可以较大，进入模型的 Final K 应较小，避免无关上下文稀释答案。

## 为什么需要混合检索

Dense 擅长同义表达，BM25 擅长专有名词、编号和精确关键词。企业文档同时包含自然语言和大量精确
标识，因此两路召回后使用 RRF 融合，通常比单路更稳定。

## 用了 Milvus，为什么还需要单独的 BM25

原项目的 Milvus Schema 只有 Dense `FLOAT_VECTOR`，所以 Milvus 当时没有承担全文检索。当前本地
模式使用 SQLite FTS5 持久倒排索引，不再在每次查询时全量扫描 Chunk。生产可选择 Milvus 原生
BM25，或者在需要复杂中文分词、同义词和字段权重时使用 OpenSearch；OpenSearch 不是必选项。

## 项目是否完全没有使用开源框架

不是。项目使用 FastAPI、Milvus/PyMilvus、Pydantic、HTTPX、PyPDF、python-docx 和 Docker
Compose。自己实现的是 RAG 编排、Tokenize、RRF、轻量重排、权限表达式、Agent 状态流和 SSE 事件。
这样做方便解释核心机制，但如果流程规模继续扩大，可以把编排迁移到 LangGraph。

## 企业 RAG 与普通 Demo 的区别

- 文档版本和增量更新。
- 多租户与权限前置过滤。
- 引用溯源和无答案拒答。
- 离线评测而非凭感觉调参。
- 审计、幂等、超时、重试和异常补偿。
- 向量库、业务库和对象存储职责分离。

## Agent 为什么不能直接访问数据库

LLM 只负责选择受控工具和生成结构化参数。工具服务必须再次执行参数校验、权限校验、超时控制、
幂等和审计。不能把数据库凭证或任意 SQL 能力暴露给模型。
