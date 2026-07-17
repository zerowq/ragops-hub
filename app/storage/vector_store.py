from __future__ import annotations

import asyncio
import math
import threading
from typing import Protocol

from app.domain.models import Chunk, Principal, SearchHit


class VectorStore(Protocol):
    persistent: bool

    async def health(self) -> bool: ...

    async def upsert(self, chunks: list[Chunk]) -> None: ...

    async def delete(self, chunk_ids: list[str]) -> None: ...

    async def search(
        self, vector: list[float], principal: Principal, limit: int
    ) -> list[SearchHit]: ...


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
    return dot / (left_norm * right_norm)


class InMemoryVectorStore:
    persistent = False

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._lock = threading.RLock()

    async def health(self) -> bool:
        return True

    async def upsert(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for chunk in chunks:
                self._chunks[chunk.id] = chunk

    async def delete(self, chunk_ids: list[str]) -> None:
        with self._lock:
            for chunk_id in chunk_ids:
                self._chunks.pop(chunk_id, None)

    async def search(
        self, vector: list[float], principal: Principal, limit: int
    ) -> list[SearchHit]:
        with self._lock:
            chunks = list(self._chunks.values())
        accessible = [
            chunk
            for chunk in chunks
            if chunk.tenant_id == principal.tenant_id
            and (
                chunk.visibility == "public"
                or (chunk.visibility == "department" and chunk.department_id == principal.department_id)
                or (
                    chunk.visibility == "private"
                    and chunk.metadata.get("owner_user_id") == principal.user_id
                )
            )
        ]
        hits = []
        for chunk in accessible:
            similarity = cosine_similarity(vector, chunk.embedding)
            hits.append(
                SearchHit(chunk=chunk, score=similarity, dense_score=similarity)
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        for index, hit in enumerate(hits[:limit], start=1):
            hit.dense_rank = index
        return hits[:limit]


class MilvusVectorStore:
    persistent = True

    def __init__(self, uri: str, token: str, collection: str, dimension: int) -> None:
        from pymilvus import DataType, MilvusClient

        self.client = MilvusClient(uri=uri, token=token or None)
        self.collection = collection
        self.dimension = dimension
        self.has_document_version = False
        if not self.client.has_collection(collection_name=collection):
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimension)
            schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
            schema.add_field("department_id", DataType.VARCHAR, max_length=64)
            schema.add_field("visibility", DataType.VARCHAR, max_length=32)
            schema.add_field("owner_user_id", DataType.VARCHAR, max_length=64)
            schema.add_field("document_id", DataType.VARCHAR, max_length=64)
            schema.add_field("document_version", DataType.INT64)
            schema.add_field("content", DataType.VARCHAR, max_length=8192)
            schema.add_field("source", DataType.VARCHAR, max_length=1024)
            schema.add_field("title", DataType.VARCHAR, max_length=512)
            schema.add_field("position", DataType.INT64)
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="HNSW",
                metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self.client.create_collection(
                collection_name=collection,
                schema=schema,
                index_params=index_params,
            )
            self.has_document_version = True
        else:
            description = self.client.describe_collection(collection_name=collection)
            fields = description.get("fields", [])
            field_names = {field.get("name") for field in fields}
            self.has_document_version = "document_version" in field_names
            vector_field = next(
                (field for field in fields if field.get("name") == "vector"),
                None,
            )
            configured_dimension = (
                vector_field.get("params", {}).get("dim") if vector_field else None
            )
            if configured_dimension and int(configured_dimension) != dimension:
                raise ValueError(
                    "Milvus collection dimension mismatch: "
                    f"collection={configured_dimension}, configured={dimension}. "
                    "Use a new collection and re-index documents."
                )

    async def health(self) -> bool:
        try:
            return await asyncio.to_thread(
                self.client.has_collection,
                collection_name=self.collection,
            )
        except Exception:
            return False

    async def upsert(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        data = []
        for chunk in chunks:
            entity = {
                "id": chunk.id,
                "vector": chunk.embedding,
                "tenant_id": chunk.tenant_id,
                "department_id": chunk.department_id,
                "visibility": chunk.visibility,
                "owner_user_id": str(chunk.metadata.get("owner_user_id", "")),
                "document_id": chunk.document_id,
                "content": chunk.content[:8192],
                "source": chunk.source[:1024],
                "title": chunk.title[:512],
                "position": chunk.position,
            }
            if self.has_document_version:
                entity["document_version"] = chunk.document_version
            data.append(entity)
        await asyncio.to_thread(
            self.client.upsert,
            collection_name=self.collection,
            data=data,
        )

    async def delete(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            await asyncio.to_thread(
                self.client.delete,
                collection_name=self.collection,
                ids=chunk_ids,
            )

    async def search(
        self, vector: list[float], principal: Principal, limit: int
    ) -> list[SearchHit]:
        def escaped(value: str) -> str:
            return value.replace('"', '\\"')

        tenant = escaped(principal.tenant_id)
        department = escaped(principal.department_id)
        user = escaped(principal.user_id)
        filter_expression = (
            f'tenant_id == "{tenant}" and '
            f'(visibility == "public" or '
            f'(visibility == "department" and department_id == "{department}") or '
            f'(visibility == "private" and owner_user_id == "{user}"))'
        )
        output_fields = [
            "tenant_id",
            "department_id",
            "visibility",
            "owner_user_id",
            "document_id",
            "content",
            "source",
            "title",
            "position",
        ]
        if self.has_document_version:
            output_fields.append("document_version")
        search_results = await asyncio.to_thread(
            self.client.search,
            collection_name=self.collection,
            data=[vector],
            filter=filter_expression,
            limit=limit,
            output_fields=output_fields,
            search_params={"metric_type": "COSINE", "params": {"ef": max(64, limit * 4)}},
        )
        results = search_results[0]
        hits: list[SearchHit] = []
        for rank, result in enumerate(results, start=1):
            entity = result["entity"]
            chunk = Chunk(
                id=str(result["id"]),
                tenant_id=entity["tenant_id"],
                department_id=entity["department_id"],
                document_id=entity["document_id"],
                document_version=int(entity.get("document_version", 1)),
                visibility=entity["visibility"],
                content=entity["content"],
                source=entity["source"],
                title=entity["title"],
                position=entity["position"],
                metadata={"owner_user_id": entity.get("owner_user_id", "")},
            )
            similarity = float(result["distance"])
            hits.append(
                SearchHit(
                    chunk=chunk,
                    score=similarity,
                    dense_score=similarity,
                    dense_rank=rank,
                )
            )
        return hits
