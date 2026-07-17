from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str = Field(default="demo-conversation", min_length=1, max_length=128)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)


class TicketRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)


class DocumentMetadata(BaseModel):
    visibility: Literal["public", "department", "private"] = "department"
    version: int = Field(default=1, ge=1)

