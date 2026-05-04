"""Metadata pre-filter builder — RBAC + department + date range + content type."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

logger = structlog.get_logger()

# Date-range patterns extracted from query text
_DATE_PATTERNS = [
    (r"\bthis\s+year\b", "this_year"),
    (r"\blast\s+year\b", "last_year"),
    (r"\bthis\s+quarter\b", "this_quarter"),
    (r"\blast\s+quarter\b", "last_quarter"),
    (r"\bthis\s+month\b", "this_month"),
    (r"\blast\s+(\d+)\s+days?\b", "last_n_days"),
    (r"\bsince\s+(\d{4})\b", "since_year"),
    (r"\bin\s+Q([1-4])\s+(\d{4})\b", "quarter_year"),
    (r"\bin\s+(\d{4})\b", "year"),
]

# Content-type keywords → content_type tags
_CONTENT_TYPE_HINTS: dict[str, str] = {
    "policy": "policy",
    "procedure": "policy",
    "guideline": "policy",
    "report": "report",
    "presentation": "presentation",
    "deck": "presentation",
    "slides": "presentation",
    "spreadsheet": "spreadsheet",
    "memo": "memo",
    "announcement": "announcement",
}


@dataclass
class MetadataFilter:
    """Pre-filter specification for vector search."""

    department: str | None = None
    content_type: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    access_level: str | None = None
    file_types: list[str] = field(default_factory=list)

    def to_sql_params(self) -> dict[str, Any]:
        """Convert to SQL WHERE clause parameters for pgvector search."""
        params: dict[str, Any] = {}
        if self.department:
            params["department"] = self.department
        if self.content_type:
            params["content_type"] = self.content_type
        if self.date_from:
            params["date_from"] = self.date_from.isoformat()
        if self.date_to:
            params["date_to"] = self.date_to.isoformat()
        if self.access_level:
            params["access_level"] = self.access_level
        return params

    def to_sql_clauses(self) -> list[str]:
        """Build SQL WHERE sub-clauses for each active filter."""
        clauses: list[str] = []
        if self.department:
            clauses.append("dc.department = :department")
        if self.content_type:
            clauses.append("dc.content_type = :content_type")
        if self.date_from:
            clauses.append("dc.created_at >= :date_from")
        if self.date_to:
            clauses.append("dc.created_at <= :date_to")
        if self.access_level:
            clauses.append("dc.access_level = :access_level")
        return clauses

    @property
    def is_empty(self) -> bool:
        return (
            self.department is None
            and self.content_type is None
            and self.date_from is None
            and self.date_to is None
            and self.access_level is None
            and not self.file_types
        )


class MetadataFilterBuilder:
    """
    Builds metadata pre-filters from:
    - User RBAC context (department, access_level)
    - Query analysis output (explicit department/content_type mentions)
    - Temporal expressions in the query text (last quarter, this year, etc.)
    """

    def build(
        self,
        query: str,
        user_context: dict,
        analysis_filters: dict | None = None,
    ) -> MetadataFilter:
        """
        Build a MetadataFilter from all available signals.

        Args:
            query: The original user query text.
            user_context: User's RBAC context (department, access_level, roles).
            analysis_filters: Filters already extracted by QueryAnalyzer.

        Returns:
            MetadataFilter ready for use in vector search.
        """
        analysis_filters = analysis_filters or {}
        mf = MetadataFilter()

        # 1. Department: from analysis (highest confidence) → user dept (fallback)
        mf.department = (
            analysis_filters.get("department")
            or _extract_department_from_query(query)
            or user_context.get("department")
        )

        # 2. Content type: from analysis or query text hints
        mf.content_type = (
            analysis_filters.get("content_type")
            or _extract_content_type(query)
        )

        # 3. Date range: from temporal expressions in query
        date_from, date_to = _extract_date_range(query)
        mf.date_from = date_from
        mf.date_to = date_to

        # 4. Access level: from user context
        mf.access_level = user_context.get("access_level")

        logger.debug(
            "metadata_filter.built",
            department=mf.department,
            content_type=mf.content_type,
            date_from=str(mf.date_from) if mf.date_from else None,
            date_to=str(mf.date_to) if mf.date_to else None,
        )
        return mf


def _extract_department_from_query(query: str) -> str | None:
    """Detect explicit department mentions in the query."""
    query_lower = query.lower()
    dept_patterns = {
        "hr": [r"\bhr\b", r"\bhuman\s+resources?\b", r"\bpeople\s+ops?\b"],
        "finance": [r"\bfinance\b", r"\bfinancial\b", r"\baccounting\b", r"\bbudget\b"],
        "sales": [r"\bsales\b", r"\brevenue\b", r"\bpipeline\b", r"\bdeals?\b"],
        "marketing": [r"\bmarketing\b", r"\bcampaign\b", r"\bbrand\b"],
    }
    for dept, patterns in dept_patterns.items():
        if any(re.search(p, query_lower) for p in patterns):
            return dept
    return None


def _extract_content_type(query: str) -> str | None:
    """Detect content type hints in the query."""
    query_lower = query.lower()
    for keyword, content_type in _CONTENT_TYPE_HINTS.items():
        if keyword in query_lower:
            return content_type
    return None


def _extract_date_range(query: str) -> tuple[datetime | None, datetime | None]:
    """Parse temporal expressions to a (date_from, date_to) range."""
    query_lower = query.lower()
    now = datetime.now(timezone.utc)

    # last N days
    m = re.search(r"\blast\s+(\d+)\s+days?\b", query_lower)
    if m:
        n = int(m.group(1))
        return now - timedelta(days=n), now

    # this week
    if re.search(r"\bthis\s+week\b", query_lower):
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0), now

    # last week
    if re.search(r"\blast\s+week\b", query_lower):
        start = now - timedelta(days=now.weekday() + 7)
        end = start + timedelta(days=6, hours=23, minutes=59)
        return start, end

    # this month
    if re.search(r"\bthis\s+month\b", query_lower):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    # last month
    if re.search(r"\blast\s+month\b", query_lower):
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return first_prev, last_prev

    # this quarter
    if re.search(r"\bthis\s+quarter\b", query_lower):
        q = (now.month - 1) // 3
        start_month = q * 3 + 1
        start = now.replace(month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    # last quarter
    if re.search(r"\blast\s+quarter\b", query_lower):
        q = (now.month - 1) // 3
        if q == 0:
            prev_q_start_month = 10
            year = now.year - 1
        else:
            prev_q_start_month = (q - 1) * 3 + 1
            year = now.year
        start = now.replace(year=year, month=prev_q_start_month, day=1,
                            hour=0, minute=0, second=0, microsecond=0)
        end_month = prev_q_start_month + 3
        if end_month > 12:
            end = now.replace(year=year + 1, month=1, day=1,
                              hour=0, minute=0, second=0, microsecond=0)
        else:
            end = now.replace(year=year, month=end_month, day=1,
                              hour=0, minute=0, second=0, microsecond=0)
        end -= timedelta(seconds=1)
        return start, end

    # this year
    if re.search(r"\bthis\s+year\b", query_lower):
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    # last year
    if re.search(r"\blast\s+year\b", query_lower):
        start = now.replace(year=now.year - 1, month=1, day=1,
                            hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(year=now.year - 1, month=12, day=31,
                          hour=23, minute=59, second=59, microsecond=0)
        return start, end

    # in Q1/Q2/Q3/Q4 YYYY
    m = re.search(r"\bin\s+Q([1-4])\s+(\d{4})\b", query_lower)
    if m:
        q_num = int(m.group(1))
        year = int(m.group(2))
        start_month = (q_num - 1) * 3 + 1
        end_month = start_month + 3
        start = datetime(year, start_month, 1, tzinfo=timezone.utc)
        if end_month > 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
        else:
            end = datetime(year, end_month, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
        return start, end

    # since YYYY
    m = re.search(r"\bsince\s+(\d{4})\b", query_lower)
    if m:
        year = int(m.group(1))
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        return start, now

    # in YYYY
    m = re.search(r"\bin\s+(\d{4})\b", query_lower)
    if m:
        year = int(m.group(1))
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        return start, end

    return None, None
