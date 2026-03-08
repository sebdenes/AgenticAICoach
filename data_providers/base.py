"""Base types for the data provider abstraction layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Protocol, runtime_checkable


class MetricType(str, Enum):
    """Standardised metric names across all data sources."""
    # Wellness
    HRV_RMSSD_MS = "hrv_rmssd_ms"
    RHR = "resting_heart_rate"
    SPO2 = "spo2_percentage"
    SKIN_TEMP_C = "skin_temp_celsius"
    RECOVERY_SCORE = "recovery_score"
    BODY_WEIGHT_KG = "body_weight_kg"

    # Sleep
    SLEEP_DURATION_S = "sleep_duration_seconds"
    SLEEP_IN_BED_S = "in_bed_seconds"
    SLEEP_REM_S = "rem_seconds"
    SLEEP_DEEP_S = "deep_seconds"
    SLEEP_LIGHT_S = "light_seconds"
    SLEEP_AWAKE_S = "awake_seconds"
    SLEEP_SCORE = "sleep_score"
    SLEEP_EFFICIENCY = "sleep_efficiency"
    SLEEP_PERFORMANCE = "sleep_performance"
    SLEEP_RESP_RATE = "respiratory_rate"
    SLEEP_NEED_S = "sleep_need_seconds"
    SLEEP_DEBT_S = "sleep_debt_seconds"

    # Training load
    CTL = "ctl"
    ATL = "atl"
    TSB = "tsb"
    TSS = "training_stress_score"
    INTENSITY_FACTOR = "intensity_factor"
    STRAIN = "strain"

    # Activity
    DURATION_S = "duration_seconds"
    DISTANCE_M = "distance_meters"
    AVG_HR = "average_heart_rate"
    MAX_HR = "max_heart_rate"
    AVG_POWER = "average_power"
    NORM_POWER = "normalized_power"
    CALORIES = "calories"
    ELEVATION_M = "elevation_meters"
    AVG_PACE = "average_pace"
    SPORT = "sport"


@dataclass
class NormalizedRecord:
    """A single data point normalised across all sources."""
    timestamp: datetime
    category: str            # "sleep", "wellness", "activity", "recovery"
    source: str              # "whoop", "intervals", "garmin", ...
    metrics: dict            # MetricType -> value
    confidence: float = 0.8  # 0-1, source reliability for this data type
    raw: dict = field(default_factory=dict)

    @property
    def date_str(self) -> str:
        return self.timestamp.strftime("%Y-%m-%d")

    def get(self, metric: MetricType | str, default=None):
        """Get a metric value by MetricType or string key."""
        key = metric.value if isinstance(metric, MetricType) else metric
        return self.metrics.get(key, default)


@runtime_checkable
class DataProvider(Protocol):
    """Protocol that all data source providers must implement."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. 'whoop', 'intervals')."""
        ...

    @property
    def supported_categories(self) -> list[str]:
        """List of categories this provider can supply."""
        ...

    async def fetch_wellness(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch wellness data (HRV, RHR, recovery) for the date range."""
        ...

    async def fetch_activities(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch activity/workout data for the date range."""
        ...

    async def fetch_sleep(self, start: date, end: date) -> list[NormalizedRecord]:
        """Fetch sleep data for the date range."""
        ...

    async def is_connected(self) -> bool:
        """Check whether this provider is authenticated and reachable."""
        ...
