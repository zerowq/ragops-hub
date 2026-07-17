from __future__ import annotations

import time
from collections.abc import AsyncIterator

from app.agent.intent import IntentRouter
from app.agent.tools import CustomerServiceTools
from app.domain.models import AgentEvent, Intent, Principal
from app.llm.generator import AnswerGenerator
from app.rag.retrieval import HybridRetriever
from app.security.guardrails import PromptInjectionGuard
from app.storage.repository import SQLiteRepository


class EnterpriseAgentService:
    def __init__(
        self,
        repository: SQLiteRepository,
        guard: PromptInjectionGuard,
        router: IntentRouter,
        retriever: HybridRetriever,
        generator: AnswerGenerator,
        tools: CustomerServiceTools,
    ) -> None:
        self.repository = repository
        self.guard = guard
        self.router = router
        self.retriever = retriever
        self.generator = generator
        self.tools = tools

    async def stream(
        self, message: str, conversation_id: str, principal: Principal
    ) -> AsyncIterator[AgentEvent]:
        started = time.perf_counter()
        yield AgentEvent("message_start", {"conversation_id": conversation_id})
        self.repository.save_message(conversation_id, principal, "user", message)

        guard_result = self.guard.check(message)
        if not guard_result.allowed:
            self.repository.audit(
                principal,
                "guard.block",
                "conversation",
                conversation_id,
                {"reason": guard_result.reason, "risk_score": guard_result.risk_score},
            )
            yield AgentEvent(
                "guard_blocked",
                {"reason": guard_result.reason, "risk_score": guard_result.risk_score},
            )
            yield AgentEvent("text_delta", {"content": "该请求触发了输入安全策略，无法继续处理。"})
            yield AgentEvent("message_end", {"status": "blocked"})
            return

        # Pending-action lookup is deliberately performed by attempting a confirmation only
        # when the user explicitly confirms. Other messages leave the pending action intact.
        normalized = message.strip().lower()
        intent = self.router.classify(message, has_pending_action=normalized in {"确认", "确定", "提交", "yes", "confirm"})
        yield AgentEvent("intent_classified", {"intent": intent.value})

        answer = ""
        citations: list[dict[str, object]] = []
        if intent is Intent.QUERY_ORDER:
            order_id = self.router.extract_order_id(message)
            if not order_id:
                answer = "请提供订单号，例如 ORD-1001。"
            else:
                yield AgentEvent("tool_start", {"tool": "query_order", "arguments": {"order_id": order_id}})
                result = await self.tools.query_order(principal, order_id)
                yield AgentEvent("tool_finished", {"tool": "query_order", "result": result})
                if result["ok"]:
                    order = result["order"]
                    answer = f"订单 {order['id']} 当前状态为“{order['status']}”，商品：{order['product_name']}。"
                else:
                    answer = str(result["error"])
            async for event in self._emit_text(answer):
                yield event

        elif intent is Intent.CREATE_TICKET:
            subject = "客服问题"
            result = await self.tools.prepare_ticket(principal, conversation_id, subject, message)
            yield AgentEvent("human_confirmation_required", result)
            answer = f"将创建工单：{subject}，内容为“{message}”。回复“确认”后提交。"
            async for event in self._emit_text(answer):
                yield event

        elif intent is Intent.CONFIRM_TICKET:
            yield AgentEvent("tool_start", {"tool": "create_ticket"})
            result = await self.tools.confirm_pending(principal, conversation_id)
            yield AgentEvent("tool_finished", {"tool": "create_ticket", "result": result})
            answer = (
                f"工单已创建，编号 {result['ticket']['id']}。"
                if result["ok"]
                else str(result["error"])
            )
            async for event in self._emit_text(answer):
                yield event

        elif intent is Intent.KNOWLEDGE:
            yield AgentEvent("retrieval_start", {"query": message})
            retrieval_started = time.perf_counter()
            hits = await self.retriever.search(message, principal)
            safe_hits = [hit for hit in hits if self.guard.check(hit.chunk.content).allowed]
            if len(safe_hits) != len(hits):
                self.repository.audit(
                    principal,
                    "retrieval.chunk_block",
                    "conversation",
                    conversation_id,
                    {"blocked_chunks": len(hits) - len(safe_hits)},
                )
            hits = safe_hits
            retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
            citations = [
                {
                    "index": index,
                    "chunk_id": hit.chunk.id,
                    "title": hit.chunk.title,
                    "source": hit.chunk.source,
                    "position": hit.chunk.position,
                    "score": round(hit.score, 6),
                    "rerank_score": round(hit.rerank_score or 0.0, 6),
                }
                for index, hit in enumerate(hits, start=1)
            ]
            yield AgentEvent(
                "retrieval_finished",
                {"count": len(hits), "latency_ms": retrieval_ms, "citations": citations},
            )
            fragments: list[str] = []
            async for token in self.generator.stream(message, hits):
                fragments.append(token)
                yield AgentEvent("text_delta", {"content": token})
            answer = "".join(fragments)
            for citation in citations:
                yield AgentEvent("citation", citation)

        else:
            answer = "请描述需要查询的知识、订单号，或需要创建的客服工单。"
            async for event in self._emit_text(answer):
                yield event

        self.repository.save_message(
            conversation_id,
            principal,
            "assistant",
            answer,
            {"intent": intent.value, "citations": citations},
        )
        yield AgentEvent(
            "message_end",
            {
                "status": "success",
                "intent": intent.value,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    @staticmethod
    async def _emit_text(text: str) -> AsyncIterator[AgentEvent]:
        for character in text:
            yield AgentEvent("text_delta", {"content": character})
