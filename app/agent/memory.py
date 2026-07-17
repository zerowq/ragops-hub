from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.models import Principal
from app.storage.repository import SQLiteRepository


@dataclass(slots=True)
class GenerationContext:
    """Tenant-scoped context supplied to the answer generator."""

    summary: str = ""
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    business_context: dict[str, str] = field(default_factory=dict)
    similar_tickets: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ConversationMemory:
    conversation_id: str
    case_id: str
    summary: str
    recent_messages: list[dict[str, Any]]
    message_count: int
    summarized_message_count: int
    summary_updated_at: str = ""


class ConversationMemoryService:
    """Case-scoped short-term memory and deterministic handoff summaries.

    Durable business facts remain in the customer/order/case tables. This
    service only manages conversation context and never creates a free-form
    cross-case user profile.
    """

    ORDER_ID_PATTERN = re.compile(r"\bORD-?\d+\b", re.I)
    CONTEXTUAL_MARKERS = (
        "它",
        "这个",
        "那个",
        "刚才",
        "上面",
        "前面",
        "继续",
        "之前",
        "还是",
        "第一种",
        "第二种",
        "该订单",
        "订单呢",
        "服务期呢",
    )
    ORDER_KNOWLEDGE_MARKERS = (
        "退款政策",
        "退款流程",
        "订单规则",
        "购买流程",
        "怎么购买",
        "如何购买",
    )

    def __init__(
        self,
        repository: SQLiteRepository,
        recent_message_limit: int = 8,
        summary_trigger_messages: int = 12,
        summary_max_chars: int = 1600,
    ) -> None:
        self.repository = repository
        self.recent_message_limit = max(2, recent_message_limit)
        self.summary_trigger_messages = max(
            self.recent_message_limit + 1,
            summary_trigger_messages,
        )
        self.summary_max_chars = max(400, summary_max_chars)

    def load(
        self,
        conversation_id: str,
        principal: Principal,
    ) -> ConversationMemory:
        snapshot = self.repository.get_conversation_memory(
            conversation_id,
            principal,
            self.recent_message_limit,
        )
        recent_messages = self._usable_messages(snapshot["recent_messages"])
        return ConversationMemory(
            conversation_id=conversation_id,
            case_id=str(snapshot.get("case_id", "")),
            summary=str(snapshot.get("summary", "")),
            recent_messages=recent_messages,
            message_count=int(snapshot.get("message_count", 0)),
            summarized_message_count=int(
                snapshot.get("summarized_message_count", 0)
            ),
            summary_updated_at=str(snapshot.get("summary_updated_at", "")),
        )

    def refresh_summary(
        self,
        conversation_id: str,
        principal: Principal,
    ) -> ConversationMemory:
        memory = self.load(conversation_id, principal)
        all_messages = self._usable_messages(
            self.repository.list_conversation_messages(conversation_id, principal)
        )
        if len(all_messages) < self.summary_trigger_messages:
            return memory

        archived_count = max(0, len(all_messages) - self.recent_message_limit)
        if archived_count <= memory.summarized_message_count:
            return memory

        summary = self._summarize(all_messages[:archived_count])
        self.repository.update_conversation_summary(
            conversation_id,
            principal,
            summary,
            archived_count,
        )
        return self.load(conversation_id, principal)

    def generation_context(
        self,
        memory: ConversationMemory,
        case_context: dict[str, Any] | None,
        similar_tickets: list[dict[str, Any]] | None = None,
    ) -> GenerationContext:
        recent = [
            {
                "role": str(message["role"]),
                "content": str(message["content"])[:800],
            }
            for message in memory.recent_messages
        ]
        return GenerationContext(
            summary=memory.summary,
            recent_messages=recent,
            business_context=self._business_context(case_context),
            similar_tickets=[
                {
                    "ticket_id": str(ticket["id"]),
                    "subject": str(ticket["subject"]),
                    "status": str(ticket["status"]),
                    "resolution": str(ticket.get("resolution", ""))[:800],
                }
                for ticket in (similar_tickets or [])
            ],
        )

    def contextualize_query(
        self,
        message: str,
        memory: ConversationMemory,
        case_context: dict[str, Any] | None,
    ) -> str:
        """Add limited context only for likely follow-ups to avoid query drift."""
        normalized = message.strip()
        is_follow_up = (
            len(normalized) <= 16
            or any(marker in normalized for marker in self.CONTEXTUAL_MARKERS)
        )
        if not is_follow_up:
            return normalized

        hints: list[str] = []
        business = self._business_context(case_context)
        if business:
            hints.append(
                "关联业务：" + "；".join(f"{key}={value}" for key, value in business.items())
            )
        if memory.summary:
            hints.append(f"历史摘要：{memory.summary[:400]}")
        prior_user_messages = [
            str(item["content"])[:300]
            for item in memory.recent_messages
            if item["role"] == "user"
        ][-2:]
        if prior_user_messages:
            hints.append("最近问题：" + "；".join(prior_user_messages))
        return "\n".join([normalized, *hints])

    def resolve_order_id(
        self,
        message: str,
        memory: ConversationMemory,
        case_context: dict[str, Any] | None,
    ) -> str | None:
        match = self.ORDER_ID_PATTERN.search(message)
        if match:
            return match.group(0).upper()

        order = (case_context or {}).get("order") or {}
        if order.get("id"):
            return str(order["id"]).upper()

        for item in reversed(memory.recent_messages):
            match = self.ORDER_ID_PATTERN.search(str(item["content"]))
            if match:
                return match.group(0).upper()
        return None

    def is_order_follow_up(self, message: str, order_id: str | None) -> bool:
        if not order_id:
            return False
        normalized = message.strip().lower()
        if any(marker in normalized for marker in self.ORDER_KNOWLEDGE_MARKERS):
            return False
        return bool(
            re.search(
                r"(这个|那个|该|刚才的)?订单.{0,6}(状态|怎么样|到期|有效期|服务期)"
                r"|(?:服务期|有效期|什么时候到期)(?:呢|是|到)?"
                r"|它.{0,4}(状态|到期|有效期|服务期)",
                normalized,
            )
        )

    def build_handoff_summary(
        self,
        conversation_id: str,
        principal: Principal,
        case_context: dict[str, Any] | None,
    ) -> str:
        memory = self.load(conversation_id, principal)
        sections: list[str] = []
        business = self._business_context(case_context)
        if business:
            sections.append(
                "关联业务\n" + "\n".join(f"- {key}：{value}" for key, value in business.items())
            )
        case = (case_context or {}).get("case") or {}
        if case.get("preview"):
            sections.append(f"案件概况\n{str(case['preview'])[:500]}")
        if memory.summary:
            sections.append(f"历史会话摘要\n{memory.summary}")
        if memory.recent_messages:
            lines = [
                f"{'客服/客户' if item['role'] == 'user' else 'Agent'}："
                f"{str(item['content']).strip()[:500]}"
                for item in memory.recent_messages[-6:]
            ]
            sections.append("最近对话\n" + "\n".join(lines))
        return "\n\n".join(sections)[:4000]

    def _summarize(self, messages: list[dict[str, Any]]) -> str:
        """Create an evidence-only summary that also works with LLM_ENABLED=false."""
        lines: list[str] = []
        for item in messages:
            content = " ".join(str(item["content"]).split())
            if not content:
                continue
            speaker = "客服/客户" if item["role"] == "user" else "Agent"
            metadata = item.get("metadata") or {}
            intent = metadata.get("intent")
            suffix = f"（意图：{intent}）" if intent else ""
            lines.append(f"- {speaker}{suffix}：{content[:260]}")

        selected: list[str] = []
        used = len("历史会话要点：\n")
        for line in reversed(lines):
            required = len(line) + 1
            if selected and used + required > self.summary_max_chars:
                break
            selected.append(line)
            used += required
        selected.reverse()
        return ("历史会话要点：\n" + "\n".join(selected))[: self.summary_max_chars]

    @staticmethod
    def _business_context(case_context: dict[str, Any] | None) -> dict[str, str]:
        if not case_context:
            return {}
        case = case_context.get("case") or {}
        customer = case_context.get("customer") or {}
        order = case_context.get("order") or {}
        values = {
            "Case": case.get("id"),
            "问题": case.get("subject"),
            "客户": customer.get("name") or case.get("customer_name"),
            "公司": customer.get("company_name") or case.get("company_name"),
            "订单": order.get("id") or case.get("order_id"),
            "产品": order.get("product_name"),
            "版本": order.get("product_version"),
            "服务到期": order.get("valid_until"),
        }
        return {key: str(value) for key, value in values.items() if value}

    @staticmethod
    def _usable_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            message
            for message in messages
            if not (message.get("metadata") or {}).get("blocked")
        ]
