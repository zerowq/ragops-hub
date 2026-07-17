from __future__ import annotations

import re

from app.domain.models import Intent


class IntentRouter:
    ORDER_PATTERN = re.compile(r"(?:订单|order).{0,8}([A-Za-z]+-?\d+)", re.I)

    def classify(self, text: str, has_pending_action: bool = False) -> Intent:
        normalized = text.strip().lower()
        if has_pending_action and normalized in {"确认", "确定", "提交", "yes", "confirm"}:
            return Intent.CONFIRM_TICKET
        if any(keyword in normalized for keyword in ("创建工单", "提交工单", "转人工", "投诉")):
            return Intent.CREATE_TICKET
        if any(keyword in normalized for keyword in ("订单", "物流", "发货", "order")):
            return Intent.QUERY_ORDER
        if normalized:
            return Intent.KNOWLEDGE
        return Intent.UNKNOWN

    def extract_order_id(self, text: str) -> str | None:
        direct = re.search(r"\bORD-?\d+\b", text, re.I)
        if direct:
            return direct.group(0).upper()
        match = self.ORDER_PATTERN.search(text)
        if match:
            return match.group(1).upper()
        return None
