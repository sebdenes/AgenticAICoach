"""Weather data provider using Open-Meteo free API."""

from __future__ import annotations

import logging
from datetime import date, datetime
from dataclasses import dataclass

import httpx

from data_providers.base import DataProvider, NormalizedRecord

log = logging.getLogger("coach.weather_provider")


# Weather code descriptions (WMO codes from Open-Meteo)
WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


class WeatherProvider:
    """DataProvider implementation for Open-Meteo weather API.

    Open-Meteo is free, no API key needed.
    API docs: https://open-meteo.com/en/docs
    """

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, latitude: float = 48.8566, longitude: float = 2.3522):
        """Initialize with training location. Defaults to Paris."""
        self.latitude = latitude
        self.longitude = longitude

    @property
    def name(self) -> str:
        return "weather"

    @property
    def supported_categories(self) -> list[str]:
        return ["weather"]

    async def fetch_current(self) -> dict:
        """Fetch current weather conditions.

        Open-Meteo endpoint: GET with current params for real-time data
        plus hourly data for the current hour to get humidity, UV, etc.

        Returns dict with:
        - temperature_c, feels_like_c, humidity_pct
        - wind_speed_kmh, wind_direction_deg, wind_gusts_kmh
        - precipitation_mm, weather_code, description
        - uv_index (from hourly data)
        """
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "current": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
            ]),
            "hourly": "uv_index",
            "forecast_days": 1,
            "timezone": "auto",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current", {})
        weather_code = current.get("weather_code", 0)

        # Get UV index for the current hour from hourly data
        uv_index = 0.0
        hourly = data.get("hourly", {})
        hourly_times = hourly.get("time", [])
        hourly_uv = hourly.get("uv_index", [])
        current_time_str = current.get("time", "")
        if current_time_str and hourly_times and hourly_uv:
            # Find the matching hour in hourly data
            current_hour = current_time_str[:13]  # "YYYY-MM-DDTHH"
            for i, t in enumerate(hourly_times):
                if t.startswith(current_hour) and i < len(hourly_uv):
                    uv_index = hourly_uv[i] or 0.0
                    break

        return {
            "temperature_c": current.get("temperature_2m", 0.0),
            "feels_like_c": current.get("apparent_temperature", 0.0),
            "humidity_pct": current.get("relative_humidity_2m", 0.0),
            "wind_speed_kmh": current.get("wind_speed_10m", 0.0),
            "wind_direction_deg": current.get("wind_direction_10m", 0.0),
            "wind_gusts_kmh": current.get("wind_gusts_10m", 0.0),
            "precipitation_mm": current.get("precipitation", 0.0),
            "weather_code": weather_code,
            "description": WMO_CODES.get(weather_code, "Unknown"),
            "uv_index": uv_index,
        }

    async def fetch_hourly_forecast(self, hours: int = 24) -> list[dict]:
        """Fetch hourly forecast for the next N hours.

        Open-Meteo params: hourly variables for temperature, humidity,
        precipitation, wind, UV, and apparent temperature.

        Each entry: {hour: str, temp_c, feels_like_c, humidity_pct,
                     precipitation_prob, precipitation_mm, wind_speed_kmh,
                     uv_index, weather_code, description}
        """
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation_probability",
                "precipitation",
                "wind_speed_10m",
                "uv_index",
                "weather_code",
                "apparent_temperature",
            ]),
            "forecast_hours": hours,
            "timezone": "auto",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        result = []

        for i, t in enumerate(times):
            if i >= hours:
                break
            wc = _safe_get(hourly, "weather_code", i, 0)
            result.append({
                "hour": t,
                "temp_c": _safe_get(hourly, "temperature_2m", i, 0.0),
                "feels_like_c": _safe_get(hourly, "apparent_temperature", i, 0.0),
                "humidity_pct": _safe_get(hourly, "relative_humidity_2m", i, 0.0),
                "precipitation_prob": _safe_get(hourly, "precipitation_probability", i, 0.0),
                "precipitation_mm": _safe_get(hourly, "precipitation", i, 0.0),
                "wind_speed_kmh": _safe_get(hourly, "wind_speed_10m", i, 0.0),
                "uv_index": _safe_get(hourly, "uv_index", i, 0.0),
                "weather_code": wc,
                "description": WMO_CODES.get(wc, "Unknown"),
            })

        return result

    async def fetch_training_window(self, preferred_hours: list[int] | None = None) -> dict:
        """Find the best training window in the next 24h.

        Score each hour: lower temp deviation from 15C, lower precipitation,
        lower wind, lower UV -> better score.

        If preferred_hours given (e.g. [7,8,9,17,18,19]), only consider those.

        Returns: {best_hour: str, score: float, conditions: dict,
                  reasoning: str}
        """
        forecast = await self.fetch_hourly_forecast(hours=24)
        if not forecast:
            return {
                "best_hour": "",
                "score": 0.0,
                "conditions": {},
                "reasoning": "No forecast data available.",
            }

        candidates = forecast
        if preferred_hours is not None:
            filtered = []
            for entry in forecast:
                try:
                    hour_of_day = int(entry["hour"].split("T")[1].split(":")[0])
                    if hour_of_day in preferred_hours:
                        filtered.append(entry)
                except (IndexError, ValueError):
                    continue
            if filtered:
                candidates = filtered

        best = None
        best_score = -999.0

        for entry in candidates:
            score = 100.0

            # Temperature: penalize deviation from 15C (ideal)
            temp_dev = abs(entry["temp_c"] - 15.0)
            score -= temp_dev * 2.0

            # Precipitation: heavy penalty
            score -= entry["precipitation_mm"] * 20.0
            score -= entry.get("precipitation_prob", 0.0) * 0.3

            # Wind: penalize above 15 km/h
            wind = entry["wind_speed_kmh"]
            if wind > 15.0:
                score -= (wind - 15.0) * 1.5

            # UV: penalize above 6
            uv = entry["uv_index"]
            if uv > 6.0:
                score -= (uv - 6.0) * 3.0

            # Severe weather codes penalized heavily
            wc = entry["weather_code"]
            if wc >= 95:  # thunderstorm
                score -= 100.0
            elif wc >= 61:  # rain/snow
                score -= 30.0
            elif wc >= 51:  # drizzle
                score -= 10.0

            if score > best_score:
                best_score = score
                best = entry

        if best is None:
            return {
                "best_hour": "",
                "score": 0.0,
                "conditions": {},
                "reasoning": "No suitable training window found.",
            }

        # Build reasoning
        reasons = []
        reasons.append(f"Temperature {best['temp_c']:.0f}C")
        if best["precipitation_mm"] > 0:
            reasons.append(f"precipitation {best['precipitation_mm']:.1f}mm")
        else:
            reasons.append("dry conditions")
        reasons.append(f"wind {best['wind_speed_kmh']:.0f} km/h")
        if best["uv_index"] > 0:
            reasons.append(f"UV {best['uv_index']:.0f}")
        reasoning = f"Best conditions: {', '.join(reasons)}."

        return {
            "best_hour": best["hour"],
            "score": round(best_score, 1),
            "conditions": best,
            "reasoning": reasoning,
        }

    # -- DataProvider protocol (weather doesn't provide these) --
    async def fetch_wellness(self, start: date, end: date) -> list[NormalizedRecord]:
        return []

    async def fetch_activities(self, start: date, end: date) -> list[NormalizedRecord]:
        return []

    async def fetch_sleep(self, start: date, end: date) -> list[NormalizedRecord]:
        return []

    async def is_connected(self) -> bool:
        """Check API reachability with a minimal request."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.BASE_URL, params={
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "current_weather": "true",
                })
                return resp.status_code == 200
        except Exception:
            return False


def _safe_get(hourly: dict, key: str, index: int, default):
    """Safely get a value from an hourly data list by index."""
    values = hourly.get(key, [])
    if index < len(values) and values[index] is not None:
        return values[index]
    return default
