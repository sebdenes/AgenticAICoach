"""Tests for RAG engine — combined vector + keyword retrieval."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path so modules can be imported
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.rag_engine import RAGEngine, _query_to_context
from modules.knowledge_base import KnowledgeBase


# ---------------------------------------------------------------------------
# Check if ChromaDB is available
# ---------------------------------------------------------------------------

def _chromadb_available():
    try:
        import chromadb  # noqa: F401
        return True
    except ImportError:
        return False


needs_chromadb = pytest.mark.skipif(
    not _chromadb_available(),
    reason="chromadb not installed"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def knowledge_base():
    """KnowledgeBase loaded from real YAML files."""
    kb_dir = str(Path(__file__).resolve().parent.parent / "knowledge")
    return KnowledgeBase(knowledge_dir=kb_dir)


@pytest.fixture
def rag_keyword_only(knowledge_base):
    """RAG engine without vector store (keyword-only)."""
    return RAGEngine(knowledge_base=knowledge_base, vector_store=None)


@pytest.fixture
def rag_with_vectors(knowledge_base):
    """RAG engine with in-memory vector store."""
    from modules.vector_store import VectorStore
    vs = VectorStore(in_memory=True)
    return RAGEngine(knowledge_base=knowledge_base, vector_store=vs)


# ===========================================================================
# Tests: _query_to_context helper
# ===========================================================================

class TestQueryToContext:
    def test_detects_hrv_metric(self):
        ctx = _query_to_context("my HRV has been declining this week")
        assert ctx.get("metric") == "hrv"
        assert ctx.get("status") == "declining"

    def test_detects_sleep(self):
        ctx = _query_to_context("I need help with sleep recovery")
        assert "sleep" in ctx.get("tags", []) or ctx.get("metric") == "sleep"

    def test_detects_fatigue(self):
        ctx = _query_to_context("I feel tired after hard training")
        assert ctx.get("status") in ("fatigued", "recovery")

    def test_respects_category_param(self):
        ctx = _query_to_context("anything", category="nutrition")
        assert ctx["category"] == "nutrition"

    def test_empty_query_produces_context(self):
        ctx = _query_to_context("some random question")
        # Should still produce some context (at least tags from words)
        assert len(ctx) > 0


# ===========================================================================
# Tests: Keyword-only retrieval
# ===========================================================================

class TestKeywordOnlyRetrieval:
    def test_retrieve_returns_string(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_context("recovery after hard training")
        assert isinstance(result, str)

    def test_retrieve_contains_guidelines(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_context("HRV declining need recovery")
        # Should contain the formatted guidelines header
        if result:
            assert "EVIDENCE-BASED GUIDELINES" in result

    def test_retrieve_with_category_filter(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_context(
            "training load", category="recovery"
        )
        assert isinstance(result, str)

    def test_retrieve_respects_max_rules(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_context(
            "recovery sleep training", max_rules=2
        )
        if result:
            # Count rule entries: each starts with [ in the formatted output
            rule_lines = [line for line in result.split("\n") if line.strip().startswith("[")]
            assert len(rule_lines) <= 2


class TestFallbackWithoutVectorStore:
    """Verify RAG works without vector store (keyword-only mode)."""

    def test_works_without_vector_store(self, knowledge_base):
        rag = RAGEngine(knowledge_base=knowledge_base, vector_store=None)
        result = rag.retrieve_context("recovery after hard session")
        assert isinstance(result, str)
        # Should still find rules via keyword search
        if len(knowledge_base.rules) > 0:
            assert len(result) > 0


# ===========================================================================
# Tests: Combined retrieval (vector + keyword)
# ===========================================================================

@needs_chromadb
class TestCombinedRetrieval:
    def test_returns_results(self, rag_with_vectors):
        result = rag_with_vectors.retrieve_context("tired after hard training week")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_deduplication(self, rag_with_vectors):
        """Same rule should not appear twice in output."""
        result = rag_with_vectors.retrieve_context("HRV recovery sleep")
        if result:
            lines = result.split("\n")
            rule_ids = []
            for line in lines:
                # Extract rule IDs from formatted output: [category/id]
                if "/" in line and line.strip().startswith("["):
                    parts = line.strip()
                    # Extract content between [ and ]
                    start = parts.index("[") + 1
                    end = parts.index("]")
                    rule_ref = parts[start:end]
                    rule_ids.append(rule_ref)
            # No duplicates
            assert len(rule_ids) == len(set(rule_ids))


# ===========================================================================
# Tests: augment_prompt
# ===========================================================================

class TestAugmentPrompt:
    def test_returns_string(self, rag_keyword_only):
        result = rag_keyword_only.augment_prompt("How should I recover after a hard week?")
        assert isinstance(result, str)

    def test_contains_science_header(self, rag_keyword_only):
        result = rag_keyword_only.augment_prompt("recovery after hard training")
        if result:
            assert "RELEVANT SPORTS SCIENCE" in result

    def test_includes_citations(self, rag_keyword_only):
        result = rag_keyword_only.augment_prompt("recovery after hard training")
        if result:
            assert "Citation:" in result

    def test_includes_data_context(self, rag_keyword_only):
        data_ctx = "CTL: 48.5 | ATL: 55.2 | TSB: -6.7"
        result = rag_keyword_only.augment_prompt(
            "Am I overtrained?", data_context=data_ctx
        )
        if result:
            assert data_ctx in result

    def test_empty_data_context(self, rag_keyword_only):
        result = rag_keyword_only.augment_prompt(
            "sleep quality", data_context=""
        )
        assert isinstance(result, str)


# ===========================================================================
# Tests: retrieve_for_session
# ===========================================================================

class TestRetrieveForSession:
    def test_returns_string(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_for_session("long_run")
        assert isinstance(result, str)

    def test_with_phase(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_for_session("long_run", phase="taper")
        assert isinstance(result, str)

    def test_with_conditions(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_for_session(
            "tempo_run", conditions={"fatigue": "high", "tsb": -15}
        )
        assert isinstance(result, str)

    def test_recovery_session_gets_recovery_rules(self, rag_keyword_only):
        result = rag_keyword_only.retrieve_for_session("recovery_run")
        if result:
            # Should contain recovery-related content
            lower_result = result.lower()
            assert "recovery" in lower_result or "rest" in lower_result or "GUIDELINES" in result
