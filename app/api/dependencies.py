from __future__ import annotations

from functools import lru_cache

from fastapi import Header

from app.agent.intent import IntentRouter
from app.agent.service import EnterpriseAgentService
from app.agent.tools import CustomerServiceTools
from app.core.config import get_settings
from app.domain.models import Principal
from app.embeddings.providers import HashEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from app.llm.generator import ExtractiveAnswerGenerator, OpenAICompatibleAnswerGenerator
from app.rag.chunking import StructureAwareChunker
from app.rag.ingestion import IngestionService
from app.rag.parsers import DocumentParser
from app.rag.retrieval import HybridRetriever
from app.security.guardrails import PromptInjectionGuard
from app.storage.repository import SQLiteRepository
from app.storage.vector_store import InMemoryVectorStore, MilvusVectorStore


class Container:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.repository = SQLiteRepository(settings.sqlite_path)
        if settings.embedding_provider == "openai":
            self.embedder = OpenAICompatibleEmbeddingProvider(
                settings.openai_base_url,
                settings.openai_api_key,
                settings.embedding_model,
                settings.embedding_dimension,
            )
        else:
            self.embedder = HashEmbeddingProvider(settings.embedding_dimension)

        if settings.vector_backend == "milvus":
            self.vector_store = MilvusVectorStore(
                settings.milvus_uri,
                settings.milvus_token,
                settings.milvus_collection,
                settings.embedding_dimension,
            )
        else:
            self.vector_store = InMemoryVectorStore()

        self.retriever = HybridRetriever(
            self.repository,
            self.vector_store,
            self.embedder,
            settings.top_k_dense,
            settings.top_k_sparse,
            settings.top_k_final,
            settings.rrf_k,
        )
        self.ingestion = IngestionService(
            self.repository,
            self.vector_store,
            self.embedder,
            StructureAwareChunker(settings.chunk_size, settings.chunk_overlap),
            DocumentParser(),
        )
        generator = (
            OpenAICompatibleAnswerGenerator(
                settings.openai_base_url, settings.openai_api_key, settings.chat_model
            )
            if settings.llm_enabled
            else ExtractiveAnswerGenerator()
        )
        self.tools = CustomerServiceTools(self.repository)
        self.agent = EnterpriseAgentService(
            self.repository,
            PromptInjectionGuard(),
            IntentRouter(),
            self.retriever,
            generator,
            self.tools,
        )


@lru_cache(maxsize=1)
def get_container() -> Container:
    return Container()


def get_principal(
    x_user_id: str = Header(default="demo-user"),
    x_tenant_id: str = Header(default="demo-company"),
    x_department_id: str = Header(default="customer-service"),
) -> Principal:
    return Principal(
        user_id=x_user_id,
        tenant_id=x_tenant_id,
        department_id=x_department_id,
    )

