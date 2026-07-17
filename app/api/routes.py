from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.dependencies import Container, get_container, get_principal
from app.api.schemas import ChatRequest, SearchRequest
from app.domain.models import AgentEvent, Principal

router = APIRouter(prefix="/api/v1")


def encode_sse(event: AgentEvent) -> str:
    payload = {"created_at": event.created_at, **event.data}
    return f"event: {event.event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/health")
async def health(container: Container = Depends(get_container)) -> dict[str, object]:
    return {
        "status": "ok",
        "app": container.settings.app_name,
        "vector_backend": container.settings.vector_backend,
        "embedding_provider": container.settings.embedding_provider,
        "llm_enabled": container.settings.llm_enabled,
    }


@router.get("/documents")
async def list_documents(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[dict[str, object]]:
    return container.repository.list_documents(principal)


@router.post("/documents")
async def upload_document(
    file: UploadFile = File(...),
    visibility: str = Form(default="department"),
    version: int = Form(default=1),
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    if visibility not in {"public", "department", "private"}:
        raise HTTPException(400, "visibility must be public, department, or private")
    suffix = Path(file.filename or "document.txt").suffix.lower()
    if suffix not in {".txt", ".md", ".pdf", ".docx"}:
        raise HTTPException(415, "Supported file types: txt, md, pdf, docx")
    target = container.settings.upload_dir / f"{uuid.uuid4().hex}{suffix}"
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        return await container.ingestion.ingest_path(
            target,
            principal,
            visibility=visibility,
            version=version,
            title=Path(file.filename or "document").stem,
        )
    finally:
        target.unlink(missing_ok=True)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    chunk_ids = container.repository.delete_document(principal, document_id)
    if not chunk_ids:
        raise HTTPException(404, "Document not found")
    await container.vector_store.delete(chunk_ids)
    container.repository.audit(principal, "document.delete", "document", document_id)
    return {"status": "deleted", "chunks": len(chunk_ids)}


@router.post("/search")
async def search(
    request: SearchRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    hits = await container.retriever.search(request.query, principal)
    return {
        "query": request.query,
        "hits": [
            {
                "chunk_id": hit.chunk.id,
                "content": hit.chunk.content,
                "source": hit.chunk.source,
                "title": hit.chunk.title,
                "score": hit.score,
                "rerank_score": hit.rerank_score,
                "dense_rank": hit.dense_rank,
                "sparse_rank": hit.sparse_rank,
            }
            for hit in hits
        ],
    }


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        try:
            async for event in container.agent.stream(
                request.message, request.conversation_id, principal
            ):
                yield encode_sse(event)
        except Exception as error:
            yield encode_sse(
                AgentEvent(
                    "error",
                    {"code": "AGENT_EXECUTION_ERROR", "message": str(error)},
                )
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

