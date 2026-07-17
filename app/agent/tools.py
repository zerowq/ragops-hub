from __future__ import annotations

import hashlib

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
        self, principal: Principal, conversation_id: str, subject: str, description: str
    ) -> dict[str, object]:
        action = {
            "type": "create_ticket",
            "subject": subject[:120],
            "description": description[:2000],
        }
        self.repository.set_pending_action(conversation_id, principal, action)
        self.repository.audit(
            principal, "ticket.prepare", "conversation", conversation_id, {"subject": subject}
        )
        return {"ok": True, "requires_confirmation": True, "action": action}

    async def confirm_pending(self, principal: Principal, conversation_id: str) -> dict[str, object]:
        action = self.repository.pop_pending_action(conversation_id, principal)
        if not action:
            return {"ok": False, "error": "当前没有待确认操作"}
        if action.get("type") != "create_ticket":
            return {"ok": False, "error": "不支持的待确认操作"}
        idempotency_key = hashlib.sha256(
            f"{principal.tenant_id}:{principal.user_id}:{conversation_id}:{action['description']}".encode()
        ).hexdigest()
        ticket = self.repository.create_ticket(
            principal,
            subject=action["subject"],
            description=action["description"],
            idempotency_key=idempotency_key,
        )
        self.repository.audit(principal, "ticket.create", "ticket", ticket["id"])
        return {"ok": True, "ticket": ticket}

