from pathlib import Path

import pytest

from app.agent.intent import IntentRouter
from app.agent.service import EnterpriseAgentService
from app.agent.tools import CustomerServiceTools
from app.domain.models import Principal
from app.embeddings.providers import HashEmbeddingProvider
from app.llm.generator import ExtractiveAnswerGenerator
from app.rag.retrieval import HybridRetriever
from app.security.guardrails import PromptInjectionGuard
from app.storage.repository import SQLiteRepository
from app.storage.vector_store import InMemoryVectorStore


def build_agent(path: Path) -> EnterpriseAgentService:
    repository = SQLiteRepository(path)
    embedder = HashEmbeddingProvider(64)
    retriever = HybridRetriever(repository, InMemoryVectorStore(), embedder)
    return EnterpriseAgentService(
        repository,
        PromptInjectionGuard(),
        IntentRouter(),
        retriever,
        ExtractiveAnswerGenerator(),
        CustomerServiceTools(repository),
    )


def test_intent_router_handles_negation_and_order_policy() -> None:
    router = IntentRouter()
    assert router.classify("我不想创建工单，只想了解处理流程").value == "knowledge"
    assert router.classify("订单退款政策是什么").value == "knowledge"
    assert router.classify("查询订单 ORD-1001").value == "query_order"


@pytest.mark.asyncio
async def test_order_tool_enforces_owner(tmp_path: Path) -> None:
    agent = build_agent(tmp_path / "agent.db")
    principal = Principal("demo-user", "demo-company", "customer-service")
    events = [event async for event in agent.stream("查询订单 ORD-1001", "c1", principal)]
    text = "".join(event.data.get("content", "") for event in events if event.event == "text_delta")
    assert "已发货" in text


@pytest.mark.asyncio
async def test_ticket_requires_confirmation(tmp_path: Path) -> None:
    agent = build_agent(tmp_path / "agent.db")
    principal = Principal("demo-user", "demo-company", "customer-service")
    first = [event async for event in agent.stream("产品故障，请创建工单", "c2", principal)]
    assert any(event.event == "human_confirmation_required" for event in first)
    second = [event async for event in agent.stream("确认", "c2", principal)]
    text = "".join(event.data.get("content", "") for event in second if event.event == "text_delta")
    assert "工单已创建" in text
