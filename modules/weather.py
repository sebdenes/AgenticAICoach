"""Weather analysis -- training adjustments based on conditions."""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field

log = logging.getLogger("coach.weather")


@dataclass
class WeatherConditions:
    """Parsed weather data."""
    temperature_c: float
    feels_like_c: float
    humidity_pct: float
    wind_speed_kmh: float
    wind_direction_deg: float = 0.0
    wind_gusts_kmh: float = 0.0
    precipitation_mm: float = 0.0
    precipitation_probability: float = 0.0
    uv_index: float = 0.0
    weather_code: int = 0
    description: str = ""


@dataclass
class TrainingAdjustment:
    """Recommended training adjustments for weather conditions."""
    pace_modifier: float = 1.0       # 1.0 = no change, 1.05 = 5% slower
    hydration_ml_per_hour: int = 500  # Base hydration rate
    clothing_recommendation: str = ""
    warnings: list[str] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    overall_risk: str = "low"        # "low", "moderate", "high", "extreme"


class WeatherEngine:
    """Assess weather conditions and generate training adjustments."""

    def __init__(self, knowledge_base=None):
        self.kb = knowledge_base

    def assess_conditions(self, conditions: WeatherConditions) -> TrainingAdjustment:
        """Assess weather impact on training.

        Rules (based on sports science):
        1. Heat: pace +1% per 5C above 15C. Above 30C: cap at Z2. Above 35C: indoor only.
        2. Cold: below 0C add warm-up time. Below -15C wind chill: indoor recommended.
        3. Humidity >70%: increase hydration 20-30%. Combined heat+humidity: extra caution.
        4. Wind >30 km/h: pace affected, consider indoor. >50 km/h: unsafe for cycling.
        5. Rain >5mm/h: slippery surfaces, visibility. Thunderstorm: stop immediately.
        6. UV >8: sun protection mandatory, prefer early/late sessions.
        7. Precipitation prob >70%: recommend rain gear.

        Clothing by temperature bands:
        - >25C: minimal (singlet, shorts)
        - 15-25C: shorts, t-shirt
        - 5-15C: long sleeves, tights optional
        - 0-5C: thermal layers, gloves, hat
        - <0C: full winter gear, consider indoor
        """
        adj = TrainingAdjustment()
        feels_like = conditions.feels_like_c
        risk_level = 0  # 0=low, 1=moderate, 2=high, 3=extreme

        # --- 1. Heat assessment ---
        adj.pace_modifier = self.pace_adjustment_for_heat(feels_like)
        if feels_like > 35.0:
            adj.warnings.append("Extreme heat: indoor training strongly recommended")
            adj.adjustments.append("Move session indoors or postpone")
            risk_level = max(risk_level, 3)
        elif feels_like > 30.0:
            adj.warnings.append("High heat: cap intensity at Zone 2")
            adj.adjustments.append(f"Slow pace by {(adj.pace_modifier - 1) * 100:.0f}%")
            risk_level = max(risk_level, 2)
        elif feels_like > 25.0:
            adj.adjustments.append(f"Slow pace by {(adj.pace_modifier - 1) * 100:.0f}%")
            risk_level = max(risk_level, 1)

        # --- 2. Cold assessment ---
        wind_chill = self.compute_wind_chill(conditions.temperature_c, conditions.wind_speed_kmh)
        if wind_chill < -15.0:
            adj.warnings.append("Severe wind chill: indoor training recommended")
            adj.adjustments.append("Risk of frostbite on exposed skin")
            risk_level = max(risk_level, 2)
        elif conditions.temperature_c < 0.0:
            adj.adjustments.append("Extended warm-up recommended (15+ minutes)")
            adj.adjustments.append("Protect extremities from cold")
            risk_level = max(risk_level, 1)

        # --- 3. Humidity assessment ---
        if conditions.humidity_pct > 70.0:
            humidity_increase = 0.20 if conditions.humidity_pct <= 85 else 0.30
            extra_ml = int(adj.hydration_ml_per_hour * humidity_increase)
            adj.hydration_ml_per_hour += extra_ml
            adj.adjustments.append(
                f"Increase hydration +{humidity_increase * 100:.0f}% due to high humidity"
            )
            # Combined heat + humidity
            if feels_like > 25.0 and conditions.humidity_pct > 70.0:
                heat_idx = self.compute_heat_index(conditions.temperature_c, conditions.humidity_pct)
                if heat_idx > 40.0:
                    adj.warnings.append(
                        f"Dangerous heat index ({heat_idx:.0f}C): consider cancelling"
                    )
                    risk_level = max(risk_level, 3)
                elif heat_idx > 32.0:
                    adj.warnings.append(f"High heat index ({heat_idx:.0f}C): extreme caution")
                    risk_level = max(risk_level, 2)

        # Heat-based hydration increase
        if feels_like > 25.0:
            heat_extra = int(100 * ((feels_like - 25.0) / 5.0))
            adj.hydration_ml_per_hour += heat_extra

        # --- 4. Wind assessment ---
        if conditions.wind_speed_kmh > 50.0:
            adj.warnings.append("Dangerous winds: unsafe for cycling")
            risk_level = max(risk_level, 2)
        elif conditions.wind_speed_kmh > 30.0:
            adj.adjustments.append("Strong wind: expect pace impact, consider indoor")
            risk_level = max(risk_level, 1)

        if conditions.wind_gusts_kmh > 60.0:
            adj.warnings.append(f"Wind gusts up to {conditions.wind_gusts_kmh:.0f} km/h")
            risk_level = max(risk_level, 2)

        # --- 5. Rain / thunderstorm assessment ---
        if conditions.weather_code >= 95:
            adj.warnings.append("Thunderstorm: stop outdoor activity immediately")
            adj.adjustments.append("Lightning risk -- seek indoor shelter")
            risk_level = max(risk_level, 3)
        elif conditions.precipitation_mm > 5.0:
            adj.warnings.append("Heavy precipitation: slippery surfaces, reduced visibility")
            adj.adjustments.append("Reduce pace on turns and descents")
            risk_level = max(risk_level, 1)
        elif conditions.precipitation_mm > 0.0:
            adj.adjustments.append("Light precipitation: consider waterproof layer")

        if conditions.precipitation_probability > 70.0:
            adj.adjustments.append("High rain probability: bring rain gear")

        # --- 6. UV assessment ---
        if conditions.uv_index >= 8.0:
            adj.warnings.append(f"Very high UV index ({conditions.uv_index:.0f}): sun protection mandatory")
            adj.adjustments.append("Prefer early morning or late evening sessions")
            risk_level = max(risk_level, 1)
        elif conditions.uv_index >= 6.0:
            adj.adjustments.append(f"High UV ({conditions.uv_index:.0f}): apply sunscreen, wear hat")

        # --- Clothing recommendation ---
        adj.clothing_recommendation = self._clothing_for_temp(feels_like, conditions)

        # --- Set overall risk ---
        risk_map = {0: "low", 1: "moderate", 2: "high", 3: "extreme"}
        adj.overall_risk = risk_map.get(min(risk_level, 3), "extreme")

        return adj

    @staticmethod
    def _clothing_for_temp(feels_like_c: float, conditions: WeatherConditions) -> str:
        """Determine clothing recommendation based on feels-like temperature."""
        extras = []

        if feels_like_c > 25.0:
            base = "Singlet and shorts"
        elif feels_like_c > 15.0:
            base = "T-shirt and shorts"
        elif feels_like_c > 5.0:
            base = "Long sleeves, tights optional"
        elif feels_like_c > 0.0:
            base = "Thermal layers, gloves, hat"
        else:
            base = "Full winter gear, consider indoor training"

        if conditions.uv_index >= 6.0:
            extras.append("sunscreen")
        if conditions.uv_index >= 8.0:
            extras.append("UV-protective hat")
        if conditions.precipitation_mm > 0 or conditions.precipitation_probability > 50:
            extras.append("waterproof layer")
        if conditions.wind_speed_kmh > 20.0 and feels_like_c < 15.0:
            extras.append("windbreaker")

        if extras:
            return f"{base}, plus {', '.join(extras)}"
        return base

    @staticmethod
    def compute_heat_index(temp_c: float, humidity_pct: float) -> float:
        """Steadman heat index (Rothfusz regression equation).

        Valid for temp > 27C and humidity > 40%.
        Returns feels-like temperature in Celsius.
        """
        if temp_c < 27.0 or humidity_pct < 40.0:
            return temp_c

        # Convert to Fahrenheit for the standard Rothfusz formula
        t_f = temp_c * 9.0 / 5.0 + 32.0
        rh = humidity_pct

        # Rothfusz regression
        hi_f = (
            -42.379
            + 2.04901523 * t_f
            + 10.14333127 * rh
            - 0.22475541 * t_f * rh
            - 0.00683783 * t_f * t_f
            - 0.05481717 * rh * rh
            + 0.00122874 * t_f * t_f * rh
            + 0.00085282 * t_f * rh * rh
            - 0.00000199 * t_f * t_f * rh * rh
        )

        # Adjustments for low/high humidity
        if rh < 13.0 and 80.0 < t_f < 112.0:
            adjustment = -((13.0 - rh) / 4.0) * math.sqrt((17.0 - abs(t_f - 95.0)) / 17.0)
            hi_f += adjustment
        elif rh > 85.0 and 80.0 < t_f < 87.0:
            adjustment = ((rh - 85.0) / 10.0) * ((87.0 - t_f) / 5.0)
            hi_f += adjustment

        # Convert back to Celsius
        return (hi_f - 32.0) * 5.0 / 9.0

    @staticmethod
    def compute_wind_chill(temp_c: float, wind_kmh: float) -> float:
        """Wind chill index (Environment Canada / NWS formula).

        Valid for temp < 10C and wind > 4.8 km/h.
        WC = 13.12 + 0.6215*T - 11.37*V^0.16 + 0.3965*T*V^0.16
        Returns feels-like temperature in Celsius.
        """
        if temp_c >= 10.0 or wind_kmh <= 4.8:
            return temp_c

        v_exp = wind_kmh ** 0.16
        wc = 13.12 + 0.6215 * temp_c - 11.37 * v_exp + 0.3965 * temp_c * v_exp
        return wc

    @staticmethod
    def pace_adjustment_for_heat(feels_like_c: float) -> float:
        """Calculate pace slowdown percentage for temperature.

        Based on research (Ely et al., 2007, Med Sci Sports Exerc):
        - Below 15C: no adjustment (1.0)
        - 15-20C: +1-2% (1.01-1.02)
        - 20-25C: +2-4% (1.02-1.04)
        - 25-30C: +4-8% (1.04-1.08)
        - 30-35C: +8-15% (1.08-1.15)
        - >35C: +15%+ (not recommended outdoors)

        Returns modifier (e.g., 1.05 means 5% slower).
        Uses linear interpolation within each band.
        """
        if feels_like_c <= 15.0:
            return 1.0

        if feels_like_c <= 20.0:
            frac = (feels_like_c - 15.0) / 5.0
            return 1.0 + frac * 0.02  # 1.0 -> 1.02

        if feels_like_c <= 25.0:
            frac = (feels_like_c - 20.0) / 5.0
            return 1.02 + frac * 0.02  # 1.02 -> 1.04

        if feels_like_c <= 30.0:
            frac = (feels_like_c - 25.0) / 5.0
            return 1.04 + frac * 0.04  # 1.04 -> 1.08

        if feels_like_c <= 35.0:
            frac = (feels_like_c - 30.0) / 5.0
            return 1.08 + frac * 0.07  # 1.08 -> 1.15

        # Above 35C
        return 1.15 + (feels_like_c - 35.0) * 0.02

    def dew_point(self, temp_c: float, humidity_pct: float) -> float:
        """Magnus formula for dew point.

        Td = (b * alpha) / (a - alpha)
        where alpha = (a * T) / (b + T) + ln(RH / 100)
        a = 17.27, b = 237.7
        """
        a = 17.27
        b = 237.7
        if humidity_pct <= 0.0:
            humidity_pct = 0.01
        alpha = (a * temp_c) / (b + temp_c) + math.log(humidity_pct / 100.0)
        td = (b * alpha) / (a - alpha)
        return td

    def format_weather_context(self, conditions: WeatherConditions,
                                adjustment: TrainingAdjustment) -> str:
        """Format for LLM system prompt injection.

        WEATHER CONDITIONS:
          Temperature: 22C (feels like 24C) | Humidity: 65%
          Wind: 15 km/h NW | UV: 6 (High)
          Conditions: Partly cloudy
        TRAINING ADJUSTMENTS:
          Pace: +2% slower than normal
          Hydration: 600ml/hour
          Clothing: shorts, t-shirt, sunscreen
          Notes: Good conditions for outdoor training
        """
        wind_dir = self.wind_direction_str(conditions.wind_direction_deg)
        uv_label = _uv_label(conditions.uv_index)

        pace_pct = (adjustment.pace_modifier - 1.0) * 100

        lines = [
            "WEATHER CONDITIONS:",
            f"  Temperature: {conditions.temperature_c:.0f}C"
            f" (feels like {conditions.feels_like_c:.0f}C)"
            f" | Humidity: {conditions.humidity_pct:.0f}%",
            f"  Wind: {conditions.wind_speed_kmh:.0f} km/h {wind_dir}"
            f" | UV: {conditions.uv_index:.0f} ({uv_label})",
            f"  Conditions: {conditions.description}",
            "TRAINING ADJUSTMENTS:",
        ]

        if pace_pct > 0:
            lines.append(f"  Pace: +{pace_pct:.0f}% slower than normal")
        else:
            lines.append("  Pace: no adjustment needed")

        lines.append(f"  Hydration: {adjustment.hydration_ml_per_hour}ml/hour")
        lines.append(f"  Clothing: {adjustment.clothing_recommendation}")

        if adjustment.warnings:
            for w in adjustment.warnings:
                lines.append(f"  WARNING: {w}")
        if adjustment.adjustments:
            for a in adjustment.adjustments:
                lines.append(f"  Note: {a}")

        if not adjustment.warnings and not adjustment.adjustments:
            lines.append("  Notes: Good conditions for outdoor training")

        return "\n".join(lines)

    def format_weather_summary(self, conditions: WeatherConditions,
                                adjustment: TrainingAdjustment) -> str:
        """Format for Telegram display with emoji indicators."""
        wind_dir = self.wind_direction_str(conditions.wind_direction_deg)
        uv_label = _uv_label(conditions.uv_index)
        pace_pct = (adjustment.pace_modifier - 1.0) * 100

        # Weather icon based on code
        icon = _weather_icon(conditions.weather_code)

        lines = [
            f"{icon} *Weather Report*",
            "",
            f"\U0001f321 {conditions.temperature_c:.0f}C"
            f" (feels {conditions.feels_like_c:.0f}C)"
            f" | \U0001f4a8 {conditions.wind_speed_kmh:.0f} km/h {wind_dir}",
            f"\U0001f4a7 {conditions.humidity_pct:.0f}% humidity"
            f" | \u2600\ufe0f UV {conditions.uv_index:.0f} ({uv_label})",
            "",
            "\U0001f3c3 *Training Adjustments*",
        ]

        if pace_pct > 0:
            lines.append(f"\u2022 Pace: +{pace_pct:.0f}% slower")
        else:
            lines.append("\u2022 Pace: no change needed")

        lines.append(f"\u2022 \U0001f4a7 Hydration: {adjustment.hydration_ml_per_hour}ml/hr")
        lines.append(f"\u2022 \U0001f455 {adjustment.clothing_recommendation}")

        if adjustment.warnings:
            lines.append("")
            for w in adjustment.warnings:
                lines.append(f"\u26a0\ufe0f {w}")

        if adjustment.adjustments:
            for a in adjustment.adjustments:
                lines.append(f"\u2022 {a}")

        # Overall status
        lines.append("")
        risk_icons = {
            "low": "\u2705 Good conditions for outdoor training",
            "moderate": "\U0001f7e1 Acceptable with adjustments",
            "high": "\U0001f7e0 Consider indoor alternatives",
            "extreme": "\U0001f534 Indoor training recommended",
        }
        lines.append(risk_icons.get(adjustment.overall_risk, ""))

        return "\n".join(lines)

    @staticmethod
    def wind_direction_str(degrees: float) -> str:
        """Convert wind direction degrees to compass string (N, NE, E, etc.)."""
        if degrees < 0:
            degrees = degrees % 360
        # 8 compass directions, each covering 45 degrees
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        index = round(degrees / 45.0) % 8
        return directions[index]


def _uv_label(uv: float) -> str:
    """Map UV index to descriptive label."""
    if uv <= 2:
        return "Low"
    if uv <= 5:
        return "Moderate"
    if uv <= 7:
        return "High"
    if uv <= 10:
        return "Very High"
    return "Extreme"


def _weather_icon(code: int) -> str:
    """Map WMO weather code to an emoji icon."""
    if code == 0:
        return "\u2600\ufe0f"      # Clear sky
    if code <= 3:
        return "\U0001f324\ufe0f"  # Partly cloudy
    if code <= 48:
        return "\U0001f32b\ufe0f"  # Fog
    if code <= 55:
        return "\U0001f326\ufe0f"  # Drizzle
    if code <= 67:
        return "\U0001f327\ufe0f"  # Rain
    if code <= 77:
        return "\u2744\ufe0f"      # Snow
    if code <= 82:
        return "\U0001f327\ufe0f"  # Rain showers
    if code <= 86:
        return "\U0001f328\ufe0f"  # Snow showers
    return "\u26c8\ufe0f"          # Thunderstorm
