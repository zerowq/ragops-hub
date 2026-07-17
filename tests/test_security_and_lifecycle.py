from pathlib import Path

import pytest

from app.api.dependencies import Container
from app.domain.models import Chunk, Principal
from app.embeddings.providers import HashEmbeddingProvider
from app.rag.chunking import StructureAwareChunker
from app.rag.ingestion import IngestionService
from app.rag.parsers import DocumentParser
from app.storage.repository import SQLiteRepository
from app.storage.vector_store import InMemoryVectorStore


def save_private_document(
    repository: SQLiteRepository,
    *,
    owner: Principal,
    document_id_suffix: str = "private",
) -> tuple[str, str]:
    document_id = repository.create_document(
        tenant_id=owner.tenant_id,
        department_id=owner.department_id,
        owner_user_id=owner.user_id,
        title="Private handbook",
        source=f"{document_id_suffix}.md",
        visibility="private",
        version=1,
        content_hash=f"hash-{document_id_suffix}",
    )
    chunk_id = f"chunk-{document_id_suffix}"
    repository.save_chunks(
        [
            Chunk(
                id=chunk_id,
                tenant_id=owner.tenant_id,
                department_id=owner.department_id,
                document_id=document_id,
                document_version=1,
                visibility="private",
                content="Only the owner can read this document.",
                source=f"{document_id_suffix}.md",
                title="Private handbook",
                position=0,
                metadata={"owner_user_id": owner.user_id},
            )
        ]
    )
    return document_id, chunk_id


def test_private_document_list_and_delete_enforce_owner(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "acl.db")
    owner = Principal("owner", "tenant-a", "support")
    attacker = Principal("attacker", "tenant-a", "support")
    document_id, chunk_id = save_private_document(repository, owner=owner)

    assert [item["id"] for item in repository.list_documents(owner)] == [document_id]
    assert repository.list_documents(attacker) == []
    assert repository.mark_document_deleting(attacker, document_id) is None
    assert repository.mark_document_deleting(owner, document_id) == [chunk_id]


def test_conversation_id_cannot_be_overwritten_by_another_principal(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "conversation.db")
    owner = Principal("owner", "tenant-a", "support")
    attacker = Principal("attacker", "tenant-b", "support")
    owner_action = {"action_id": "a1", "type": "create_ticket", "description": "owner"}
    repository.set_pending_action("shared-id", owner, owner_action)

    with pytest.raises(PermissionError):
        repository.set_pending_action(
            "shared-id",
            attacker,
            {"action_id": "a2", "type": "create_ticket", "description": "attacker"},
        )

    assert repository.get_pending_action("shared-id", owner) == owner_action


@pytest.mark.asyncio
async def test_ingestion_compensates_vector_write_when_metadata_save_fails(
    tmp_path: Path,
) -> None:
    class FailingRepository(SQLiteRepository):
        def save_chunks(self, chunks: list[Chunk]) -> None:
            raise RuntimeError("metadata write failed")

    class RecordingVectorStore:
        persistent = False

        def __init__(self) -> None:
            self.upserted: list[str] = []
            self.deleted: list[str] = []

        async def health(self) -> bool:
            return True

        async def upsert(self, chunks: list[Chunk]) -> None:
            self.upserted = [chunk.id for chunk in chunks]

        async def delete(self, chunk_ids: list[str]) -> None:
            self.deleted.extend(chunk_ids)

        async def search(self, vector, principal, limit):
            return []

    path = tmp_path / "document.md"
    path.write_text("# Test\n\nCompensation should remove orphan vectors.")
    repository = FailingRepository(tmp_path / "ingestion.db")
    vector_store = RecordingVectorStore()
    service = IngestionService(
        repository,
        vector_store,
        HashEmbeddingProvider(64),
        StructureAwareChunker(),
        DocumentParser(),
    )

    with pytest.raises(RuntimeError, match="metadata write failed"):
        await service.ingest_path(
            path,
            Principal("owner", "tenant-a", "support"),
        )

    assert vector_store.upserted
    assert vector_store.deleted == vector_store.upserted


@pytest.mark.asyncio
async def test_duplicate_bootstrap_rehydrates_memory_vectors(tmp_path: Path) -> None:
    path = tmp_path / "document.md"
    path.write_text("# Refund\n\nRefunds are available within seven days.")
    repository = SQLiteRepository(tmp_path / "rehydrate.db")
    embedder = HashEmbeddingProvider(64)
    principal = Principal("owner", "tenant-a", "support")
    first_store = InMemoryVectorStore()
    first_service = IngestionService(
        repository,
        first_store,
        embedder,
        StructureAwareChunker(),
        DocumentParser(),
    )
    await first_service.ingest_path(path, principal)

    restarted_store = InMemoryVectorStore()
    restarted_service = IngestionService(
        repository,
        restarted_store,
        embedder,
        StructureAwareChunker(),
        DocumentParser(),
    )
    result = await restarted_service.ingest_path(path, principal)
    query_vector = (await embedder.embed(["refund seven days"]))[0]
    hits = await restarted_store.search(query_vector, principal, 5)

    assert result["vector_rehydrated"] is True
    assert hits


@pytest.mark.asyncio
async def test_application_startup_rehydrates_memory_vectors(tmp_path: Path) -> None:
    path = tmp_path / "document.md"
    path.write_text("# Refund\n\nRefunds are available within seven days.")
    repository = SQLiteRepository(tmp_path / "startup-rehydrate.db")
    embedder = HashEmbeddingProvider(64)
    principal = Principal("owner", "tenant-a", "support")
    ingestion = IngestionService(
        repository,
        InMemoryVectorStore(),
        embedder,
        StructureAwareChunker(),
        DocumentParser(),
    )
    await ingestion.ingest_path(path, principal)

    restarted_store = InMemoryVectorStore()
    container = object.__new__(Container)
    container.repository = repository
    container.vector_store = restarted_store
    container.embedder = embedder

    restored = await container.startup()
    query_vector = (await embedder.embed(["refund seven days"]))[0]
    hits = await restarted_store.search(query_vector, principal, 5)

    assert restored > 0
    assert hits
    assert hits[0].dense_rank == 1
