"""Tests for the KnowledgeBase — sports science rules from YAML files."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.knowledge_base import KnowledgeBase, Rule


# ===========================================================================
# Loading and stats
# ===========================================================================

class TestKnowledgeBaseLoading:
    def test_loads_all_rules(self, knowledge_base):
        """Should load 75 rules from 6 YAML files (5 original + weather)."""
        assert len(knowledge_base.rules) == 75

    def test_six_categories(self, knowledge_base):
        """Should have exactly 6 categories."""
        assert len(knowledge_base.categories) == 6

    def test_category_names(self, knowledge_base):
        expected = {"marathon_specific", "nutrition", "recovery", "sleep", "training_load", "weather"}
        assert set(knowledge_base.categories) == expected

    def test_stats_total(self, knowledge_base):
        stats = knowledge_base.stats
        assert stats["total"] == 75

    def test_stats_by_category(self, knowledge_base):
        stats = knowledge_base.stats
        for cat, count in stats["by_category"].items():
            if cat == "weather":
                assert count == 15  # weather.yaml has 15 rules
            else:
                assert count == 12  # Other YAMLs have 12 rules each

    def test_stats_by_confidence(self, knowledge_base):
        stats = knowledge_base.stats
        # Should have high and medium confidence rules
        assert "high" in stats["by_confidence"]
        assert stats["by_confidence"]["high"] > 0

    def test_all_rules_have_required_fields(self, knowledge_base):
        for rule in knowledge_base.rules:
            assert rule.id, f"Rule missing id: {rule}"
            assert rule.category, f"Rule missing category: {rule.id}"
            assert rule.principle, f"Rule missing principle: {rule.id}"
            assert rule.application, f"Rule missing application: {rule.id}"
            assert rule.citation, f"Rule missing citation: {rule.id}"
            assert rule.confidence in ("high", "medium", "low"), (
                f"Invalid confidence for {rule.id}: {rule.confidence}"
            )


# ===========================================================================
# Query by category
# ===========================================================================

class TestQueryByCategory:
    def test_recovery_category(self, knowledge_base):
        results = knowledge_base.query(category="recovery")
        assert len(results) == 12
        assert all(r.category == "recovery" for r in results)

    def test_sleep_category(self, knowledge_base):
        results = knowledge_base.query(category="sleep")
        assert len(results) == 12
        assert all(r.category == "sleep" for r in results)

    def test_nonexistent_category(self, knowledge_base):
        results = knowledge_base.query(category="nonexistent")
        assert len(results) == 0

    def test_no_category_filter_returns_all(self, knowledge_base):
        results = knowledge_base.query()
        assert len(results) == 75


# ===========================================================================
# Query by tags
# ===========================================================================

class TestQueryByTags:
    def test_hrv_tag(self, knowledge_base):
        results = knowledge_base.query(tags=["hrv"])
        assert len(results) > 0
        for r in results:
            assert "hrv" in r.tags

    def test_multiple_tags_or_logic(self, knowledge_base):
        """Tags query uses OR logic — any matching tag counts."""
        results = knowledge_base.query(tags=["hrv", "sleep"])
        hrv_only = knowledge_base.query(tags=["hrv"])
        sleep_only = knowledge_base.query(tags=["sleep"])
        # Union should be at least as large as either individual set
        assert len(results) >= max(len(hrv_only), len(sleep_only))

    def test_nonexistent_tag(self, knowledge_base):
        results = knowledge_base.query(tags=["nonexistent_tag_xyz"])
        assert len(results) == 0

    def test_injury_prevention_tag(self, knowledge_base):
        results = knowledge_base.query(tags=["injury_prevention"])
        assert len(results) > 0


# ===========================================================================
# Query by sport
# ===========================================================================

class TestQueryBySport:
    def test_running_sport(self, knowledge_base):
        results = knowledge_base.query(sport="running")
        # Should include universal rules (sport_specific=None) + running-specific
        universal = [r for r in results if r.sport_specific is None]
        running = [r for r in results if r.sport_specific == "running"]
        assert len(universal) > 0
        assert len(running) > 0
        # Should NOT include marathon-specific (different sport value)
        for r in results:
            assert r.sport_specific in (None, "running")

    def test_marathon_sport(self, knowledge_base):
        results = knowledge_base.query(sport="marathon")
        marathon = [r for r in results if r.sport_specific == "marathon"]
        assert len(marathon) > 0

    def test_no_sport_returns_all(self, knowledge_base):
        results = knowledge_base.query()
        assert len(results) == 75


# ===========================================================================
# Query by confidence
# ===========================================================================

class TestQueryByConfidence:
    def test_high_confidence_only(self, knowledge_base):
        results = knowledge_base.query(confidence="high")
        assert all(r.confidence == "high" for r in results)
        assert len(results) > 0

    def test_medium_includes_high(self, knowledge_base):
        results = knowledge_base.query(confidence="medium")
        confidences = {r.confidence for r in results}
        assert "high" in confidences
        assert "medium" in confidences

    def test_sorted_by_confidence(self, knowledge_base):
        results = knowledge_base.query()
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        ranks = [conf_rank.get(r.confidence, 3) for r in results]
        assert ranks == sorted(ranks)


# ===========================================================================
# Get rule by ID
# ===========================================================================

class TestGetRule:
    def test_existing_rule(self, knowledge_base):
        rule = knowledge_base.get_rule("hrv_guided_training")
        assert rule is not None
        assert rule.id == "hrv_guided_training"
        assert rule.category == "recovery"
        assert rule.confidence == "high"

    def test_nonexistent_rule(self, knowledge_base):
        assert knowledge_base.get_rule("totally_fake_id") is None

    def test_rule_from_each_category(self, knowledge_base):
        ids_by_cat = {
            "recovery": "supercompensation_window",
            "sleep": "sleep_extension",
            "training_load": "acwr_sweet_spot",
            "nutrition": "carb_periodization",
            "marathon_specific": "long_run_guidelines",
        }
        for cat, rule_id in ids_by_cat.items():
            rule = knowledge_base.get_rule(rule_id)
            assert rule is not None, f"Rule {rule_id} not found"
            assert rule.category == cat


# ===========================================================================
# get_relevant_rules (smart retrieval)
# ===========================================================================

class TestGetRelevantRules:
    def test_hrv_context(self, knowledge_base):
        context = {"metric": "hrv", "status": "declining", "category": "recovery"}
        results = knowledge_base.get_relevant_rules(context, max_rules=5)
        assert len(results) > 0
        assert len(results) <= 5
        # Top result should be recovery-related with HRV tag
        top = results[0]
        assert "hrv" in top.tags or top.category == "recovery"

    def test_sleep_context(self, knowledge_base):
        context = {"metric": "sleep", "status": "poor", "category": "sleep"}
        results = knowledge_base.get_relevant_rules(context, max_rules=5)
        assert len(results) > 0
        assert results[0].category == "sleep" or any("sleep" in t for t in results[0].tags)

    def test_training_load_context(self, knowledge_base):
        context = {"metric": "training_load", "status": "high", "tags": ["acwr"]}
        results = knowledge_base.get_relevant_rules(context, max_rules=5)
        assert len(results) > 0

    def test_sport_context_boosts_sport_rules(self, knowledge_base):
        context = {"metric": "running", "sport": "marathon", "category": "marathon_specific"}
        results = knowledge_base.get_relevant_rules(context, max_rules=5)
        assert len(results) > 0
        marathon_rules = [r for r in results if r.sport_specific == "marathon"]
        assert len(marathon_rules) > 0

    def test_empty_context_returns_empty(self, knowledge_base):
        results = knowledge_base.get_relevant_rules({})
        assert results == []

    def test_max_rules_respected(self, knowledge_base):
        context = {"metric": "recovery", "category": "recovery", "tags": ["hrv"]}
        results = knowledge_base.get_relevant_rules(context, max_rules=3)
        assert len(results) <= 3


# ===========================================================================
# format_for_prompt
# ===========================================================================

class TestFormatForPrompt:
    def test_basic_format(self, knowledge_base):
        rules = knowledge_base.query(category="recovery")[:3]
        text = knowledge_base.format_for_prompt(rules)
        assert "EVIDENCE-BASED GUIDELINES:" in text
        assert "[recovery/" in text

    def test_empty_rules(self, knowledge_base):
        text = knowledge_base.format_for_prompt([])
        assert text == ""

    def test_max_rules_limit(self, knowledge_base):
        rules = knowledge_base.query(category="recovery")  # 12 rules
        text = knowledge_base.format_for_prompt(rules, max_rules=2)
        # Should only contain 2 rule entries (plus the header line)
        lines = [l for l in text.split("\n") if l.strip().startswith("[")]
        assert len(lines) == 2

    def test_includes_citation(self, knowledge_base):
        rules = knowledge_base.query(category="recovery")[:1]
        text = knowledge_base.format_for_prompt(rules)
        # Should reference the citation
        assert "Plews" in text or "Apply:" in text

    def test_format_citation_single_rule(self, knowledge_base):
        rule = knowledge_base.get_rule("hrv_guided_training")
        text = knowledge_base.format_citation(rule)
        assert "Application:" in text
        assert "Conditions:" in text
        assert "Source:" in text
        assert "confidence:" in text


# ===========================================================================
# Combined queries
# ===========================================================================

class TestCombinedQueries:
    def test_category_and_tags(self, knowledge_base):
        results = knowledge_base.query(category="recovery", tags=["hrv"])
        assert all(r.category == "recovery" for r in results)
        assert all("hrv" in r.tags for r in results)

    def test_category_and_confidence(self, knowledge_base):
        results = knowledge_base.query(category="sleep", confidence="high")
        assert all(r.category == "sleep" for r in results)
        assert all(r.confidence == "high" for r in results)

    def test_sport_and_confidence(self, knowledge_base):
        results = knowledge_base.query(sport="marathon", confidence="high")
        for r in results:
            assert r.sport_specific in (None, "marathon")
            assert r.confidence == "high"


# ===========================================================================
# Edge cases
# ===========================================================================

class TestKnowledgeBaseEdgeCases:
    def test_nonexistent_directory(self):
        """Loading from a missing directory should produce empty KB."""
        kb = KnowledgeBase(knowledge_dir="/nonexistent/path/xyz")
        assert len(kb.rules) == 0

    def test_empty_directory(self, tmp_path):
        """Loading from an empty directory should produce empty KB."""
        kb = KnowledgeBase(knowledge_dir=str(tmp_path))
        assert len(kb.rules) == 0
        assert kb.stats["total"] == 0
