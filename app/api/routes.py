from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.dependencies import Container, get_container, get_principal
from app.api.schemas import ChatRequest, SearchRequest
from app.domain.models import AgentEvent, Principal

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


def encode_sse(event: AgentEvent) -> str:
    payload = {"created_at": event.created_at, **event.data}
    return f"event: {event.event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/health")
async def health(container: Container = Depends(get_container)) -> dict[str, object]:
    database_ready = container.repository.health()
    vector_ready = await container.vector_store.health()
    if not database_ready or not vector_ready:
        raise HTTPException(
            503,
            {
                "status": "not_ready",
                "database": database_ready,
                "vector_store": vector_ready,
            },
        )
    return {
        "status": "ok",
        "app": container.settings.app_name,
        "vector_backend": container.settings.vector_backend,
        "embedding_provider": container.settings.embedding_provider,
        "llm_enabled": container.settings.llm_enabled,
        "database": "ready",
        "vector_store": "ready",
    }


@router.get("/documents")
async def list_documents(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[dict[str, object]]:
    return container.repository.list_documents(principal)


@router.get("/support/cases")
async def list_support_cases(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[dict[str, object]]:
    if not {"support_agent", "support_manager", "admin"}.intersection(principal.roles):
        raise HTTPException(403, "Support role is required")
    return container.repository.list_support_cases(principal)


@router.get("/support/cases/{case_id}")
async def get_support_case(
    case_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    context = container.repository.get_support_case(principal, case_id)
    if context is None:
        raise HTTPException(404, "Support case not found or not accessible")
    pending_action = container.repository.get_pending_action(case_id, principal)
    memory = container.memory.load(case_id, principal)
    case_query = f"{context['case']['subject']} {context['case']['preview']}"
    ticket_history = container.repository.list_customer_ticket_history(
        principal,
        case_id,
    )
    similar_tickets = container.repository.search_similar_customer_tickets(
        principal,
        case_id,
        case_query,
    )
    return {
        **context,
        "pending_action": pending_action,
        "memory": {
            "summary": memory.summary,
            "recent_messages": memory.recent_messages,
            "message_count": memory.message_count,
            "summarized_message_count": memory.summarized_message_count,
            "summary_updated_at": memory.summary_updated_at,
        },
        "ticket_history": ticket_history,
        "similar_tickets": similar_tickets,
    }


@router.post("/support/cases/{case_id}/pending-action/cancel")
async def cancel_support_case_pending_action(
    case_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    result = await container.tools.cancel_pending(principal, case_id)
    if not result["ok"]:
        raise HTTPException(404, str(result.get("error", "Pending action not found")))
    return result


@router.get("/ops/summary")
async def ops_summary(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    if not {
        "support_agent",
        "support_manager",
        "knowledge_admin",
        "admin",
    }.intersection(principal.roles):
        raise HTTPException(403, "Operational role is required")
    return container.repository.get_ops_summary(principal)


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
        uploaded = 0
        while chunk := await file.read(1024 * 1024):
            uploaded += len(chunk)
            if uploaded > container.settings.max_upload_bytes:
                target.unlink(missing_ok=True)
                raise HTTPException(413, "Uploaded file is too large")
            await asyncio.to_thread(output.write, chunk)
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
    chunk_ids = container.repository.mark_document_deleting(principal, document_id)
    if chunk_ids is None:
        raise HTTPException(404, "Document not found or not deletable")
    try:
        await container.vector_store.delete(chunk_ids)
    except Exception as error:
        logger.exception("Vector deletion failed for document %s", document_id)
        raise HTTPException(
            503,
            "Document deletion is pending and will be retried",
        ) from error
    container.repository.finalize_document_delete(principal, document_id)
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


@router.get("/chunks/{chunk_id}")
async def get_chunk_source(
    chunk_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, object]:
    if chunk_id not in container.repository.filter_accessible_chunk_ids(
        principal, [chunk_id]
    ):
        raise HTTPException(404, "Chunk not found or not accessible")
    chunk = container.repository.get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(404, "Chunk not found")
    return {
        "chunk_id": chunk.id,
        "title": chunk.title,
        "source": chunk.source,
        "position": chunk.position,
        "content": chunk.content,
        "metadata": chunk.metadata,
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
                request.message,
                request.conversation_id,
                principal,
                request.case_id,
            ):
                yield encode_sse(event)
        except Exception as error:
            logger.exception("Agent stream failed")
            try:
                container.repository.audit(
                    principal,
                    "agent.error",
                    "conversation",
                    request.conversation_id,
                    {"error_type": type(error).__name__},
                )
            except Exception:
                logger.exception("Failed to persist agent error audit")
            yield encode_sse(
                AgentEvent(
                    "error",
                    {
                        "code": "AGENT_EXECUTION_ERROR",
                        "message": "请求处理失败，请稍后重试。",
                    },
                )
            )
            yield encode_sse(AgentEvent("message_end", {"status": "error"}))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
