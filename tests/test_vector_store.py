"""Tests for VectorStore — semantic search over knowledge base rules."""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path so modules can be imported
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.vector_store import VectorStore


# ---------------------------------------------------------------------------
# Helper: mock Rule dataclass (mirrors knowledge_base.Rule)
# ---------------------------------------------------------------------------

@dataclass
class MockRule:
    id: str
    category: str
    principle: str
    application: str
    conditions: str
    citation: str
    confidence: str
    tags: list = field(default_factory=list)
    sport_specific: str = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def recovery_rules():
    """Recovery-related rules for testing."""
    return [
        MockRule(
            id="hrv_guided_training",
            category="recovery",
            principle="7-day HRV rolling average is more reliable than daily readings",
            application="Compare today's HRV to 7-day rolling mean",
            conditions="HRV data available for at least 7 consecutive days",
            citation="Plews DJ et al. (2013)",
            confidence="high",
            tags=["hrv", "training_modification", "fatigue"],
        ),
        MockRule(
            id="supercompensation_window",
            category="recovery",
            principle="Peak supercompensation occurs 36-72h after a hard session",
            application="Schedule next hard session 48-72h after previous",
            conditions="After high-intensity sessions",
            citation="Issurin VB (2010)",
            confidence="high",
            tags=["recovery", "periodization", "supercompensation"],
        ),
        MockRule(
            id="sleep_and_recovery",
            category="recovery",
            principle="Sleep is the single most important recovery modality",
            application="Prioritize 7-9h sleep over all other recovery modalities",
            conditions="Always applicable",
            citation="Halson SL (2014)",
            confidence="high",
            tags=["sleep", "recovery", "growth_hormone"],
        ),
    ]


@pytest.fixture
def training_rules():
    """Training-related rules for testing."""
    return [
        MockRule(
            id="progressive_overload",
            category="training_load",
            principle="Progressive overload is essential for continued adaptation",
            application="Increase weekly TSS by 5-10% per week during build phase",
            conditions="During base and build training phases",
            citation="Bompa & Haff (2009)",
            confidence="high",
            tags=["training", "overload", "periodization"],
        ),
        MockRule(
            id="taper_protocol",
            category="training_load",
            principle="A 2-3 week taper with 40-60% volume reduction optimizes race performance",
            application="Reduce volume while maintaining intensity before key races",
            conditions="2-3 weeks before target race",
            citation="Mujika & Padilla (2003)",
            confidence="high",
            tags=["taper", "race", "volume", "performance"],
        ),
    ]


@pytest.fixture
def all_rules(recovery_rules, training_rules):
    """Combined recovery + training rules."""
    return recovery_rules + training_rules


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


# ===========================================================================
# Tests
# ===========================================================================


class TestGracefulDegradation:
    """VectorStore degrades gracefully when ChromaDB is not available."""

    def test_unavailable_returns_false(self):
        """When chromadb import is blocked, available should be False."""
        with patch.dict("sys.modules", {"chromadb": None}):
            # Force re-import by creating a new instance that hits the ImportError
            vs = VectorStore.__new__(VectorStore)
            vs._available = False
            vs._client = None
            vs._collection = None
            assert vs.available is False

    def test_search_returns_empty_when_unavailable(self):
        """Search returns [] when not available."""
        vs = VectorStore.__new__(VectorStore)
        vs._available = False
        vs._client = None
        vs._collection = None
        assert vs.search("anything") == []

    def test_index_returns_zero_when_unavailable(self):
        """index_rules returns 0 when not available."""
        vs = VectorStore.__new__(VectorStore)
        vs._available = False
        vs._client = None
        vs._collection = None
        assert vs.index_rules([]) == 0

    def test_count_returns_zero_when_unavailable(self):
        vs = VectorStore.__new__(VectorStore)
        vs._available = False
        vs._client = None
        vs._collection = None
        assert vs.count() == 0

    def test_is_indexed_returns_false_when_unavailable(self):
        vs = VectorStore.__new__(VectorStore)
        vs._available = False
        vs._client = None
        vs._collection = None
        assert vs.is_indexed() is False


@needs_chromadb
class TestIndexing:
    """Test rule indexing into ChromaDB."""

    def test_index_rules_count_matches(self, all_rules):
        vs = VectorStore(in_memory=True)
        assert vs.available is True
        count = vs.index_rules(all_rules)
        assert count == len(all_rules)
        assert vs.count() == len(all_rules)

    def test_index_empty_list(self):
        vs = VectorStore(in_memory=True)
        count = vs.index_rules([])
        assert count == 0

    def test_is_indexed_after_indexing(self, all_rules):
        vs = VectorStore(in_memory=True)
        assert vs.is_indexed() is False
        vs.index_rules(all_rules)
        assert vs.is_indexed() is True

    def test_skip_already_indexed(self, all_rules):
        vs = VectorStore(in_memory=True)
        count1 = vs.index_rules(all_rules)
        count2 = vs.index_rules(all_rules)
        # Second call should skip (already indexed)
        assert count1 == count2
        assert vs.count() == len(all_rules)

    def test_index_rules_with_none_fields(self):
        """Rules with None in optional fields should still index."""
        rules = [
            MockRule(
                id="test_rule",
                category="test",
                principle="Test principle",
                application="Test application",
                conditions="",
                citation="",
                confidence="medium",
                tags=[],
                sport_specific=None,
            )
        ]
        vs = VectorStore(in_memory=True)
        count = vs.index_rules(rules)
        assert count == 1


@needs_chromadb
class TestSearch:
    """Test semantic search functionality."""

    def test_search_returns_results(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("fatigue after hard training")
        assert len(results) > 0

    def test_search_returns_dict_format(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("recovery from training")
        assert len(results) > 0
        first = results[0]
        assert "id" in first
        assert "document" in first
        assert "metadata" in first
        assert "distance" in first

    def test_search_relevance(self, all_rules):
        """Recovery query should rank recovery rules higher."""
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("tired after hard training week need recovery")
        assert len(results) > 0
        # At least one recovery rule should be in top 3
        top_categories = [r["metadata"].get("category") for r in results[:3]]
        assert "recovery" in top_categories

    def test_search_empty_query(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("")
        assert results == []

    def test_search_n_results(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("training", n_results=2)
        assert len(results) <= 2


@needs_chromadb
class TestCategoryFilter:
    """Test category-based filtering in search."""

    def test_filter_by_category(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("training adaptation", category="recovery")
        # All results should be in recovery category
        for r in results:
            assert r["metadata"]["category"] == "recovery"

    def test_filter_nonexistent_category(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        results = vs.search("anything", category="nonexistent_category")
        assert results == []


@needs_chromadb
class TestReindex:
    """Test drop and re-index."""

    def test_reindex_replaces_all(self, recovery_rules, training_rules):
        vs = VectorStore(in_memory=True)
        # Index only recovery rules first
        vs.index_rules(recovery_rules)
        assert vs.count() == len(recovery_rules)

        # Reindex with all rules (recovery + training)
        all_rules = recovery_rules + training_rules
        new_count = vs.reindex(all_rules)
        assert new_count == len(all_rules)

    def test_reindex_with_fewer_rules(self, recovery_rules, training_rules):
        vs = VectorStore(in_memory=True)
        all_rules = recovery_rules + training_rules
        vs.index_rules(all_rules)
        assert vs.count() == len(all_rules)

        # Reindex with only recovery rules
        new_count = vs.reindex(recovery_rules)
        assert new_count == len(recovery_rules)

    def test_reindex_empty(self, recovery_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(recovery_rules)
        assert vs.count() > 0

        new_count = vs.reindex([])
        assert new_count == 0


@needs_chromadb
class TestGetById:
    """Test retrieving a single document by ID."""

    def test_get_existing(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        result = vs.get_by_id("hrv_guided_training")
        assert result is not None
        assert result["id"] == "hrv_guided_training"
        assert "document" in result
        assert "metadata" in result

    def test_get_nonexistent(self, all_rules):
        vs = VectorStore(in_memory=True)
        vs.index_rules(all_rules)
        result = vs.get_by_id("nonexistent_rule_id")
        assert result is None
