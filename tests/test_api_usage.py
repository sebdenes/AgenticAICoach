"""Tests for API usage tracking — logging, summaries, cost calculation."""

import tempfile
from datetime import datetime, timedelta

import pytest

from database import Database


@pytest.fixture
def db():
    """Fresh database with api_usage table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    d = Database(db_path)
    from migrations.runner import run_migrations
    run_migrations(db_path)
    return d


# ── Basic logging ────────────────────────────────────────────

def test_log_api_usage(db):
    db.log_api_usage(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        endpoint="respond",
        input_tokens=1000,
        output_tokens=500,
        agent="daily_coach",
    )
    summary = db.get_usage_summary(days=1)
    assert len(summary) == 1
    assert summary[0]["provider"] == "anthropic"
    assert summary[0]["total_input"] == 1000
    assert summary[0]["total_output"] == 500
    assert summary[0]["calls"] == 1


def test_cost_calculation(db):
    """Verify cost is computed from hardcoded rates."""
    db.log_api_usage(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        endpoint="respond",
        input_tokens=1000,
        output_tokens=1000,
    )
    summary = db.get_usage_summary(days=1)
    # Sonnet: input $0.003/1K + output $0.015/1K = $0.018
    expected_cost = (1000 * 0.003 + 1000 * 0.015) / 1000
    assert abs(summary[0]["total_cost"] - expected_cost) < 0.0001


def test_haiku_cost_calculation(db):
    db.log_api_usage(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        endpoint="memory_extraction",
        input_tokens=500,
        output_tokens=100,
    )
    summary = db.get_usage_summary(days=1)
    # Haiku: input $0.0008/1K + output $0.004/1K
    expected = (500 * 0.0008 + 100 * 0.004) / 1000
    assert abs(summary[0]["total_cost"] - expected) < 0.0001


# ── Aggregation ──────────────────────────────────────────────

def test_multiple_calls_aggregate(db):
    for _ in range(5):
        db.log_api_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            endpoint="respond",
            input_tokens=200,
            output_tokens=100,
        )
    summary = db.get_usage_summary(days=1)
    assert summary[0]["calls"] == 5
    assert summary[0]["total_input"] == 1000
    assert summary[0]["total_output"] == 500


def test_daily_cost_breakdown(db):
    db.log_api_usage(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        endpoint="respond",
        input_tokens=1000,
        output_tokens=500,
    )
    daily = db.get_daily_cost(days=1)
    assert len(daily) >= 1
    assert daily[0]["date"] == datetime.now().strftime("%Y-%m-%d")
    assert daily[0]["calls"] == 1


def test_empty_usage_returns_empty(db):
    summary = db.get_usage_summary(days=7)
    assert summary == []
    daily = db.get_daily_cost(days=7)
    assert daily == []


# ── Unknown model ────────────────────────────────────────────

def test_unknown_model_zero_cost(db):
    """Unknown model should log with $0 cost."""
    db.log_api_usage(
        provider="intervals",
        model=None,
        endpoint="wellness",
        input_tokens=0,
        output_tokens=0,
    )
    summary = db.get_usage_summary(days=1)
    assert summary[0]["total_cost"] == 0.0
