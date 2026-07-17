from __future__ import annotations

import re
from functools import lru_cache

from fastapi import Header, HTTPException

from app.agent.intent import IntentRouter
from app.agent.memory import ConversationMemoryService
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
            settings.min_dense_score,
            settings.min_lexical_overlap,
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
        self.memory = ConversationMemoryService(
            self.repository,
            settings.memory_recent_messages,
            settings.memory_summary_trigger_messages,
            settings.memory_summary_max_chars,
        )
        self.agent = EnterpriseAgentService(
            self.repository,
            PromptInjectionGuard(),
            IntentRouter(),
            self.retriever,
            generator,
            self.tools,
            self.memory,
        )

    async def startup(self) -> int:
        """Restore vectors when the selected vector backend is non-persistent."""
        if self.vector_store.persistent:
            return 0
        chunks = self.repository.list_ready_chunks()
        if not chunks:
            return 0
        for offset in range(0, len(chunks), 64):
            batch = chunks[offset : offset + 64]
            vectors = await self.embedder.embed([chunk.content for chunk in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
            await self.vector_store.upsert(batch)
        return len(chunks)


@lru_cache(maxsize=1)
def get_container() -> Container:
    return Container()


def get_principal(
    authorization: str | None = Header(default=None),
    x_user_id: str = Header(default="demo-user"),
    x_tenant_id: str = Header(default="demo-company"),
    x_department_id: str = Header(default="customer-service"),
    x_roles: str = Header(default="employee"),
) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "jwt":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Bearer token is required")
        if not settings.jwt_secret:
            raise HTTPException(500, "JWT authentication is not configured")
        import jwt

        try:
            claims = jwt.decode(
                authorization.split(" ", 1)[1],
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={
                    "require": ["exp", "sub", "tenant_id", "department_id"],
                },
            )
            x_user_id = str(claims["sub"])
            x_tenant_id = str(claims["tenant_id"])
            x_department_id = str(claims["department_id"])
            claim_roles = claims.get("roles", ["employee"])
            roles = claim_roles if isinstance(claim_roles, list) else [str(claim_roles)]
        except (jwt.PyJWTError, KeyError) as error:
            raise HTTPException(401, "Invalid authentication token") from error
    elif settings.auth_mode == "demo":
        roles = [role.strip() for role in x_roles.split(",") if role.strip()]
    else:
        raise HTTPException(500, "Unsupported AUTH_MODE")

    identifier = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
    if not all(identifier.fullmatch(value) for value in (x_user_id, x_tenant_id, x_department_id)):
        raise HTTPException(400, "Invalid principal identifier")
    return Principal(
        user_id=x_user_id,
        tenant_id=x_tenant_id,
        department_id=x_department_id,
        roles=roles,
    )
