"""Tests for weather module and weather provider."""

import sys
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.weather import WeatherConditions, TrainingAdjustment, WeatherEngine
from data_providers.weather_provider import WeatherProvider, WMO_CODES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine():
    """WeatherEngine without knowledge base."""
    return WeatherEngine()


@pytest.fixture
def mild_conditions():
    """Mild, ideal conditions: 15C, low wind, dry."""
    return WeatherConditions(
        temperature_c=15.0,
        feels_like_c=15.0,
        humidity_pct=50.0,
        wind_speed_kmh=10.0,
        wind_direction_deg=180.0,
        precipitation_mm=0.0,
        uv_index=3.0,
        weather_code=1,
        description="Mainly clear",
    )


@pytest.fixture
def hot_humid_conditions():
    """Hot and humid: 33C, 80% humidity."""
    return WeatherConditions(
        temperature_c=33.0,
        feels_like_c=38.0,
        humidity_pct=80.0,
        wind_speed_kmh=5.0,
        wind_direction_deg=90.0,
        precipitation_mm=0.0,
        uv_index=9.0,
        weather_code=0,
        description="Clear sky",
    )


@pytest.fixture
def cold_windy_conditions():
    """Cold with strong wind: -5C, 35 km/h wind."""
    return WeatherConditions(
        temperature_c=-5.0,
        feels_like_c=-12.0,
        humidity_pct=40.0,
        wind_speed_kmh=35.0,
        wind_direction_deg=315.0,
        precipitation_mm=0.0,
        uv_index=1.0,
        weather_code=3,
        description="Overcast",
    )


@pytest.fixture
def rainy_conditions():
    """Moderate rain with precipitation."""
    return WeatherConditions(
        temperature_c=12.0,
        feels_like_c=10.0,
        humidity_pct=90.0,
        wind_speed_kmh=20.0,
        wind_direction_deg=270.0,
        precipitation_mm=6.0,
        precipitation_probability=85.0,
        uv_index=1.0,
        weather_code=63,
        description="Moderate rain",
    )


@pytest.fixture
def thunderstorm_conditions():
    """Active thunderstorm."""
    return WeatherConditions(
        temperature_c=28.0,
        feels_like_c=32.0,
        humidity_pct=85.0,
        wind_speed_kmh=45.0,
        wind_gusts_kmh=70.0,
        precipitation_mm=15.0,
        precipitation_probability=95.0,
        uv_index=0.0,
        weather_code=95,
        description="Thunderstorm",
    )


# ---------------------------------------------------------------------------
# TestHeatIndex
# ---------------------------------------------------------------------------

class TestHeatIndex:
    """Verify Steadman / Rothfusz heat index formula."""

    def test_high_heat_high_humidity(self, engine):
        """35C at 80% humidity — Rothfusz regression yields ~56C heat index."""
        hi = engine.compute_heat_index(35.0, 80.0)
        assert 54.0 < hi < 60.0, f"Heat index {hi} out of expected range"

    def test_moderate_heat(self, engine):
        """30C at 60% humidity should produce modest heat index increase."""
        hi = engine.compute_heat_index(30.0, 60.0)
        assert 30.0 < hi < 38.0, f"Heat index {hi} out of expected range"

    def test_below_threshold_returns_temp(self, engine):
        """Below 27C the formula is not applied; should return input temp."""
        hi = engine.compute_heat_index(20.0, 50.0)
        assert hi == 20.0

    def test_low_humidity_below_threshold(self, engine):
        """Below 40% humidity the formula is not applied."""
        hi = engine.compute_heat_index(30.0, 30.0)
        assert hi == 30.0

    def test_extreme_heat(self, engine):
        """40C at 90% humidity -> very high heat index."""
        hi = engine.compute_heat_index(40.0, 90.0)
        assert hi > 55.0, f"Heat index {hi} should be extreme"


# ---------------------------------------------------------------------------
# TestWindChill
# ---------------------------------------------------------------------------

class TestWindChill:
    """Verify Environment Canada / NWS wind chill formula."""

    def test_moderate_cold_moderate_wind(self, engine):
        """-10C at 30 km/h wind should give approx -18C."""
        wc = engine.compute_wind_chill(-10.0, 30.0)
        assert -22.0 < wc < -15.0, f"Wind chill {wc} out of expected range"

    def test_zero_degrees_strong_wind(self, engine):
        """0C at 40 km/h wind should give negative wind chill."""
        wc = engine.compute_wind_chill(0.0, 40.0)
        assert wc < 0.0, f"Wind chill {wc} should be negative"

    def test_above_threshold_returns_temp(self, engine):
        """Above 10C, wind chill formula is not applied."""
        wc = engine.compute_wind_chill(15.0, 30.0)
        assert wc == 15.0

    def test_low_wind_returns_temp(self, engine):
        """Below 4.8 km/h wind, formula is not applied."""
        wc = engine.compute_wind_chill(-5.0, 3.0)
        assert wc == -5.0

    def test_severe_cold_strong_wind(self, engine):
        """-20C at 50 km/h should give severe wind chill well below -30C."""
        wc = engine.compute_wind_chill(-20.0, 50.0)
        assert wc < -30.0, f"Wind chill {wc} should be severe"


# ---------------------------------------------------------------------------
# TestPaceAdjustment
# ---------------------------------------------------------------------------

class TestPaceAdjustment:
    """Verify pace modifier at various temperatures."""

    def test_cool_no_adjustment(self):
        """10C should return 1.0 (no adjustment)."""
        assert WeatherEngine.pace_adjustment_for_heat(10.0) == 1.0

    def test_at_15c_no_adjustment(self):
        """15C is the threshold; no adjustment."""
        assert WeatherEngine.pace_adjustment_for_heat(15.0) == 1.0

    def test_at_20c(self):
        """20C should give ~1.02 (2% slower)."""
        mod = WeatherEngine.pace_adjustment_for_heat(20.0)
        assert abs(mod - 1.02) < 0.005

    def test_at_25c(self):
        """25C should give ~1.04 (4% slower)."""
        mod = WeatherEngine.pace_adjustment_for_heat(25.0)
        assert abs(mod - 1.04) < 0.005

    def test_at_30c(self):
        """30C should give ~1.08 (8% slower)."""
        mod = WeatherEngine.pace_adjustment_for_heat(30.0)
        assert abs(mod - 1.08) < 0.005

    def test_at_35c(self):
        """35C should give ~1.15 (15% slower)."""
        mod = WeatherEngine.pace_adjustment_for_heat(35.0)
        assert abs(mod - 1.15) < 0.005

    def test_above_35c(self):
        """Above 35C should give >1.15."""
        mod = WeatherEngine.pace_adjustment_for_heat(40.0)
        assert mod > 1.15

    def test_monotonically_increasing(self):
        """Pace modifier should increase with temperature."""
        prev = 1.0
        for temp in [10, 15, 18, 20, 22, 25, 28, 30, 32, 35, 38]:
            mod = WeatherEngine.pace_adjustment_for_heat(float(temp))
            assert mod >= prev, f"Non-monotonic at {temp}C"
            prev = mod


# ---------------------------------------------------------------------------
# TestDewPoint
# ---------------------------------------------------------------------------

class TestDewPoint:
    """Verify Magnus formula for dew point calculation."""

    def test_known_value_20c_50pct(self, engine):
        """20C at 50% humidity -> dew point around 9-10C."""
        dp = engine.dew_point(20.0, 50.0)
        assert 8.0 < dp < 11.0, f"Dew point {dp} out of expected range"

    def test_known_value_30c_80pct(self, engine):
        """30C at 80% humidity -> dew point around 26C."""
        dp = engine.dew_point(30.0, 80.0)
        assert 25.0 < dp < 28.0, f"Dew point {dp} out of expected range"

    def test_saturated_air(self, engine):
        """100% humidity -> dew point equals air temperature."""
        dp = engine.dew_point(25.0, 100.0)
        assert abs(dp - 25.0) < 0.5

    def test_very_dry(self, engine):
        """10% humidity -> very low dew point."""
        dp = engine.dew_point(25.0, 10.0)
        assert dp < 0.0, f"Dew point {dp} should be well below zero for dry air"

    def test_zero_humidity_handled(self, engine):
        """0% humidity should not crash (edge case)."""
        dp = engine.dew_point(20.0, 0.0)
        assert isinstance(dp, float)


# ---------------------------------------------------------------------------
# TestTrainingAdjustments
# ---------------------------------------------------------------------------

class TestTrainingAdjustments:
    """Verify clothing, hydration, warnings for various conditions."""

    def test_mild_conditions_low_risk(self, engine, mild_conditions):
        adj = engine.assess_conditions(mild_conditions)
        assert adj.overall_risk == "low"
        assert adj.pace_modifier == 1.0
        assert len(adj.warnings) == 0

    def test_hot_conditions_increased_hydration(self, engine, hot_humid_conditions):
        adj = engine.assess_conditions(hot_humid_conditions)
        assert adj.hydration_ml_per_hour > 500
        assert adj.pace_modifier > 1.0
        assert adj.overall_risk in ("high", "extreme")

    def test_hot_conditions_uv_warning(self, engine, hot_humid_conditions):
        adj = engine.assess_conditions(hot_humid_conditions)
        uv_warnings = [w for w in adj.warnings if "UV" in w.upper() or "uv" in w.lower()]
        assert len(uv_warnings) > 0, "Should warn about high UV"

    def test_cold_conditions_warmup(self, engine, cold_windy_conditions):
        adj = engine.assess_conditions(cold_windy_conditions)
        warmup_adj = [a for a in adj.adjustments if "warm" in a.lower()]
        assert len(warmup_adj) > 0, "Should recommend extended warm-up"

    def test_cold_wind_strong_wind_note(self, engine, cold_windy_conditions):
        adj = engine.assess_conditions(cold_windy_conditions)
        wind_adj = [a for a in adj.adjustments if "wind" in a.lower()]
        assert len(wind_adj) > 0, "Should note strong wind impact"

    def test_rain_gear_recommendation(self, engine, rainy_conditions):
        adj = engine.assess_conditions(rainy_conditions)
        rain_items = [a for a in adj.adjustments if "rain" in a.lower() or "waterproof" in a.lower()]
        assert len(rain_items) > 0, "Should recommend rain gear"

    def test_thunderstorm_extreme_risk(self, engine, thunderstorm_conditions):
        adj = engine.assess_conditions(thunderstorm_conditions)
        assert adj.overall_risk == "extreme"
        thunder_warnings = [w for w in adj.warnings if "thunder" in w.lower() or "lightning" in w.lower()]
        assert len(thunder_warnings) > 0

    def test_clothing_hot(self, engine, hot_humid_conditions):
        adj = engine.assess_conditions(hot_humid_conditions)
        assert "singlet" in adj.clothing_recommendation.lower() or "shorts" in adj.clothing_recommendation.lower()

    def test_clothing_cold(self, engine, cold_windy_conditions):
        adj = engine.assess_conditions(cold_windy_conditions)
        assert "winter" in adj.clothing_recommendation.lower() or "thermal" in adj.clothing_recommendation.lower()

    def test_high_wind_cycling_warning(self, engine):
        """Wind > 50 km/h should flag as unsafe for cycling."""
        conditions = WeatherConditions(
            temperature_c=20.0, feels_like_c=18.0, humidity_pct=50.0,
            wind_speed_kmh=55.0, wind_gusts_kmh=70.0,
        )
        adj = engine.assess_conditions(conditions)
        cycling_warn = [w for w in adj.warnings if "cycling" in w.lower()]
        assert len(cycling_warn) > 0


# ---------------------------------------------------------------------------
# TestWeatherFormatting
# ---------------------------------------------------------------------------

class TestWeatherFormatting:
    """Verify context and summary contain expected info."""

    def test_context_format_contains_temperature(self, engine, mild_conditions):
        adj = engine.assess_conditions(mild_conditions)
        ctx = engine.format_weather_context(mild_conditions, adj)
        assert "15" in ctx
        assert "Temperature" in ctx or "WEATHER" in ctx

    def test_context_format_contains_wind(self, engine, mild_conditions):
        adj = engine.assess_conditions(mild_conditions)
        ctx = engine.format_weather_context(mild_conditions, adj)
        assert "km/h" in ctx
        assert "Wind" in ctx or "wind" in ctx

    def test_context_format_contains_hydration(self, engine, mild_conditions):
        adj = engine.assess_conditions(mild_conditions)
        ctx = engine.format_weather_context(mild_conditions, adj)
        assert "ml" in ctx.lower() or "Hydration" in ctx

    def test_summary_format_contains_training(self, engine, mild_conditions):
        adj = engine.assess_conditions(mild_conditions)
        summary = engine.format_weather_summary(mild_conditions, adj)
        assert "Training" in summary

    def test_summary_contains_clothing(self, engine, mild_conditions):
        adj = engine.assess_conditions(mild_conditions)
        summary = engine.format_weather_summary(mild_conditions, adj)
        # Clothing recommendation should appear (15C = "long sleeves, tights optional")
        assert any(w in summary.lower() for w in ("shirt", "shorts", "sleeves", "tights"))

    def test_context_hot_shows_pace_adjustment(self, engine, hot_humid_conditions):
        adj = engine.assess_conditions(hot_humid_conditions)
        ctx = engine.format_weather_context(hot_humid_conditions, adj)
        assert "slower" in ctx.lower() or "%" in ctx

    def test_summary_thunderstorm_shows_warnings(self, engine, thunderstorm_conditions):
        adj = engine.assess_conditions(thunderstorm_conditions)
        summary = engine.format_weather_summary(thunderstorm_conditions, adj)
        assert "indoor" in summary.lower() or "thunder" in summary.lower()


# ---------------------------------------------------------------------------
# TestWindDirection
# ---------------------------------------------------------------------------

class TestWindDirection:
    """Verify degree to compass conversion."""

    def test_north(self):
        assert WeatherEngine.wind_direction_str(0.0) == "N"
        assert WeatherEngine.wind_direction_str(360.0) == "N"

    def test_east(self):
        assert WeatherEngine.wind_direction_str(90.0) == "E"

    def test_south(self):
        assert WeatherEngine.wind_direction_str(180.0) == "S"

    def test_west(self):
        assert WeatherEngine.wind_direction_str(270.0) == "W"

    def test_northeast(self):
        assert WeatherEngine.wind_direction_str(45.0) == "NE"

    def test_southeast(self):
        assert WeatherEngine.wind_direction_str(135.0) == "SE"

    def test_southwest(self):
        assert WeatherEngine.wind_direction_str(225.0) == "SW"

    def test_northwest(self):
        assert WeatherEngine.wind_direction_str(315.0) == "NW"

    def test_near_boundary(self):
        """22 degrees should round to N or NE (boundary is at 22.5)."""
        result = WeatherEngine.wind_direction_str(22.0)
        assert result in ("N", "NE")

    def test_negative_degrees(self):
        """Negative degrees should wrap around."""
        result = WeatherEngine.wind_direction_str(-90.0)
        assert result == "W"


# ---------------------------------------------------------------------------
# TestWeatherProviderParsing
# ---------------------------------------------------------------------------

class TestWeatherProviderParsing:
    """Mock httpx responses and verify parsing of Open-Meteo JSON."""

    @pytest.fixture
    def provider(self):
        return WeatherProvider(latitude=48.8566, longitude=2.3522)

    @pytest.fixture
    def mock_current_response(self):
        """Simulated Open-Meteo current weather JSON."""
        return {
            "current": {
                "time": "2025-03-07T14:00",
                "temperature_2m": 18.5,
                "relative_humidity_2m": 62.0,
                "apparent_temperature": 17.2,
                "precipitation": 0.0,
                "weather_code": 2,
                "wind_speed_10m": 12.3,
                "wind_direction_10m": 225.0,
                "wind_gusts_10m": 22.1,
            },
            "hourly": {
                "time": [
                    "2025-03-07T13:00", "2025-03-07T14:00",
                    "2025-03-07T15:00",
                ],
                "uv_index": [4.2, 5.1, 4.8],
            },
        }

    @pytest.fixture
    def mock_hourly_response(self):
        """Simulated Open-Meteo hourly forecast JSON."""
        return {
            "hourly": {
                "time": [
                    "2025-03-07T06:00", "2025-03-07T07:00",
                    "2025-03-07T08:00",
                ],
                "temperature_2m": [8.0, 10.5, 13.0],
                "relative_humidity_2m": [75.0, 68.0, 60.0],
                "precipitation_probability": [10.0, 5.0, 0.0],
                "precipitation": [0.0, 0.0, 0.0],
                "wind_speed_10m": [8.0, 10.0, 12.0],
                "uv_index": [0.0, 1.0, 3.0],
                "weather_code": [3, 2, 1],
                "apparent_temperature": [6.5, 9.0, 12.0],
            },
        }

    @pytest.mark.anyio
    async def test_fetch_current_parses_correctly(self, provider, mock_current_response):
        """Verify parsing of current weather response."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=mock_current_response)
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_resp
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            result = await provider.fetch_current()

        assert result["temperature_c"] == 18.5
        assert result["humidity_pct"] == 62.0
        assert result["feels_like_c"] == 17.2
        assert result["wind_speed_kmh"] == 12.3
        assert result["wind_direction_deg"] == 225.0
        assert result["weather_code"] == 2
        assert result["description"] == "Partly cloudy"
        assert result["uv_index"] == 5.1  # matched to 14:00

    @pytest.mark.anyio
    async def test_fetch_hourly_parses_correctly(self, provider, mock_hourly_response):
        """Verify parsing of hourly forecast response."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=mock_hourly_response)
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_resp
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            result = await provider.fetch_hourly_forecast(hours=3)

        assert len(result) == 3
        assert result[0]["temp_c"] == 8.0
        assert result[1]["humidity_pct"] == 68.0
        assert result[2]["description"] == "Mainly clear"

    @pytest.mark.anyio
    async def test_fetch_hourly_limits_hours(self, provider, mock_hourly_response):
        """Should only return up to requested hours even if more data."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=mock_hourly_response)
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_resp
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            result = await provider.fetch_hourly_forecast(hours=2)

        assert len(result) == 2

    @pytest.mark.anyio
    async def test_is_connected_success(self, provider):
        """is_connected returns True on 200 response."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_resp
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            assert await provider.is_connected() is True

    @pytest.mark.anyio
    async def test_is_connected_failure(self, provider):
        """is_connected returns False on network error."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            assert await provider.is_connected() is False

    @pytest.mark.anyio
    async def test_wellness_sleep_activities_empty(self, provider):
        """Weather provider returns empty lists for non-weather data."""
        from datetime import date
        assert await provider.fetch_wellness(date.today(), date.today()) == []
        assert await provider.fetch_activities(date.today(), date.today()) == []
        assert await provider.fetch_sleep(date.today(), date.today()) == []


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Test extreme values, missing data, zero wind."""

    def test_zero_wind(self, engine):
        """Zero wind should not crash any calculation."""
        conditions = WeatherConditions(
            temperature_c=20.0, feels_like_c=20.0, humidity_pct=50.0,
            wind_speed_kmh=0.0,
        )
        adj = engine.assess_conditions(conditions)
        assert adj.overall_risk in ("low", "moderate")

    def test_extreme_heat_60c(self, engine):
        """Extreme 60C should result in extreme risk, not crash."""
        conditions = WeatherConditions(
            temperature_c=60.0, feels_like_c=65.0, humidity_pct=10.0,
            wind_speed_kmh=5.0,
        )
        adj = engine.assess_conditions(conditions)
        assert adj.overall_risk == "extreme"
        assert adj.pace_modifier > 1.15

    def test_extreme_cold_minus40(self, engine):
        """Extreme -40C with wind should handle gracefully."""
        conditions = WeatherConditions(
            temperature_c=-40.0, feels_like_c=-55.0, humidity_pct=20.0,
            wind_speed_kmh=40.0,
        )
        adj = engine.assess_conditions(conditions)
        wc = engine.compute_wind_chill(-40.0, 40.0)
        assert wc < -50.0
        assert adj.overall_risk in ("high", "extreme")

    def test_zero_humidity(self, engine):
        """Zero humidity should not crash."""
        conditions = WeatherConditions(
            temperature_c=25.0, feels_like_c=23.0, humidity_pct=0.0,
            wind_speed_kmh=10.0,
        )
        adj = engine.assess_conditions(conditions)
        assert isinstance(adj.hydration_ml_per_hour, int)

    def test_all_zero_conditions(self, engine):
        """All zeros should not crash."""
        conditions = WeatherConditions(
            temperature_c=0.0, feels_like_c=0.0, humidity_pct=0.0,
            wind_speed_kmh=0.0,
        )
        adj = engine.assess_conditions(conditions)
        assert isinstance(adj, TrainingAdjustment)

    def test_missing_description(self, engine):
        """Empty description should still work."""
        conditions = WeatherConditions(
            temperature_c=20.0, feels_like_c=20.0, humidity_pct=50.0,
            wind_speed_kmh=10.0, description="",
        )
        adj = engine.assess_conditions(conditions)
        ctx = engine.format_weather_context(conditions, adj)
        assert "WEATHER" in ctx

    def test_wmo_codes_coverage(self):
        """All defined WMO codes should have descriptions."""
        for code, desc in WMO_CODES.items():
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_provider_name_and_categories(self):
        """Provider properties should return correct values."""
        p = WeatherProvider()
        assert p.name == "weather"
        assert "weather" in p.supported_categories

    def test_wind_direction_full_circle(self):
        """Test all 8 compass directions are reachable."""
        directions_seen = set()
        for deg in range(0, 360, 5):
            d = WeatherEngine.wind_direction_str(float(deg))
            directions_seen.add(d)
        expected = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
        assert directions_seen == expected
