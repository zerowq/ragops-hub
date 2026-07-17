from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

from app.domain.models import Chunk, Principal
from app.embeddings.providers import EmbeddingProvider
from app.rag.chunking import StructureAwareChunker
from app.rag.parsers import DocumentParser
from app.storage.repository import DuplicateDocumentError, SQLiteRepository
from app.storage.vector_store import VectorStore


class IngestionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        chunker: StructureAwareChunker,
        parser: DocumentParser,
    ) -> None:
        self.repository = repository
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker
        self.parser = parser

    async def ingest_path(
        self,
        path: Path,
        principal: Principal,
        visibility: str = "department",
        version: int = 1,
        title: str | None = None,
    ) -> dict[str, object]:
        raw = await asyncio.to_thread(path.read_bytes)
        content_hash = hashlib.sha256(raw).hexdigest()
        if self.repository.document_exists(principal.tenant_id, content_hash, version):
            if not self.vector_store.persistent:
                existing_chunks = self.repository.get_ready_chunks_by_hash(
                    principal.tenant_id,
                    content_hash,
                    version,
                )
                if existing_chunks:
                    vectors = await self.embedder.embed(
                        [chunk.content for chunk in existing_chunks]
                    )
                    for chunk, vector in zip(existing_chunks, vectors, strict=True):
                        chunk.embedding = vector
                    await self.vector_store.upsert(existing_chunks)
                return {
                    "status": "duplicate",
                    "content_hash": content_hash,
                    "chunks": len(existing_chunks),
                    "vector_rehydrated": bool(existing_chunks),
                }
            return {"status": "duplicate", "content_hash": content_hash, "chunks": 0}
        try:
            document_id = self.repository.create_document(
                tenant_id=principal.tenant_id,
                department_id=principal.department_id,
                owner_user_id=principal.user_id,
                title=title or path.stem,
                source=path.name,
                visibility=visibility,
                version=version,
                content_hash=content_hash,
            )
        except DuplicateDocumentError:
            return {"status": "duplicate", "content_hash": content_hash, "chunks": 0}
        chunks: list[Chunk] = []
        vectors_written = False
        try:
            text = await asyncio.to_thread(self.parser.parse, path)
            parts = await asyncio.to_thread(self.chunker.split, text)
            if not parts:
                raise ValueError("No readable text was extracted from the document")
            vectors = await self.embedder.embed(parts)
            chunks = [
                Chunk(
                    id=str(uuid.uuid4()),
                    tenant_id=principal.tenant_id,
                    department_id=principal.department_id,
                    document_id=document_id,
                    document_version=version,
                    visibility=visibility,
                    content=content,
                    source=path.name,
                    title=title or path.stem,
                    position=position,
                    embedding=vectors[position],
                    metadata={
                        "owner_user_id": principal.user_id,
                        "content_hash": content_hash,
                    },
                )
                for position, content in enumerate(parts)
            ]
            await self.vector_store.upsert(chunks)
            vectors_written = True
            self.repository.save_chunks(chunks)
            self.repository.audit(
                principal,
                "document.ingest",
                "document",
                document_id,
                {"chunks": len(chunks), "source": path.name},
            )
            return {
                "status": "ready",
                "document_id": document_id,
                "content_hash": content_hash,
                "chunks": len(chunks),
            }
        except Exception:
            if vectors_written and chunks:
                try:
                    await self.vector_store.delete([chunk.id for chunk in chunks])
                except Exception:
                    pass
            self.repository.mark_document_failed(document_id)
            raise
