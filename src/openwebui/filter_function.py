"""
BetterRAG Open WebUI Filter Function — inlet preprocessing.

Intercepts messages before they reach the Pipe, allowing:
- Query reformulation (expanding abbreviations, adding context)
- Department hint injection from conversation history
- PII scrubbing before the query reaches the LLM
- Short-circuit responses for common greetings

Deploy: Open WebUI → Admin → Functions → + → paste this file.
Set priority lower than the Pipe (e.g. 0) so it runs first.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from pydantic import BaseModel


class Filter:
    class Valves(BaseModel):
        enabled: bool = True
        inject_department_hint: bool = True
        scrub_pii: bool = False
        max_query_length: int = 2000

    class UserValves(BaseModel):
        # Per-user settings exposed in Open WebUI profile
        preferred_department: str = ""

    def __init__(self):
        self.valves = self.Valves()
        self.type = "filter"
        self.name = "BetterRAG Preprocessor"
        self.id = "better-rag-filter"

    def inlet(
        self,
        body: dict[str, Any],
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> dict[str, Any]:
        """
        Preprocess messages before they reach the Pipe.

        Modifications made to the body are passed downstream to the Pipe.
        Return the body unchanged to pass through without modification.
        """
        if not self.valves.enabled:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        # Find last user message index
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return body

        msg = messages[last_user_idx]
        content = msg.get("content", "")
        if not isinstance(content, str):
            return body

        # Truncate over-long queries
        if len(content) > self.valves.max_query_length:
            content = content[: self.valves.max_query_length]

        # Optional PII scrubbing (email, phone, SSN patterns)
        if self.valves.scrub_pii:
            content = _scrub_pii(content)

        # Inject department hint as a system message if inferable
        if self.valves.inject_department_hint:
            dept = _infer_department(content)
            if not dept and __user__:
                # Check per-user preferred department
                user_valves = (__user__ or {}).get("valves", {})
                dept = user_valves.get("preferred_department", "")

            if dept:
                system_hint = {
                    "role": "system",
                    "content": (
                        f"[BetterRAG routing hint] The user query relates to the "
                        f"**{dept}** department. Prioritise {dept} documents."
                    ),
                }
                # Prepend system hint if there's no existing system message
                if messages[0].get("role") != "system":
                    messages = [system_hint] + messages
                    body = {**body, "messages": messages}

        # Apply cleaned content
        messages[last_user_idx] = {**msg, "content": content}
        body = {**body, "messages": messages}

        return body

    def outlet(
        self,
        body: dict[str, Any],
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> dict[str, Any]:
        """
        Post-process the assistant response before it's shown to the user.
        No-op by default — override to clean up or reformat the final answer.
        """
        return body


# ── Helpers ───────────────────────────────────────────────────────────────────

_DEPARTMENT_KEYWORDS: dict[str, list[str]] = {
    "hr": [
        "pto", "leave", "vacation", "holiday", "parental", "sick",
        "benefits", "onboarding", "offboarding", "payroll", "salary",
        "compensation", "performance review", "fmla", "ada", "policy",
        "job description", "headcount", "hire", "termination",
    ],
    "finance": [
        "budget", "revenue", "p&l", "profit", "loss", "ebitda", "cash flow",
        "invoice", "expense", "reimbursement", "vendor", "procurement",
        "forecast", "quarter", "fiscal", "audit", "tax", "balance sheet",
        "accounts", "capex", "opex", "kpi", "variance",
    ],
    "sales": [
        "pipeline", "deal", "opportunity", "quota", "crm", "salesforce",
        "close", "prospect", "account", "contract", "negotiation", "arr",
        "mrr", "churn", "win rate", "lead", "territory", "commission",
    ],
    "marketing": [
        "campaign", "content", "brand", "social", "email", "seo", "ads",
        "attribution", "roi", "conversion", "funnel", "engagement",
        "impressions", "ctr", "cpl", "event", "webinar", "press release",
    ],
}


def _infer_department(query: str) -> str:
    """Return the most likely department for a query, or empty string."""
    q = query.lower()
    scores: dict[str, int] = {dept: 0 for dept in _DEPARTMENT_KEYWORDS}
    for dept, keywords in _DEPARTMENT_KEYWORDS.items():
        for kw in keywords:
            if kw in q:
                scores[dept] += 1
    best_dept, best_score = max(scores.items(), key=lambda x: x[1])
    return best_dept if best_score >= 2 else ""


_PII_PATTERNS = [
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
]


def _scrub_pii(text: str) -> str:
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
