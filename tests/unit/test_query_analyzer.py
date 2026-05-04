"""Unit tests for query analyzer fallback logic."""

from __future__ import annotations

import pytest

from src.retrieval.query_analyzer import QueryAnalyzer


class TestQueryAnalyzerFallback:
    """Test the rule-based fallback when LLM is unavailable."""

    def setup_method(self):
        self.analyzer = QueryAnalyzer()

    def test_factual_query(self):
        result = self.analyzer._fallback_analyze("What is our PTO policy?")
        assert result.query_type == "factual"

    def test_procedural_query(self):
        result = self.analyzer._fallback_analyze("How to submit an expense report?")
        assert result.query_type == "procedural"

    def test_analytical_query(self):
        result = self.analyzer._fallback_analyze("Compare Q3 vs Q4 sales trends")
        assert result.query_type == "analytical"
        assert result.retrieval_strategy == "hyde_cosine"

    def test_generative_query(self):
        result = self.analyzer._fallback_analyze(
            "Create a presentation about Q4 sales"
        )
        assert result.query_type == "generative"
        assert result.requires_document_generation is True
        assert result.document_type == "pptx"

    def test_hr_department_routing(self):
        result = self.analyzer._fallback_analyze("What is our leave policy?")
        assert result.target_department == "hr"

    def test_finance_department_routing(self):
        result = self.analyzer._fallback_analyze("Show me the latest budget report")
        assert result.target_department == "finance"

    def test_sales_department_routing(self):
        result = self.analyzer._fallback_analyze("What's the current pipeline value?")
        assert result.target_department == "sales"

    def test_marketing_department_routing(self):
        result = self.analyzer._fallback_analyze("What campaigns are running?")
        assert result.target_department == "marketing"

    def test_general_fallback(self):
        result = self.analyzer._fallback_analyze("Tell me about the company")
        assert result.target_department is None

    def test_docx_generation(self):
        result = self.analyzer._fallback_analyze("Generate a report document")
        assert result.document_type == "docx"

    def test_xlsx_generation(self):
        result = self.analyzer._fallback_analyze("Create a spreadsheet with budget data")
        assert result.document_type == "xlsx"

    def test_mmr_strategy_for_procedural(self):
        result = self.analyzer._fallback_analyze("Steps to onboard a new employee")
        assert result.retrieval_strategy == "mmr"
