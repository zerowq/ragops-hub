from pathlib import Path

import pytest

from app.agent.intent import IntentRouter
from app.agent.memory import ConversationMemoryService, GenerationContext
from app.agent.service import EnterpriseAgentService
from app.agent.tools import CustomerServiceTools
from app.domain.models import Principal
from app.embeddings.providers import HashEmbeddingProvider
from app.llm.generator import ExtractiveAnswerGenerator
from app.rag.retrieval import HybridRetriever
from app.security.guardrails import PromptInjectionGuard
from app.storage.repository import SQLiteRepository
from app.storage.vector_store import InMemoryVectorStore


def build_agent(
    path: Path,
    *,
    recent_message_limit: int = 8,
    summary_trigger_messages: int = 12,
) -> EnterpriseAgentService:
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
        ConversationMemoryService(
            repository,
            recent_message_limit,
            summary_trigger_messages,
        ),
    )


def test_intent_router_handles_negation_and_order_policy() -> None:
    router = IntentRouter()
    assert router.classify("我不想创建工单，只想了解处理流程").value == "knowledge"
    assert router.classify("订单退款政策是什么").value == "knowledge"
    assert router.classify("查询订单 ORD-1001").value == "query_order"


def test_memory_order_follow_up_does_not_capture_policy_question(
    tmp_path: Path,
) -> None:
    memory = ConversationMemoryService(
        SQLiteRepository(tmp_path / "memory-intent.db")
    )
    assert memory.is_order_follow_up("这个订单的状态怎么样？", "ORD-1001")
    assert memory.is_order_follow_up("服务期呢？", "ORD-1001")
    assert not memory.is_order_follow_up("订单退款政策是什么？", "ORD-1001")


@pytest.mark.asyncio
async def test_order_tool_enforces_owner(tmp_path: Path) -> None:
    agent = build_agent(tmp_path / "agent.db")
    principal = Principal("demo-user", "demo-company", "customer-service")
    events = [event async for event in agent.stream("查询订单 ORD-1001", "c1", principal)]
    text = "".join(event.data.get("content", "") for event in events if event.event == "text_delta")
    assert "已生效" in text


def test_support_order_query_requires_assigned_case(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "support-order.db")
    assigned = Principal(
        "agent-chenyu",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )
    unassigned = Principal(
        "agent-other",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )

    assert repository.query_order(assigned, "ORD-1001") is not None
    assert repository.query_order(unassigned, "ORD-1001") is None


def test_customer_ticket_history_and_similar_issue_require_case_access(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "ticket-history.db")
    assigned = Principal(
        "agent-chenyu",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )
    unassigned = Principal(
        "agent-other",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )

    history = repository.list_customer_ticket_history(assigned, "CASE-1001")
    similar = repository.search_similar_customer_tickets(
        assigned,
        "CASE-1001",
        "企业知识库专业版登录失败",
    )

    assert {ticket["id"] for ticket in history} == {
        "TKT-HIST-1001",
        "TKT-HIST-1002",
    }
    assert similar[0]["id"] == "TKT-HIST-1001"
    assert "SSO 回调地址" in similar[0]["resolution"]
    assert repository.list_customer_ticket_history(unassigned, "CASE-1001") == []
    assert (
        repository.search_similar_customer_tickets(
            unassigned,
            "CASE-1001",
            "登录失败",
        )
        == []
    )


@pytest.mark.asyncio
async def test_ticket_requires_confirmation(tmp_path: Path) -> None:
    agent = build_agent(tmp_path / "agent.db")
    principal = Principal("demo-user", "demo-company", "customer-service")
    first = [event async for event in agent.stream("产品故障，请创建工单", "c2", principal)]
    assert any(event.event == "human_confirmation_required" for event in first)
    second = [event async for event in agent.stream("确认", "c2", principal)]
    text = "".join(event.data.get("content", "") for event in second if event.event == "text_delta")
    assert "工单已创建" in text


@pytest.mark.asyncio
async def test_support_ticket_links_case_order_and_customer(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "support-ticket.db")
    tools = CustomerServiceTools(repository)
    principal = Principal(
        "agent-chenyu",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )
    await tools.prepare_ticket(
        principal,
        "CASE-1001",
        "登录失败与退款政策咨询",
        "客户登录失败，排查后需要技术支持。",
        case_id="CASE-1001",
        order_id="ORD-1001",
        customer_user_id="demo-user",
        handoff_summary="客户登录失败；已经核对订单和退款政策。",
    )

    result = await tools.confirm_pending(principal, "CASE-1001")
    context = repository.get_support_case(principal, "CASE-1001")

    assert result["ok"] is True
    assert result["ticket"]["case_id"] == "CASE-1001"
    assert result["ticket"]["order_id"] == "ORD-1001"
    assert result["ticket"]["customer_user_id"] == "demo-user"
    assert "已经核对订单" in result["ticket"]["handoff_summary"]
    assert context is not None
    assert context["case"]["status"] == "escalated"
    assert context["case"]["ticket_id"] == result["ticket"]["id"]


@pytest.mark.asyncio
async def test_order_follow_up_resolves_order_from_recent_memory(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path / "order-memory.db")
    principal = Principal("demo-user", "demo-company", "customer-service")

    _ = [
        event
        async for event in agent.stream(
            "查询订单 ORD-1001",
            "order-memory",
            principal,
        )
    ]
    follow_up = [
        event
        async for event in agent.stream(
            "这个订单的状态怎么样？",
            "order-memory",
            principal,
        )
    ]

    text = "".join(
        event.data.get("content", "")
        for event in follow_up
        if event.event == "text_delta"
    )
    assert "ORD-1001" in text
    assert "已生效" in text
    assert any(
        event.event == "memory_loaded"
        and event.data["recent_messages"] >= 2
        for event in follow_up
    )


@pytest.mark.asyncio
async def test_knowledge_answer_receives_case_scoped_generation_context(
    tmp_path: Path,
) -> None:
    class RecordingGenerator:
        def __init__(self) -> None:
            self.contexts: list[GenerationContext | None] = []

        async def stream(self, query, hits, context=None):
            self.contexts.append(context)
            yield "已记录上下文"

    repository = SQLiteRepository(tmp_path / "generation-memory.db")
    generator = RecordingGenerator()
    embedder = HashEmbeddingProvider(64)
    agent = EnterpriseAgentService(
        repository,
        PromptInjectionGuard(),
        IntentRouter(),
        HybridRetriever(repository, InMemoryVectorStore(), embedder),
        generator,
        CustomerServiceTools(repository),
        ConversationMemoryService(repository),
    )
    principal = Principal(
        "agent-chenyu",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )

    _ = [
        event
        async for event in agent.stream(
            "登录失败应该先检查什么？",
            "CASE-1001",
            principal,
            "CASE-1001",
        )
    ]
    _ = [
        event
        async for event in agent.stream(
            "那下一步呢？",
            "CASE-1001",
            principal,
            "CASE-1001",
        )
    ]

    context = generator.contexts[-1]
    assert context is not None
    assert context.business_context["订单"] == "ORD-1001"
    assert context.similar_tickets
    assert context.similar_tickets[0]["ticket_id"] == "TKT-HIST-1001"
    assert any(
        message["content"] == "登录失败应该先检查什么？"
        for message in context.recent_messages
    )


@pytest.mark.asyncio
async def test_case_summary_is_generated_and_frozen_into_ticket_handoff(
    tmp_path: Path,
) -> None:
    agent = build_agent(
        tmp_path / "summary-memory.db",
        recent_message_limit=2,
        summary_trigger_messages=5,
    )
    principal = Principal(
        "agent-chenyu",
        "demo-company",
        "customer-service",
        ["support_agent"],
    )
    for message in ("客户登录失败", "已经重置密码但仍失败", "还需要进一步排查"):
        _ = [
            event
            async for event in agent.stream(
                message,
                "CASE-1001",
                principal,
                "CASE-1001",
            )
        ]

    memory = agent.memory.load("CASE-1001", principal)
    assert memory.summary
    assert memory.summarized_message_count >= 2
    assert len(memory.recent_messages) == 2

    prepared = [
        event
        async for event in agent.stream(
            "请为当前客户创建工单",
            "CASE-1001",
            principal,
            "CASE-1001",
        )
    ]
    action = next(
        event.data["action"]
        for event in prepared
        if event.event == "human_confirmation_required"
    )
    assert "历史会话摘要" in action["handoff_summary"]
    assert "ORD-1001" in action["handoff_summary"]

    confirmed = [
        event
        async for event in agent.stream(
            "确认",
            "CASE-1001",
            principal,
            "CASE-1001",
        )
    ]
    result = next(
        event.data["result"]
        for event in confirmed
        if event.event == "tool_finished"
    )
    assert result["ok"] is True
    assert result["ticket"]["handoff_summary"] == action["handoff_summary"]
