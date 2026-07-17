from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Visibility(StrEnum):
    PUBLIC = "public"
    DEPARTMENT = "department"
    PRIVATE = "private"


class Intent(StrEnum):
    KNOWLEDGE = "knowledge"
    QUERY_ORDER = "query_order"
    CREATE_TICKET = "create_ticket"
    CONFIRM_TICKET = "confirm_ticket"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class Principal:
    user_id: str
    tenant_id: str
    department_id: str
    roles: list[str] = field(default_factory=lambda: ["employee"])


@dataclass(slots=True)
class Chunk:
    id: str
    tenant_id: str
    department_id: str
    document_id: str
    document_version: int
    visibility: str
    content: str
    source: str
    title: str
    position: int
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchHit:
    chunk: Chunk
    score: float
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rerank_score: float | None = None


@dataclass(slots=True)
class AgentEvent:
    event: str
    data: dict[str, Any]
    created_at: str = field(default_factory=utc_now)
