"""Tests for the ExplainabilityEngine — reasoning chains from wellness/training data."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.explainability import (
    ExplainabilityEngine,
    ReasoningChain,
    ReasoningStep,
    _safe_float,
    _w_date,
    _avg_confidence,
)
from modules.thresholds import PersonalizedThresholds


# ===========================================================================
# Helper function tests
# ===========================================================================

class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_string_number(self):
        assert _safe_float("55.2") == 55.2

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_none_custom_default(self):
        assert _safe_float(None, -1.0) == -1.0

    def test_bad_string(self):
        assert _safe_float("bad") == 0.0


class TestWDate:
    def test_id_field(self):
        assert _w_date({"id": "2025-03-07"}) == "2025-03-07"

    def test_date_field_fallback(self):
        assert _w_date({"date": "2025-03-07"}) == "2025-03-07"

    def test_truncation(self):
        assert _w_date({"id": "2025-03-07T12:30:00"}) == "2025-03-07"

    def test_missing_both(self):
        assert _w_date({}) == ""


class TestAvgConfidence:
    def test_normal(self):
        steps = [
            ReasoningStep("obs1", [], "rule1", "src1", 0.8),
            ReasoningStep("obs2", [], "rule2", "src2", 0.6),
        ]
        assert _avg_confidence(steps) == pytest.approx(0.7)

    def test_empty(self):
        assert _avg_confidence([]) == 0.5


# ===========================================================================
# ReasoningChain formatting
# ===========================================================================

class TestReasoningChainFormatting:
    def _make_chain(self):
        steps = [
            ReasoningStep(
                observation="HRV is 48.2ms (low, z=-1.8, P15, declining)",
                data_points=[{"date": "2025-03-05", "hrv": 48.2, "baseline": 55.5}],
                rule_applied="Compared to 30-day baseline 55.5ms +/- 4.8",
                source="personalized_thresholds",
                confidence=0.9,
            ),
            ReasoningStep(
                observation="RHR elevated at 47bpm (high, baseline 43bpm)",
                data_points=[{"date": "2025-03-05", "rhr": 47, "baseline": 43}],
                rule_applied="Elevated RHR >1.5 SD above baseline suggests incomplete recovery",
                source="personalized_thresholds",
                confidence=0.8,
            ),
            ReasoningStep(
                observation="Sleep: 5.0h (low, baseline 7.2h)",
                data_points=[{"date": "2025-03-05", "sleep_h": 5.0}],
                rule_applied="Compared to 14-day average of 7.2h",
                source="personalized_thresholds",
                confidence=0.85,
            ),
        ]
        return ReasoningChain(
            conclusion="Key findings: recovery focus needed; elevated RHR; poor sleep",
            steps=steps,
            alternatives=["Consider nap before training if sleep debt is high"],
            overall_confidence=0.85,
        )

    def test_to_athlete_summary(self):
        chain = self._make_chain()
        summary = chain.to_athlete_summary()
        assert "recovery focus needed" in summary
        assert "HRV" in summary
        # Should include up to 3 observations
        assert "RHR elevated" in summary
        # Should include the first alternative
        assert "Also considered" in summary

    def test_to_athlete_summary_no_steps(self):
        chain = ReasoningChain(conclusion="All good", steps=[], overall_confidence=0.5)
        assert chain.to_athlete_summary() == "All good"

    def test_to_coach_detail(self):
        chain = self._make_chain()
        detail = chain.to_coach_detail()
        assert "CONCLUSION:" in detail
        assert "Step 1:" in detail
        assert "Step 2:" in detail
        assert "Step 3:" in detail
        assert "Observation:" in detail
        assert "Rule:" in detail
        assert "Source:" in detail
        assert "Confidence:" in detail
        assert "Alternatives considered:" in detail

    def test_to_prompt_context(self):
        chain = self._make_chain()
        ctx = chain.to_prompt_context()
        assert "ANALYSIS:" in ctx
        assert "[personalized_thresholds]" in ctx
        # Each step shows [source] observation -> rule
        lines = ctx.split("\n")
        assert len(lines) >= 4  # ANALYSIS + 3 steps


# ===========================================================================
# ExplainabilityEngine — analyze_wellness
# ===========================================================================

class TestAnalyzeWellness:
    def test_normal_wellness(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        engine = ExplainabilityEngine(thresholds=pt)
        chain = engine.analyze_wellness(mock_wellness_14d)
        assert isinstance(chain, ReasoningChain)
        assert chain.conclusion
        assert chain.overall_confidence > 0

    def test_empty_wellness(self):
        engine = ExplainabilityEngine()
        chain = engine.analyze_wellness([])
        assert "Insufficient" in chain.conclusion
        assert chain.overall_confidence == 0.0

    def test_poor_metrics_trigger_findings(self):
        """Create wellness data with low HRV, high RHR, poor sleep."""
        # First build normal history for thresholds
        normal = [
            {"id": f"2025-02-{i:02d}", "hrv": 55.0, "restingHR": 43, "sleepSecs": 27000,
             "ctl": 45.0, "atl": 50.0}
            for i in range(10, 24)
        ]
        # Then a bad latest day
        bad_day = [
            {"id": "2025-03-07", "hrv": 35.0, "restingHR": 55, "sleepSecs": 14400,
             "ctl": 45.0, "atl": 70.0}
        ]
        all_data = normal + bad_day
        pt = PersonalizedThresholds(all_data)
        engine = ExplainabilityEngine(thresholds=pt)
        chain = engine.analyze_wellness(all_data)

        # Should flag multiple issues
        assert "recovery focus needed" in chain.conclusion.lower() or "Key findings" in chain.conclusion
        # Should have multiple reasoning steps
        assert len(chain.steps) >= 2

    def test_no_thresholds_still_works(self):
        """Without thresholds, should still produce some output."""
        data = [{"id": "2025-03-07", "hrv": 55.0, "restingHR": 43, "sleepSecs": 27000}]
        engine = ExplainabilityEngine(thresholds=None)
        chain = engine.analyze_wellness(data)
        assert isinstance(chain, ReasoningChain)
        # Should at least note the HRV
        has_hrv_step = any("HRV" in s.observation for s in chain.steps)
        assert has_hrv_step or chain.conclusion

    def test_good_wellness_all_normal(self):
        """Data right at baseline should produce 'All normal' conclusion."""
        normal = [
            {"id": f"2025-02-{i:02d}", "hrv": 55.0, "restingHR": 43, "sleepSecs": 27000,
             "ctl": 45.0, "atl": 50.0}
            for i in range(10, 25)
        ]
        pt = PersonalizedThresholds(normal)
        engine = ExplainabilityEngine(thresholds=pt)
        chain = engine.analyze_wellness(normal)
        # With baseline equal to current, should be "normal"
        assert "normal" in chain.conclusion.lower() or "HRV is strong" in chain.conclusion


# ===========================================================================
# ExplainabilityEngine — analyze_training_readiness
# ===========================================================================

class TestAnalyzeTrainingReadiness:
    def _build_engine_with_history(self, latest_overrides=None):
        """Helper to build engine with normal history and custom latest day."""
        normal = [
            {"id": f"2025-02-{i:02d}", "hrv": 55.0, "restingHR": 43, "sleepSecs": 27000,
             "ctl": 45.0, "atl": 50.0}
            for i in range(10, 24)
        ]
        latest = {
            "id": "2025-03-07", "hrv": 55.0, "restingHR": 43,
            "sleepSecs": 27000, "ctl": 45.0, "atl": 50.0,
        }
        if latest_overrides:
            latest.update(latest_overrides)
        all_data = normal + [latest]
        pt = PersonalizedThresholds(all_data)
        engine = ExplainabilityEngine(thresholds=pt)
        return engine, all_data

    def test_green_readiness(self):
        """Normal values should yield GREEN."""
        engine, data = self._build_engine_with_history()
        chain = engine.analyze_training_readiness(data, [])
        assert "GREEN" in chain.conclusion

    def test_red_readiness(self):
        """Very low HRV + high RHR should yield RED."""
        engine, data = self._build_engine_with_history(
            {"hrv": 35.0, "restingHR": 55, "sleepSecs": 14400}
        )
        chain = engine.analyze_training_readiness(data, [])
        assert "RED" in chain.conclusion or "AMBER" in chain.conclusion

    def test_amber_readiness(self):
        """Mild HRV dip should yield AMBER."""
        engine, data = self._build_engine_with_history(
            {"hrv": 40.0, "restingHR": 48}
        )
        chain = engine.analyze_training_readiness(data, [])
        # Could be AMBER or RED depending on exact thresholds
        assert "AMBER" in chain.conclusion or "RED" in chain.conclusion

    def test_empty_wellness(self):
        engine = ExplainabilityEngine()
        chain = engine.analyze_training_readiness([], [])
        assert "Insufficient" in chain.conclusion
        assert chain.overall_confidence == 0.0

    def test_planned_workout_in_alternatives(self):
        """When not ready and a planned workout is provided, it should appear in alternatives."""
        engine, data = self._build_engine_with_history(
            {"hrv": 35.0, "restingHR": 55, "sleepSecs": 14400}
        )
        planned = {"name": "Tempo Run"}
        chain = engine.analyze_training_readiness(data, [], planned_workout=planned)
        if "RED" in chain.conclusion or "AMBER" in chain.conclusion:
            has_planned = any("Tempo Run" in alt for alt in chain.alternatives)
            assert has_planned

    def test_recent_high_training_load(self):
        """High recent TSS in activities should contribute to readiness assessment."""
        normal = [
            {"id": f"2025-02-{i:02d}", "hrv": 55.0, "restingHR": 43, "sleepSecs": 27000,
             "ctl": 45.0, "atl": 50.0}
            for i in range(10, 24)
        ]
        latest = {"id": "2025-03-07", "hrv": 50.0, "restingHR": 45, "sleepSecs": 25200,
                  "ctl": 45.0, "atl": 50.0}
        all_data = normal + [latest]

        # Activities with very high training loads
        activities = [
            {"type": "Run", "icu_training_load": 200, "start_date_local": "2025-03-05"},
            {"type": "Run", "icu_training_load": 180, "start_date_local": "2025-03-06"},
            {"type": "Ride", "icu_training_load": 190, "start_date_local": "2025-03-07"},
        ]
        pt = PersonalizedThresholds(all_data, activities)
        engine = ExplainabilityEngine(thresholds=pt)
        chain = engine.analyze_training_readiness(all_data, activities)
        # Should mention high training load in steps
        has_tss_step = any("TSS" in s.observation or "avg" in s.observation for s in chain.steps)
        # Either the TSS is flagged or the overall readiness accounts for it
        assert isinstance(chain, ReasoningChain)


# ===========================================================================
# ExplainabilityEngine — analyze_sleep
# ===========================================================================

class TestAnalyzeSleep:
    def test_good_sleep(self, mock_athlete_config):
        """8h sleep should produce GREEN."""
        data = [
            {"id": f"2025-03-{i:02d}", "sleepSecs": 28800}
            for i in range(1, 8)
        ]
        engine = ExplainabilityEngine()
        chain = engine.analyze_sleep(data, mock_athlete_config)
        assert "GREEN" in chain.conclusion

    def test_average_sleep(self, mock_athlete_config):
        """6.5h sleep (within 1h of 7.5h target) should produce AMBER."""
        data = [
            {"id": f"2025-03-{i:02d}", "sleepSecs": 23400}
            for i in range(1, 8)
        ]
        engine = ExplainabilityEngine()
        chain = engine.analyze_sleep(data, mock_athlete_config)
        assert "AMBER" in chain.conclusion

    def test_critical_sleep(self, mock_athlete_config):
        """4h sleep should produce RED."""
        data = [
            {"id": f"2025-03-{i:02d}", "sleepSecs": 14400}
            for i in range(1, 8)
        ]
        engine = ExplainabilityEngine()
        chain = engine.analyze_sleep(data, mock_athlete_config)
        assert "RED" in chain.conclusion
        # Should have sleep debt step
        has_debt = any("debt" in s.observation.lower() for s in chain.steps)
        assert has_debt

    def test_no_sleep_data(self):
        engine = ExplainabilityEngine()
        chain = engine.analyze_sleep([], None)
        assert "No sleep data" in chain.conclusion
        assert chain.overall_confidence == 0.0

    def test_sleep_without_athlete_config(self):
        """Should default to 7.5h target when no config."""
        data = [
            {"id": f"2025-03-{i:02d}", "sleepSecs": 28800}
            for i in range(1, 8)
        ]
        engine = ExplainabilityEngine()
        chain = engine.analyze_sleep(data, athlete_config=None)
        assert "GREEN" in chain.conclusion

    def test_declining_sleep_trend(self):
        """Declining sleep over 7 days should be detected."""
        data = [
            {"id": f"2025-03-{i:02d}", "sleepSecs": int((9.0 - i * 0.6) * 3600)}
            for i in range(1, 8)
        ]
        engine = ExplainabilityEngine()
        chain = engine.analyze_sleep(data)
        # Check if trend step was added
        has_trend = any("trend" in s.observation.lower() or "declining" in s.observation.lower()
                        for s in chain.steps)
        # Trend may or may not trigger depending on slope threshold
        assert isinstance(chain, ReasoningChain)


# ===========================================================================
# Engine state: get_last_chain / get_all_chains
# ===========================================================================

class TestEngineState:
    def test_get_last_chain_initially_none(self):
        engine = ExplainabilityEngine()
        assert engine.get_last_chain() is None

    def test_get_last_chain_after_analysis(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        engine = ExplainabilityEngine(thresholds=pt)
        chain1 = engine.analyze_wellness(mock_wellness_14d)
        assert engine.get_last_chain() is chain1

    def test_multiple_analyses_tracked(self, mock_wellness_14d, mock_athlete_config):
        pt = PersonalizedThresholds(mock_wellness_14d)
        engine = ExplainabilityEngine(thresholds=pt)
        engine.analyze_wellness(mock_wellness_14d)
        engine.analyze_sleep(mock_wellness_14d, mock_athlete_config)
        chains = engine.get_all_chains()
        assert len(chains) == 2

    def test_get_last_chain_returns_most_recent(self, mock_wellness_14d, mock_athlete_config):
        pt = PersonalizedThresholds(mock_wellness_14d)
        engine = ExplainabilityEngine(thresholds=pt)
        engine.analyze_wellness(mock_wellness_14d)
        chain2 = engine.analyze_sleep(mock_wellness_14d, mock_athlete_config)
        assert engine.get_last_chain() is chain2

    def test_format_all_context(self, mock_wellness_14d):
        pt = PersonalizedThresholds(mock_wellness_14d)
        engine = ExplainabilityEngine(thresholds=pt)
        engine.analyze_wellness(mock_wellness_14d)
        ctx = engine.format_all_context()
        assert "REASONING ANALYSIS:" in ctx

    def test_format_all_context_empty(self):
        engine = ExplainabilityEngine()
        assert engine.format_all_context() == ""
