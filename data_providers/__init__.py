"""Data provider abstraction layer — plug-and-play data sources."""

from data_providers.base import DataProvider, NormalizedRecord, MetricType
from data_providers.aggregator import DataAggregator

__all__ = ["DataProvider", "NormalizedRecord", "MetricType", "DataAggregator"]
