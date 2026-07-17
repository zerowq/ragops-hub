from pathlib import Path

import pytest

from app.domain.models import Chunk, Principal, SearchHit
from app.embeddings.providers import HashEmbeddingProvider
from app.rag.retrieval import HybridRetriever
from app.storage.repository import SQLiteRepository
from app.storage.vector_store import InMemoryVectorStore


@pytest.mark.asyncio
async def test_hybrid_retrieval_respects_tenant(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "test.db")
    store = InMemoryVectorStore()
    embedder = HashEmbeddingProvider(64)
    principal = Principal("u1", "tenant-a", "support")

    def chunk(chunk_id: str, tenant: str, content: str) -> Chunk:
        return Chunk(
            id=chunk_id,
            tenant_id=tenant,
            department_id="support",
            document_id=f"doc-{chunk_id}",
            document_version=1,
            visibility="public",
            content=content,
            source=f"{chunk_id}.md",
            title=chunk_id,
            position=0,
        )

    allowed = chunk("a", "tenant-a", "企业退款期限为七天")
    forbidden = chunk("b", "tenant-b", "企业退款期限为三十天")
    allowed.embedding, forbidden.embedding = await embedder.embed([allowed.content, forbidden.content])
    await store.upsert([allowed, forbidden])

    # BM25 repository rows are intentionally omitted; dense retrieval still verifies tenant filtering.
    retriever = HybridRetriever(repository, store, embedder, top_k_final=5)
    hits = await retriever.search("退款期限", principal)
    assert [hit.chunk.id for hit in hits] == ["a"]


@pytest.mark.asyncio
async def test_light_rerank_keeps_strong_sparse_match_when_dense_misses(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "test.db")
    embedder = HashEmbeddingProvider(64)
    principal = Principal("u1", "tenant-a", "support")
    target_text = "系统支持 PDF、Word、Markdown 和纯文本文件，用户可以上传文件。"
    distractor_texts = [
        "安全系统支持部门权限过滤。",
        "文档系统采用部门访问控制。",
        "退款申请需要上传付款凭证。",
        "审核文件由客服人员处理。",
        "企业版本支持订单查询。",
        "管理员可以设置文件权限。",
        "知识库使用向量检索。",
        "客服系统支持创建工单。",
    ]

    chunks: list[Chunk] = []
    for index, content in enumerate([target_text, *distractor_texts]):
        document_id = repository.create_document(
            tenant_id="tenant-a",
            department_id="support",
            title=f"doc-{index}",
            source=f"doc-{index}.md",
            visibility="public",
            version=1,
            content_hash=f"hash-{index}",
        )
        chunk = Chunk(
            id=f"chunk-{index}",
            tenant_id="tenant-a",
            department_id="support",
            document_id=document_id,
            document_version=1,
            visibility="public",
            content=content,
            source=f"doc-{index}.md",
            title=f"doc-{index}",
            position=0,
        )
        repository.save_chunks([chunk])
        chunks.append(chunk)

    class DenseMissStore:
        async def search(self, vector: list[float], principal: Principal, limit: int) -> list[SearchHit]:
            return [
                SearchHit(chunk=chunk, score=1 / rank, dense_rank=rank)
                for rank, chunk in enumerate(chunks[1 : limit + 1], start=1)
            ]

    retriever = HybridRetriever(repository, DenseMissStore(), embedder, top_k_final=5)
    hits = await retriever.search("上传文件支持哪些格式？", principal)

    assert hits[0].chunk.id == "chunk-0"
