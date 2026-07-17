from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardResult:
    allowed: bool
    reason: str = ""
    risk_score: float = 0.0


class PromptInjectionGuard:
    """Fast deterministic first-line guardrail.

    It is intentionally transparent and testable. In production this can be
    composed with a local DeBERTa classifier or a policy service.
    """

    _patterns = [
        re.compile(r"ignore\s+(all|any|the)?\s*(previous|prior)\s+instructions", re.I),
        re.compile(r"system\s+prompt", re.I),
        re.compile(r"reveal\s+(your|the)\s+(prompt|instructions)", re.I),
        re.compile(r"忽略.{0,8}(之前|以上|前面).{0,8}(指令|要求|提示)", re.I),
        re.compile(r"输出.{0,8}(系统提示词|system prompt)", re.I),
        re.compile(r"越过.{0,8}(权限|安全|限制)", re.I),
    ]

    def check(self, text: str) -> GuardResult:
        normalized = " ".join(text.split())[:20_000]
        matches = sum(bool(pattern.search(normalized)) for pattern in self._patterns)
        if matches:
            return GuardResult(False, "potential_prompt_injection", min(1.0, 0.65 + matches * 0.15))
        if len(normalized) > 12_000:
            return GuardResult(False, "input_too_long", 0.7)
        return GuardResult(True)

