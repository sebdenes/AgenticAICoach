"""Tool definitions and async executor for the coaching AI agentic loop."""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from intervals import IntervalsClient
    from database import Database
    from config import AthleteConfig

log = logging.getLogger("coach.tools")

# ── Tool Schemas (passed to Anthropic API as tools= parameter) ──────────────

TOOL_SCHEMAS = [
    {
        "name": "get_wellness",
        "description": "Fetch raw wellness records from Intervals.icu and Whoop: HRV, RHR, sleep hours/score, CTL, ATL, TSB, steps, weight. Returns list of daily records sorted oldest-first. Use this when you need raw biometric data for trend analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of past days to fetch (1-90). Default 7.", "default": 7}
            },
            "required": [],
        },
    },
    {
        "name": "get_activities",
        "description": "Fetch completed training activities from Intervals.icu: type, name, date, duration, distance, TSS, average HR, average power, IF. Use this to see what training has been done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of past days to fetch (1-90). Default 7.", "default": 7},
                "activity_type": {"type": "string", "description": "Optional filter by sport type: 'Run', 'Ride', 'Workout', 'Swim', etc."},
            },
            "required": [],
        },
    },
    {
        "name": "get_planned_events",
        "description": "Fetch upcoming planned training sessions from the Intervals.icu calendar: name, date, type, description, target TSS. Use this to see what is scheduled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "How many days ahead to look (1-14). Default 3.", "default": 3}
            },
            "required": [],
        },
    },
    {
        "name": "analyze_sleep",
        "description": "Compute a structured sleep analysis: average hours, sleep debt, quality grade (A-F), HRV correlation, RHR correlation, and trend. More useful than raw wellness when you want a summary assessment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Analysis window in days (7-30). Default 7.", "default": 7}
            },
            "required": [],
        },
    },
    {
        "name": "analyze_recovery",
        "description": "Compute today's recovery readiness score (0-100) based on personalized HRV, RHR, sleep, CTL, ATL, TSB baselines. Also returns contributing factors and the baselines used.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "analyze_training_load",
        "description": "Compute training load metrics over a window: CTL (chronic fitness), ATL (acute fatigue), TSB (form), ramp rate, acute:chronic ratio, weekly TSS, and load status. Use for fitness/fatigue assessment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Analysis window (14-90). Default 14.", "default": 14}
            },
            "required": [],
        },
    },
    {
        "name": "get_weather",
        "description": "Fetch current weather conditions at the athlete's location and compute training adjustments: temperature, feels-like, humidity, wind, precipitation, pace adjustment percentage, hydration needs, clothing recommendation, and safety warnings.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_race_countdown",
        "description": "Get race countdown: days to race, race name and date, goal time, current CTL/ATL/TSB, and a brief readiness note.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "query_knowledge_base",
        "description": "Search the sports science knowledge base for evidence-based rules, principles, and guidelines on a topic. Returns relevant rules with citations. Use to ground recommendations in science.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to search, e.g. 'HRV recovery', 'marathon taper', 'heat training', 'overreaching signs'"},
                "n_results": {"type": "integer", "description": "Number of rules to return (1-8). Default 4.", "default": 4},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "run_scenario",
        "description": "Simulate the training load impact of a hypothetical workout. Returns before/after CTL/ATL/TSB, days to recovery, race-day projection change, and a recommendation. Use when the athlete asks 'what if I do X?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Natural language workout description, e.g. '3h easy ride', '10km tempo run', '90min Z2 ride', 'intervals 8x400m'"}
            },
            "required": ["description"],
        },
    },
    {
        "name": "get_training_plan",
        "description": "Get the current training plan: this week's sessions with dates, names, types, durations, target TSS, intensity zones, and which are key sessions. Also returns phase, week number, and target weekly TSS.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_coaching_state",
        "description": "Retrieve named state values stored by the coaching system (e.g. training notes, adaptation flags, last check-in summary, custom athlete settings).",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "List of state keys to retrieve. If omitted, returns all state."}
            },
            "required": [],
        },
    },
]


# ── CoachTools executor ──────────────────────────────────────────────────────

class CoachTools:
    """Async tool executor. Dispatches Anthropic tool_use calls to existing coach modules."""

    def __init__(
        self,
        iv,
        db: "Database",
        athlete: "AthleteConfig",
        whoop=None,
        weather_provider=None,
        weather_engine=None,
        rag=None,
        simulator=None,
        strava=None,
    ):
        self.iv = iv
        self.db = db
        self.athlete = athlete
        self.whoop = whoop
        self.weather_provider = weather_provider
        self.weather_engine = weather_engine
        self.rag = rag
        self.simulator = simulator
        self.strava = strava

    async def execute(self, name: str, inputs: dict) -> str:
        """Dispatch a tool call by name. Returns JSON string. Never raises."""
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = await handler(**inputs)
            return json.dumps(result, default=str)
        except Exception as exc:
            log.error("Tool %s failed with inputs %s: %s", name, inputs, exc)
            return json.dumps({"error": str(exc), "tool": name})

    # ── Raw data tools ───────────────────────────────────────────────────────

    async def _tool_get_wellness(self, days: int = 7) -> dict:
        days = max(1, min(days, 90))
        w = await self.iv.wellness(days=days)
        # Augment with Whoop if available — fills HRV, RHR and recovery score gaps
        if self.whoop and self.whoop.is_authenticated:
            try:
                whoop_records = await self.whoop.recovery(days=days)
                # Whoop records use "created_at" (ISO string) as the date indicator
                whoop_by_date: dict = {}
                for r in (whoop_records or []):
                    d = (r.get("created_at") or "")[:10]
                    if d:
                        whoop_by_date[d] = r
                for record in w:
                    # Intervals wellness records use "id" or "date" for the date
                    d = record.get("id") or record.get("date") or ""
                    if not d or d not in whoop_by_date:
                        continue
                    wr_score = whoop_by_date[d].get("score", {}) or {}
                    # Fill HRV gap (Whoop hrv_rmssd_milli → same unit as Intervals hrv)
                    if not record.get("hrv") and wr_score.get("hrv_rmssd_milli"):
                        record["hrv"] = wr_score["hrv_rmssd_milli"]
                    # Fill RHR gap
                    if not record.get("restingHR") and wr_score.get("resting_heart_rate"):
                        record["restingHR"] = wr_score["resting_heart_rate"]
                    # Fill recovery score gap (Whoop 0-100%)
                    if not record.get("recoveryScore") and wr_score.get("recovery_score"):
                        record["recoveryScore"] = wr_score["recovery_score"]
            except Exception as exc:
                log.warning("Whoop augmentation failed: %s", exc)
        return {"days": days, "count": len(w), "records": w}

    async def _tool_get_activities(self, days: int = 7, activity_type: str = None) -> dict:
        # Allow up to 10 years — Strava DB history covers the full range.
        # Intervals.icu is capped at 90 days; used for TSS enrichment only.
        days = max(1, min(days, 3650))

        # Intervals.icu: capped at 90 days — source of TSS/IF/NP analytics metrics
        intervals_acts = await self.iv.activities(days=min(days, 90))

        # Strava: primary source (real-time, full history from local DB via activities())
        strava_acts = []
        if self.strava and self.strava.is_authenticated:
            try:
                strava_acts = await self.strava.activities(days=days)
            except Exception as exc:
                log.warning("Strava fetch skipped: %s", exc)

        # Build Intervals lookup by (date, sport_lower) for TSS enrichment
        intervals_lookup: dict = {}
        for a in intervals_acts:
            key = ((a.get("start_date_local") or "")[:10], (a.get("type") or "").lower())
            intervals_lookup[key] = a

        merged: list = []
        seen_keys: set = set()

        # Strava activities as base, enriched with Intervals TSS/IF/NP where matched
        for sa in strava_acts:
            key = ((sa.get("start_date_local") or "")[:10], (sa.get("type") or "").lower())
            seen_keys.add(key)
            iv = intervals_lookup.get(key, {})
            record = dict(sa)
            if iv:
                record["icu_training_load"]      = iv.get("icu_training_load")
                record["icu_intensity"]          = iv.get("icu_intensity")
                record["icu_weighted_avg_watts"] = iv.get("icu_weighted_avg_watts")
                record["_source"] = "strava+intervals"
            else:
                record["_source"] = "strava"
            merged.append(record)

        # Intervals-only activities (no Strava match) — virtual rides, older history, etc.
        for ia in intervals_acts:
            key = ((ia.get("start_date_local") or "")[:10], (ia.get("type") or "").lower())
            if key not in seen_keys:
                ia["_source"] = "intervals"
                merged.append(ia)

        if activity_type:
            merged = [
                a for a in merged
                if (a.get("type") or "").lower() == activity_type.lower()
            ]
        return {"days": days, "count": len(merged), "activities": merged}

    async def _tool_get_planned_events(self, days_ahead: int = 3) -> dict:
        days_ahead = max(1, min(days_ahead, 14))
        e = await self.iv.events(days_ahead=days_ahead)
        return {"days_ahead": days_ahead, "count": len(e), "events": e}

    # ── Analysis tools ───────────────────────────────────────────────────────

    async def _tool_analyze_sleep(self, days: int = 7) -> dict:
        days = max(7, min(days, 30))
        from modules.sleep import analyze_sleep
        w = await self.iv.wellness(days=days)
        return analyze_sleep(w, self.athlete.sleep_target_hours)

    async def _tool_analyze_recovery(self) -> dict:
        from modules.recovery import calculate_recovery_score
        from modules.thresholds import PersonalizedThresholds
        w = await self.iv.wellness(days=30)
        a = await self.iv.activities(days=30)
        latest = w[-1] if w else {}
        if len(w) >= 7:
            thr = PersonalizedThresholds(w, a)
            baselines = {
                "hrv": thr.hrv_baseline,
                "rhr": thr.rhr_baseline,
                "sleep": thr.sleep_baseline,
            }
        else:
            baselines = {
                "hrv": float(self.athlete.hrv_baseline or 55),
                "rhr": float(self.athlete.rhr_baseline or 45),
                "sleep": float(self.athlete.sleep_target_hours or 7.5),
            }
        # calculate_recovery_score returns a dict with score, grade, signals, etc.
        recovery = calculate_recovery_score(latest, baselines)
        return {
            "recovery_score": recovery.get("score"),
            "grade": recovery.get("grade"),
            "recommendation": recovery.get("recommendation"),
            "signals": recovery.get("signals", []),
            "baselines": baselines,
            "latest": {
                "date": latest.get("date") or latest.get("id"),
                "hrv": latest.get("hrv"),
                "rhr": latest.get("restingHR"),
                "sleep_hours": latest.get("sleepSecs", 0) / 3600 if latest.get("sleepSecs") else None,
                "ctl": latest.get("ctl"),
                "atl": latest.get("atl"),
                "tsb": (latest.get("ctl") or 0) - (latest.get("atl") or 0),
            },
        }

    async def _tool_analyze_training_load(self, days: int = 14) -> dict:
        days = max(14, min(days, 90))
        from modules.performance import analyze_training
        w = await self.iv.wellness(days=days)
        a = await self.iv.activities(days=days)
        return analyze_training(w, a, self.athlete.race_date)

    # ── Contextual tools ─────────────────────────────────────────────────────

    async def _tool_get_weather(self) -> dict:
        if not self.weather_provider or not self.weather_engine:
            return {"available": False, "reason": "Weather not configured (no location set)"}
        try:
            from modules.weather import WeatherConditions
            raw = await self.weather_provider.fetch_current()
            conditions = WeatherConditions(
                temperature_c=raw.get("temperature_c", 15.0),
                feels_like_c=raw.get("feels_like_c", 15.0),
                humidity_pct=raw.get("humidity_pct", 60),
                wind_speed_kmh=raw.get("wind_speed_kmh", 0.0),
                wind_direction_deg=raw.get("wind_direction_deg", 0),
                wind_gusts_kmh=raw.get("wind_gusts_kmh", 0.0),
                precipitation_mm=raw.get("precipitation_mm", 0.0),
                precipitation_probability=raw.get("precipitation_probability", 0),
                uv_index=raw.get("uv_index", 0),
                weather_code=raw.get("weather_code", 0),
                description=raw.get("description", ""),
            )
            adj = self.weather_engine.assess_conditions(conditions)
            return {
                "available": True,
                "temperature_c": raw.get("temperature_c"),
                "feels_like_c": raw.get("feels_like_c"),
                "humidity_pct": raw.get("humidity_pct"),
                "wind_speed_kmh": raw.get("wind_speed_kmh"),
                "precipitation_mm": raw.get("precipitation_mm"),
                "description": raw.get("description", ""),
                "pace_adjustment_pct": round((adj.pace_modifier - 1.0) * 100, 1),
                "hydration_ml_per_hr": adj.hydration_ml_per_hour,
                # TrainingAdjustment uses .clothing_recommendation (not .clothing)
                "clothing": adj.clothing_recommendation,
                "safety_warnings": adj.warnings,
            }
        except Exception as exc:
            log.warning("Weather fetch failed: %s", exc)
            return {"available": False, "reason": str(exc)}

    async def _tool_get_race_countdown(self) -> dict:
        w = await self.iv.wellness(days=3)
        latest = w[-1] if w else {}
        today = date.today()
        days_to_race = None
        if self.athlete.race_date:
            try:
                race = date.fromisoformat(self.athlete.race_date)
                days_to_race = (race - today).days
            except ValueError:
                pass
        return {
            "race_name": self.athlete.race_name,
            "race_date": self.athlete.race_date,
            "days_to_race": days_to_race,
            "goal_time": getattr(self.athlete, "goal_time", None),
            "current_ctl": round(float(latest.get("ctl") or 0), 1),
            "current_atl": round(float(latest.get("atl") or 0), 1),
            "current_tsb": round(float((latest.get("ctl") or 0) - (latest.get("atl") or 0)), 1),
            "today": str(today),
        }

    async def _tool_query_knowledge_base(self, topic: str, n_results: int = 4) -> dict:
        if not self.rag:
            return {"available": False, "reason": "Knowledge base not initialized", "rules": ""}
        n_results = max(1, min(n_results, 8))
        try:
            rules_text = self.rag.retrieve_context(topic, max_rules=n_results)
            return {"available": True, "topic": topic, "rules": rules_text}
        except Exception as exc:
            return {"available": False, "reason": str(exc), "rules": ""}

    async def _tool_run_scenario(self, description: str) -> dict:
        if not self.simulator:
            return {"available": False, "reason": "Simulator not initialized"}
        try:
            from modules.simulation import ScenarioSimulator
            workout = ScenarioSimulator.parse_workout_description(description)
            w = await self.iv.wellness(days=14)
            # simulate() signature: simulate(workout, wellness_history, activities=None)
            result = self.simulator.simulate(workout, w)
            # format_result() returns a str for Telegram display
            formatted_str = self.simulator.format_result(result)
            return {
                "available": True,
                "workout": {
                    "sport": workout.sport,
                    "duration_minutes": workout.duration_minutes,
                    "estimated_tss": workout.estimated_tss,
                    "intensity": workout.intensity,
                },
                "impact": {
                    "summary": formatted_str,
                    "current_ctl": result.current_ctl,
                    "current_atl": result.current_atl,
                    "current_tsb": result.current_tsb,
                    "projected_ctl": result.projected_ctl,
                    "projected_atl": result.projected_atl,
                    "projected_tsb": result.projected_tsb,
                    "ctl_delta": result.ctl_delta,
                    "atl_delta": result.atl_delta,
                    "tsb_delta": result.tsb_delta,
                    "days_to_recovery": result.days_to_recovery,
                    "days_to_baseline_tsb": result.days_to_baseline_tsb,
                    "race_ctl_projected": result.race_ctl_projected,
                    "race_tsb_projected": result.race_tsb_projected,
                    "race_readiness_change": result.race_readiness_change,
                    "days_to_race": result.days_to_race,
                    "recommendation": result.recommendation,
                    "alternative": result.alternative,
                },
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    async def _tool_get_training_plan(self) -> dict:
        plan_data = self.db.get_state("training_plan")
        if not plan_data:
            return {"plan_exists": False, "sessions": []}
        try:
            from modules.periodization import (
                PeriodizationEngine, TrainingPlan, Mesocycle, Microcycle, TrainingSession
            )
            # Reconstruct plan from stored dict
            plan = _reconstruct_plan(plan_data)
            # Find current week
            today = str(date.today())
            current_week = None
            for meso in plan.mesocycles:
                for mc in meso.microcycles:
                    dates = [s.date for s in mc.sessions]
                    if dates and min(dates) <= today <= max(dates):
                        current_week = mc
                        break
                if current_week:
                    break
            if not current_week and plan.mesocycles and plan.mesocycles[0].microcycles:
                current_week = plan.mesocycles[0].microcycles[0]
            if not current_week:
                return {"plan_exists": True, "sessions": [], "message": "No current week found"}
            sessions = [
                {
                    "date": s.date,
                    "day": _day_abbr(s.date),
                    "session_type": s.session_type,
                    "sport": s.sport,
                    "name": s.name,
                    "duration_minutes": s.duration_minutes,
                    "target_tss": s.target_tss,
                    "intensity_zone": s.intensity_zone,
                    "is_key_session": s.is_key_session,
                    "is_today": s.date == today,
                }
                for s in current_week.sessions
            ]
            return {
                "plan_exists": True,
                "phase": current_week.phase,
                "week_number": current_week.week_number,
                "target_weekly_tss": current_week.target_weekly_tss,
                "theme": current_week.theme,
                "sessions": sessions,
            }
        except Exception as exc:
            log.warning("get_training_plan failed: %s", exc)
            return {"plan_exists": True, "error": str(exc), "sessions": []}

    async def _tool_get_coaching_state(self, keys: list = None) -> dict:
        all_state = self.db.get_all_state()
        if keys:
            return {k: all_state[k] for k in keys if k in all_state}
        # Exclude large objects (training plan) from unfiltered dump
        excluded = {"training_plan"}
        return {k: v for k, v in all_state.items() if k not in excluded}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _reconstruct_plan(plan_data: dict):
    """Reconstruct a TrainingPlan dataclass from a stored dict."""
    from modules.periodization import TrainingPlan, Mesocycle, Microcycle, TrainingSession
    mesos = []
    for meso_d in plan_data.get("mesocycles", []):
        micros = []
        for mc_d in meso_d.get("microcycles", []):
            sessions = [TrainingSession(**s) for s in mc_d.get("sessions", [])]
            mc_d_copy = {k: v for k, v in mc_d.items() if k != "sessions"}
            micros.append(Microcycle(**mc_d_copy, sessions=sessions))
        meso_d_copy = {k: v for k, v in meso_d.items() if k != "microcycles"}
        mesos.append(Mesocycle(**meso_d_copy, microcycles=micros))
    plan_copy = {k: v for k, v in plan_data.items() if k != "mesocycles"}
    return TrainingPlan(**plan_copy, mesocycles=mesos)


def _day_abbr(date_str: str) -> str:
    """Return Mon/Tue/Wed etc. for a date string."""
    try:
        return date.fromisoformat(date_str).strftime("%a")
    except Exception:
        return "?"
