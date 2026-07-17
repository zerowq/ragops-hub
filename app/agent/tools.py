from __future__ import annotations

import hashlib
import uuid

from app.domain.models import Principal
from app.storage.repository import SQLiteRepository


class CustomerServiceTools:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    async def query_order(self, principal: Principal, order_id: str) -> dict[str, object]:
        order = self.repository.query_order(principal, order_id)
        if not order:
            return {"ok": False, "error": "订单不存在或当前用户无权访问"}
        self.repository.audit(principal, "order.query", "order", order_id)
        return {"ok": True, "order": order}

    async def prepare_ticket(
        self,
        principal: Principal,
        conversation_id: str,
        subject: str,
        description: str,
        *,
        case_id: str = "",
        order_id: str = "",
        customer_user_id: str = "",
    ) -> dict[str, object]:
        action = {
            "action_id": str(uuid.uuid4()),
            "type": "create_ticket",
            "subject": subject[:120],
            "description": description[:2000],
            "case_id": case_id,
            "order_id": order_id,
            "customer_user_id": customer_user_id,
        }
        self.repository.set_pending_action(conversation_id, principal, action)
        self.repository.audit(
            principal, "ticket.prepare", "conversation", conversation_id, {"subject": subject}
        )
        return {"ok": True, "requires_confirmation": True, "action": action}

    async def confirm_pending(self, principal: Principal, conversation_id: str) -> dict[str, object]:
        action = self.repository.get_pending_action(conversation_id, principal)
        if not action:
            return {"ok": False, "error": "当前没有待确认操作"}
        if action.get("type") != "create_ticket":
            return {"ok": False, "error": "不支持的待确认操作"}
        idempotency_key = hashlib.sha256(
            f"{principal.tenant_id}:{principal.user_id}:{action['action_id']}".encode()
        ).hexdigest()
        ticket = self.repository.create_ticket(
            principal,
            subject=action["subject"],
            description=action["description"],
            idempotency_key=idempotency_key,
            customer_user_id=str(action.get("customer_user_id", "")),
            case_id=str(action.get("case_id", "")),
            order_id=str(action.get("order_id", "")),
        )
        case_id = str(action.get("case_id", ""))
        if case_id:
            self.repository.mark_support_case_escalated(
                principal,
                case_id,
                str(ticket["id"]),
            )
        self.repository.clear_pending_action(
            conversation_id,
            principal,
            str(action["action_id"]),
        )
        self.repository.audit(principal, "ticket.create", "ticket", ticket["id"])
        return {"ok": True, "ticket": ticket}

    async def cancel_pending(
        self,
        principal: Principal,
        conversation_id: str,
    ) -> dict[str, object]:
        action = self.repository.get_pending_action(conversation_id, principal)
        if not action:
            return {"ok": False, "error": "当前没有待确认操作"}
        action_id = str(action.get("action_id", ""))
        cleared = self.repository.clear_pending_action(
            conversation_id,
            principal,
            action_id,
        )
        if cleared:
            self.repository.audit(
                principal,
                "ticket.cancel",
                "conversation",
                conversation_id,
                {"action_id": action_id},
            )
        return {"ok": cleared, "status": "cancelled" if cleared else "not_found"}
