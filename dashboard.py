"""FastAPI dashboard backend for the elite endurance coaching bot.

Provides REST API endpoints for the coaching dashboard frontend,
connecting to the same SQLite database and reusing existing analysis modules.

Run with:
    uvicorn dashboard:app --host 0.0.0.0 --port 3000 --reload
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# ── Project imports ────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))

from config import BASE_DIR, load_app_config, load_athlete_config
from database import Database
from intervals import IntervalsClient
from whoop import WhoopClient

from modules.sleep import analyze_sleep
from modules.performance import analyze_training
from modules.recovery import calculate_recovery_score
from modules.race_predictor import predict_marathon
from modules.compliance import analyze_compliance

try:
    from modules.intelligence import analyze_patterns
except ImportError:
    analyze_patterns = None

try:
    from modules.alerts import generate_alerts
except ImportError:
    generate_alerts = None

try:
    from modules.thresholds import PersonalizedThresholds
except ImportError:
    PersonalizedThresholds = None

try:
    from modules.knowledge_base import KnowledgeBase
    _kb = KnowledgeBase()
except ImportError:
    KnowledgeBase = None
    _kb = None

try:
    from modules.periodization import PeriodizationEngine, TrainingPlan, Mesocycle, Microcycle, TrainingSession
except ImportError:
    PeriodizationEngine = None
    TrainingPlan = None
    Mesocycle = None
    Microcycle = None
    TrainingSession = None

try:
    from modules.weather import WeatherEngine, WeatherConditions
except ImportError:
    WeatherEngine = None
    WeatherConditions = None

try:
    from data_providers.weather_provider import WeatherProvider
except ImportError:
    WeatherProvider = None

try:
    from modules.athlete_models import PerformanceForecaster
except ImportError:
    PerformanceForecaster = None

# ── Logging ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("coach.dashboard")

# ── Globals (initialised at startup) ──────────────────────────

db: Database | None = None
intervals: IntervalsClient | None = None
whoop: WhoopClient | None = None
app_cfg = None
athlete_cfg = None

# Phase 2 module globals
_periodization = None
_weather_provider = None
_weather_engine = None
_performance_forecaster = None
_strava_client = None

STATIC_DIR = BASE_DIR / "static"


# ── Lifespan ───────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise shared resources on startup, clean up on shutdown."""
    global db, intervals, whoop, app_cfg, athlete_cfg
    global _periodization, _weather_provider, _weather_engine, _performance_forecaster, _strava_client

    app_cfg = load_app_config()
    athlete_cfg = load_athlete_config()

    db = Database(app_cfg.db_path)
    log.info("Database connected: %s", app_cfg.db_path)

    if app_cfg.intervals_api_key and app_cfg.intervals_athlete_id:
        intervals = IntervalsClient(
            api_key=app_cfg.intervals_api_key,
            athlete_id=app_cfg.intervals_athlete_id,
            db=db,
        )
        log.info("Intervals.icu client initialised")
    else:
        log.warning("Intervals.icu credentials missing -- client disabled")

    if app_cfg.whoop_client_id and app_cfg.whoop_client_secret:
        whoop = WhoopClient(
            client_id=app_cfg.whoop_client_id,
            client_secret=app_cfg.whoop_client_secret,
            redirect_uri=app_cfg.whoop_redirect_uri,
            db=db,
        )
        log.info("Whoop client initialised (authenticated=%s)", whoop.is_authenticated)
    else:
        log.warning("Whoop credentials missing -- client disabled")

    # Phase 2 modules
    try:
        _kb_instance = KnowledgeBase() if KnowledgeBase else None
        if PeriodizationEngine and athlete_cfg:
            _periodization = PeriodizationEngine(athlete_cfg, knowledge_base=_kb_instance)
        if WeatherProvider and getattr(athlete_cfg, 'latitude', 0):
            _weather_provider = WeatherProvider(
                latitude=athlete_cfg.latitude,
                longitude=athlete_cfg.longitude,
            )
        if WeatherEngine:
            _weather_engine = WeatherEngine(knowledge_base=_kb_instance)
        if PerformanceForecaster:
            _performance_forecaster = PerformanceForecaster()
        log.info("Phase 2 modules initialized")
    except Exception as exc:
        log.warning("Phase 2 module init failed: %s", exc)
        _periodization = None
        _weather_provider = None
        _weather_engine = None
        _performance_forecaster = None

    # Strava client (optional — OAuth via /strava/auth endpoint)
    try:
        from strava import StravaClient as _StravaClient
        if app_cfg.strava_client_id and app_cfg.strava_client_secret:
            _strava_client = _StravaClient(
                app_cfg.strava_client_id,
                app_cfg.strava_client_secret,
                app_cfg.strava_redirect_uri,
                db,
            )
            log.info(
                "Strava client initialized (authenticated=%s)",
                _strava_client.is_authenticated,
            )
    except Exception as exc:
        log.warning("Strava client init failed: %s", exc)

    yield

    log.info("Dashboard shutting down")


# ── App ────────────────────────────────────────────────────────

app = FastAPI(
    title="Coach Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_to_race() -> int | None:
    if not athlete_cfg or not athlete_cfg.race_date:
        return None
    try:
        race_dt = datetime.strptime(athlete_cfg.race_date, "%Y-%m-%d")
        return (race_dt - datetime.now()).days
    except ValueError:
        return None


async def _fetch_intervals_wellness(days: int) -> list:
    """Fetch wellness from Intervals.icu, falling back to DB cache."""
    if intervals:
        try:
            return await intervals.wellness(days)
        except Exception as exc:
            log.warning("Intervals wellness fetch failed: %s", exc)
    return db.get_wellness_history(days) if db else []


async def _fetch_intervals_activities(days: int) -> list:
    """Fetch activities from Intervals.icu, falling back to DB cache."""
    if intervals:
        try:
            return await intervals.activities(days)
        except Exception as exc:
            log.warning("Intervals activities fetch failed: %s", exc)
    return db.get_activity_history(days) if db else []


async def _fetch_intervals_events(days_ahead: int = 3) -> list:
    """Fetch planned events from Intervals.icu."""
    if intervals:
        try:
            return await intervals.events(days_ahead)
        except Exception as exc:
            log.warning("Intervals events fetch failed: %s", exc)
    return []


async def _fetch_whoop_recovery(days: int = 7) -> list:
    """Fetch Whoop recovery data, returning empty list if unavailable."""
    if whoop and whoop.is_authenticated:
        try:
            return await whoop.recovery(days)
        except Exception as exc:
            log.warning("Whoop recovery fetch failed: %s", exc)
    return []


async def _fetch_whoop_sleep(days: int = 7) -> list:
    """Fetch Whoop sleep data, returning empty list if unavailable."""
    if whoop and whoop.is_authenticated:
        try:
            return await whoop.sleep(days)
        except Exception as exc:
            log.warning("Whoop sleep fetch failed: %s", exc)
    return []


async def _fetch_whoop_cycles(days: int = 7) -> list:
    """Fetch Whoop daily strain cycles."""
    if whoop and whoop.is_authenticated:
        try:
            return await whoop.cycles(days)
        except Exception as exc:
            log.warning("Whoop cycles fetch failed: %s", exc)
    return []


async def _fetch_whoop_workouts(days: int = 7) -> list:
    """Fetch actual Whoop workouts (not daily cycles) for activity merge."""
    if whoop and whoop.is_authenticated:
        try:
            return await whoop.workouts(days)
        except Exception as exc:
            log.warning("Whoop workouts fetch failed: %s", exc)
    return []


async def _fetch_wellness_augmented(days: int) -> list:
    """Fetch wellness from Intervals.icu, then fill HRV/RHR/recovery gaps from Whoop.

    Mirrors the bot's ``_tool_get_wellness`` augmentation exactly so both surfaces
    see the same enriched data.  Whoop recovery records use ``created_at`` (ISO
    timestamp) as the date indicator; Intervals wellness records use ``id`` or ``date``.
    """
    wellness = await _fetch_intervals_wellness(days)
    if not whoop or not whoop.is_authenticated:
        return wellness
    try:
        whoop_records = await whoop.recovery(days=days)
        # Build date → score lookup (Whoop uses created_at as the record date)
        whoop_by_date: dict = {}
        for r in (whoop_records or []):
            d = (r.get("created_at") or "")[:10]
            if d:
                whoop_by_date[d] = r.get("score", {}) or {}
        for record in wellness:
            d = record.get("id") or record.get("date") or ""
            if not d or d not in whoop_by_date:
                continue
            ws = whoop_by_date[d]
            if not record.get("hrv") and ws.get("hrv_rmssd_milli"):
                record["hrv"] = ws["hrv_rmssd_milli"]
            if not record.get("restingHR") and ws.get("resting_heart_rate"):
                record["restingHR"] = ws["resting_heart_rate"]
            if not record.get("recoveryScore") and ws.get("recovery_score"):
                record["recoveryScore"] = ws["recovery_score"]
    except Exception as exc:
        log.warning("Whoop wellness augmentation failed: %s", exc)
    return wellness


def _merge_activities(strava_acts: list, intervals_acts: list, whoop_workouts: list = None) -> list:
    """Strava-primary merge with Intervals TSS enrichment and optional Whoop supplement.

    Priority:
      1. Strava (primary — richest activity metadata, real-time)
      2. Intervals.icu (enriches Strava records with TSS/IF/NP; adds Intervals-only activities)
      3. Whoop workouts (fills sessions tracked only on Whoop, not in Strava or Intervals)

    TSS strategy:
      - Intervals ``icu_training_load`` is the gold standard but only available for
        activities uploaded directly to Intervals (not Strava-synced ones, which appear
        as stubs in the API).
      - For Strava activities without Intervals TSS, we fall back to Strava's
        ``suffer_score`` (Relative Effort) as a rough TSS proxy.
    """
    # Sport name normalisation — maps variant names to a canonical form for dedup
    _CANONICAL = {
        "run": "run", "running": "run", "trail run": "run", "virtualrun": "run",
        "ride": "ride", "cycling": "ride", "virtualride": "ride",
        "mountain biking": "ride", "mountain bike ride": "ride", "spinning": "ride",
        "weighttraining": "strength", "weightlifting": "strength",
        "strength trainer": "strength", "strength (msk)": "strength",
        "crossfit": "strength", "functional fitness": "strength",
        "swim": "swim", "swimming": "swim",
        "walk": "walk", "walking": "walk",
        "hike": "hike", "hiking/rucking": "hike",
        "yoga": "yoga", "rowing": "rowing",
        "activity": "activity",
    }

    def _canon(name: str) -> str:
        return _CANONICAL.get(name.lower(), name.lower())

    # Step 1: Build Intervals lookup — only include records with a type (skip stubs)
    intervals_lookup: dict = {}
    for a in intervals_acts:
        itype = a.get("type") or ""
        if not itype:
            continue  # Skip Strava-synced stubs that have no type
        key = ((a.get("start_date_local") or a.get("date") or "")[:10], _canon(itype))
        intervals_lookup[key] = a

    # Build Whoop lookup by (date, canonical_sport) for strain enrichment
    whoop_lookup: dict = {}
    if whoop_workouts:
        try:
            from whoop import SPORT_MAP as _WS
        except ImportError:
            _WS = {}
        for w in whoop_workouts:
            ws = w.get("score", {}) or {}
            wstart = (w.get("start") or "")[:10]
            wsport = _WS.get(w.get("sport_id", -1), "Activity")
            if wstart and ws.get("strain"):
                wkey = (wstart, _canon(wsport))
                whoop_lookup[wkey] = ws

    merged: list = []
    seen_keys: set = set()
    seen_dates: set = set()  # track dates with real activities (for Whoop dedup)

    # Step 2: Strava activities as base, enriched with Intervals TSS/IF/NP + Whoop strain
    for sa in strava_acts:
        sa_date = (sa.get("start_date_local") or "")[:10]
        sa_type = _canon(sa.get("type") or "")
        key = (sa_date, sa_type)
        seen_keys.add(key)
        seen_dates.add(sa_date)
        iv = intervals_lookup.get(key, {})
        record = dict(sa)
        if iv and iv.get("icu_training_load"):
            record["icu_training_load"]      = iv.get("icu_training_load")
            record["icu_intensity"]          = iv.get("icu_intensity")
            record["icu_weighted_avg_watts"] = iv.get("icu_weighted_avg_watts")
            record["_source"] = "strava+intervals"
        else:
            record["_source"] = "strava"
        # Enrich with Whoop strain if matching workout exists
        wh = whoop_lookup.get(key)
        if wh and wh.get("strain"):
            record["whoop_strain"] = wh["strain"]
        merged.append(record)

    # Step 3: Intervals-only activities (no Strava match) — virtual rides, older history
    for ia in intervals_acts:
        itype = ia.get("type") or ""
        if not itype:
            continue
        ia_date = (ia.get("start_date_local") or ia.get("date") or "")[:10]
        key = (ia_date, _canon(itype))
        if key not in seen_keys:
            record = dict(ia)
            record["_source"] = "intervals"
            merged.append(record)
            seen_keys.add(key)
            seen_dates.add(ia_date)

    # Step 4: Whoop workouts — only add if no Strava/Intervals activity covers that date+sport
    if whoop_workouts:
        try:
            from whoop import SPORT_MAP
        except ImportError:
            SPORT_MAP = {}
        for w in whoop_workouts:
            ws = w.get("score", {}) or {}
            start = (w.get("start") or "")[:10]
            if not start:
                continue
            sport_id = w.get("sport_id", -1)
            sport_name = SPORT_MAP.get(sport_id, "Activity")

            # Skip generic "Activity" (sport_id -1) when real activities exist for this date
            if sport_id == -1 and start in seen_dates:
                continue

            key = (start, _canon(sport_name))
            if key not in seen_keys:
                merged.append({
                    "type": sport_name,
                    "name": sport_name,
                    "start_date_local": w.get("start", ""),
                    "moving_time": None,
                    "distance": None,
                    "average_heartrate": ws.get("average_heart_rate"),
                    "max_heartrate": ws.get("max_heart_rate"),
                    "average_watts": None,
                    "icu_training_load": None,
                    "whoop_strain": ws.get("strain"),
                    "_source": "whoop",
                })
                seen_keys.add(key)
                seen_dates.add(start)

    return merged


def _parse_wellness_for_modules(raw: list) -> list:
    """Normalise wellness rows from either Intervals.icu API or DB format.

    The analysis modules accept both ``sleepSecs``/``restingHR`` (API)
    and ``sleep_seconds``/``rhr`` (DB) key names, so we just pass through.
    """
    return raw


def _latest_wellness(wellness: list) -> dict:
    """Return the most recent wellness entry, or an empty dict."""
    return wellness[-1] if wellness else {}


def _baselines() -> dict:
    """Build baselines dict from athlete config."""
    if not athlete_cfg:
        return {"hrv": 57, "rhr": 42}
    return {
        "hrv": athlete_cfg.hrv_baseline,
        "rhr": athlete_cfg.rhr_baseline,
    }


def _format_whoop_recovery_summary(records: list) -> dict | None:
    """Extract the latest Whoop recovery score and strain."""
    if not records:
        return None
    latest = records[0]
    score = latest.get("score", {})
    if not score:
        return None
    return {
        "recovery_score": score.get("recovery_score"),
        "hrv_rmssd_milli": score.get("hrv_rmssd_milli"),
        "resting_heart_rate": score.get("resting_heart_rate"),
        "spo2_percentage": score.get("spo2_percentage"),
        "skin_temp_celsius": score.get("skin_temp_celsius"),
    }


def _format_whoop_strain_summary(cycles: list) -> dict | None:
    """Extract the latest Whoop daily strain."""
    if not cycles:
        return None
    latest = cycles[0]
    score = latest.get("score", {})
    if not score:
        return None
    return {
        "strain": score.get("strain"),
        "kilojoule": score.get("kilojoule"),
        "average_heart_rate": score.get("average_heart_rate"),
        "max_heart_rate": score.get("max_heart_rate"),
    }


# ── Strava OAuth ───────────────────────────────────────────────

@app.get("/strava/auth")
async def strava_auth():
    """Redirect user to Strava OAuth authorization page."""
    if not _strava_client:
        raise HTTPException(status_code=404, detail="Strava integration not configured")
    return RedirectResponse(_strava_client.get_auth_url())


@app.get("/strava/callback")
async def strava_callback(code: str = None, error: str = None):
    """Handle Strava OAuth callback — exchange code for tokens."""
    if error or not code:
        return HTMLResponse(
            f"<h1>Strava Authorization Failed</h1><p>Error: {error or 'no code received'}</p>",
            status_code=400,
        )
    if not _strava_client:
        return HTMLResponse("<h1>Strava not configured</h1>", status_code=500)
    ok = await _strava_client.exchange_code(code)
    if ok:
        return HTMLResponse(
            "<h1>✅ Strava Connected!</h1>"
            "<p>Your activities will now appear in coaching immediately after each workout — "
            "no need to wait for Intervals.icu to sync.</p>"
            "<p>You can close this window and return to Telegram.</p>"
            "<script>setTimeout(()=>window.close(), 4000)</script>",
        )
    return HTMLResponse("<h1>Token exchange failed</h1><p>Please try /strava again.</p>", status_code=500)


# ── Dashboard Snapshot ─────────────────────────────────────────

@app.get("/api/dashboard")
async def dashboard_snapshot():
    """Today's combined coaching dashboard snapshot."""
    today = _today()

    # Fetch data (each helper has its own caching / error handling)
    wellness = await _fetch_wellness_augmented(14)          # Whoop-enriched
    intervals_activities = await _fetch_intervals_activities(7)
    events = await _fetch_intervals_events(3)
    whoop_recovery = await _fetch_whoop_recovery(7)
    whoop_cycles = await _fetch_whoop_cycles(7)

    # Merge all activity sources — same logic as /api/activities
    strava_acts_today = db.get_strava_activities(days=7) if db else []
    whoop_workouts = await _fetch_whoop_workouts(7)
    all_activities = _merge_activities(strava_acts_today, intervals_activities, whoop_workouts)

    latest = _latest_wellness(wellness)

    # Recovery score — use PersonalizedThresholds (same as bot) when enough history
    if PersonalizedThresholds and len(wellness) >= 7:
        try:
            activities_30 = await _fetch_intervals_activities(30)
            pt = PersonalizedThresholds(wellness, activities_30)
            baselines = {
                "hrv": pt.hrv_baseline,
                "rhr": pt.rhr_baseline,
                "sleep": getattr(pt, "sleep_baseline", athlete_cfg.sleep_target_hours if athlete_cfg else 7.5),
            }
        except Exception as exc:
            log.warning("PersonalizedThresholds failed in dashboard snapshot: %s", exc)
            baselines = _baselines()
    else:
        baselines = _baselines()

    recovery = calculate_recovery_score(latest, baselines) if latest else {
        "score": 0, "grade": "unknown", "recommendation": "No data", "signals": []
    }

    # Today's activities (Intervals + Strava merged)
    today_activities = []
    for a in all_activities:
        act_date = (a.get("start_date_local", a.get("date", "")) or "")[:10]
        if act_date == today and a.get("type"):
            tss = a.get("icu_training_load") or a.get("suffer_score") or 0
            today_activities.append({
                "type": a.get("type"),
                "name": a.get("name", ""),
                "duration_min": (a.get("moving_time", 0) or 0) // 60,
                "distance_km": round((a.get("distance", 0) or 0) / 1000, 1),
                "tss": tss,
                "avg_hr": a.get("average_heartrate"),
                "whoop_strain": a.get("whoop_strain"),
                "_source": a.get("_source", "unknown"),
            })

    # Today's planned events
    today_events = []
    for e in events:
        ev_date = (e.get("start_date_local", e.get("date", "")) or "")[:10]
        if ev_date == today:
            today_events.append({
                "name": e.get("name", ""),
                "category": e.get("category", ""),
                "description": (e.get("description", "") or "")[:300],
            })

    # Alerts
    alerts = []
    if generate_alerts is not None:
        try:
            alerts = generate_alerts(
                wellness=wellness,
                activities=intervals_activities,
                athlete_config=asdict(athlete_cfg) if athlete_cfg else {},
            )
        except Exception as exc:
            log.warning("Alert generation failed: %s", exc)

    # Latest wellness values
    ctl = latest.get("ctl", 0)
    atl = latest.get("atl", 0)
    tsb = ctl - atl
    rhr = latest.get("restingHR") or latest.get("rhr")
    hrv = latest.get("hrv")
    sleep_secs = latest.get("sleepSecs", 0) or latest.get("sleep_seconds", 0) or 0
    sleep_hours = round(sleep_secs / 3600, 1) if sleep_secs else None
    sleep_score = latest.get("sleepScore") or latest.get("sleep_score")

    # Whoop data
    whoop_data = None
    whoop_rec = _format_whoop_recovery_summary(whoop_recovery)
    whoop_str = _format_whoop_strain_summary(whoop_cycles)
    if whoop_rec or whoop_str:
        whoop_data = {
            "recovery": whoop_rec,
            "strain": whoop_str,
        }

    # Weekly training load — aggregate TSS from merged activities over the last 7 days
    from datetime import timedelta as _td
    week_ago = (datetime.now() - _td(days=7)).strftime("%Y-%m-%d")
    weekly_tss = 0
    weekly_duration_min = 0
    weekly_count = 0
    for a in all_activities:
        act_date = (a.get("start_date_local", a.get("date", "")) or "")[:10]
        if act_date >= week_ago and a.get("type"):
            weekly_tss += a.get("icu_training_load") or a.get("suffer_score") or 0
            weekly_duration_min += (a.get("moving_time", 0) or 0) // 60
            weekly_count += 1

    return {
        "date": today,
        "wellness": {
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(tsb, 1),
            "rhr": rhr,
            "hrv": round(hrv, 1) if hrv else None,
            "sleep_hours": sleep_hours,
            "sleep_score": sleep_score,
        },
        "recovery": recovery,
        "today_activities": today_activities,
        "today_events": today_events,
        "weekly_training_load": {
            "total_tss": round(weekly_tss, 1),
            "total_duration_min": weekly_duration_min,
            "activity_count": weekly_count,
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(tsb, 1),
        },
        "alerts": alerts if isinstance(alerts, list) else [],
        "days_to_race": _days_to_race(),
        "race_name": athlete_cfg.race_name if athlete_cfg else None,
        "whoop": whoop_data,
    }


# ── Time Series ────────────────────────────────────────────────

@app.get("/api/wellness")
async def wellness_history(days: int = Query(default=14, ge=1, le=365)):
    """Wellness time-series: CTL, ATL, TSB, RHR, HRV, sleep for each day.

    HRV and RHR are augmented with Whoop data where Intervals.icu is missing values,
    matching the bot's ``_tool_get_wellness`` behaviour exactly.
    """
    raw = await _fetch_wellness_augmented(days)
    result = []
    for d in raw:
        ctl = d.get("ctl", 0)
        atl = d.get("atl", 0)
        sleep_secs = d.get("sleepSecs", 0) or d.get("sleep_seconds", 0) or 0
        result.append({
            "date": d.get("id", d.get("date", "")),
            "ctl": round(ctl, 1) if ctl else 0,
            "atl": round(atl, 1) if atl else 0,
            "tsb": round(ctl - atl, 1),
            "rhr": d.get("restingHR") or d.get("rhr"),
            "hrv": round(d["hrv"], 1) if d.get("hrv") else None,
            "sleep_hours": round(sleep_secs / 3600, 1) if sleep_secs else None,
            "sleep_score": d.get("sleepScore") or d.get("sleep_score"),
            "steps": d.get("steps"),
        })
    return result


@app.get("/api/activities")
async def activity_history(days: int = Query(default=14, ge=1, le=365)):
    """Activity time-series: Strava primary, Intervals enriches with TSS/IF/NP, Whoop fills gaps.

    Strava is the primary source (richest real-time metadata). Each Strava activity is
    enriched with Intervals.icu TSS (``icu_training_load``), Intensity Factor, and
    Normalized Power where a matching record exists.  Intervals-only activities (e.g.
    virtual rides) are appended.  Whoop workouts fill any remaining gaps.
    """
    strava_acts    = db.get_strava_activities(days=days)
    intervals_acts = await _fetch_intervals_activities(min(days, 90))
    whoop_workouts = await _fetch_whoop_workouts(days)

    merged = _merge_activities(strava_acts, intervals_acts, whoop_workouts)

    def _norm(a: dict) -> dict:
        # TSS: prefer Intervals icu_training_load, fall back to Strava suffer_score
        tss = a.get("icu_training_load") or a.get("suffer_score") or 0
        return {
            "date": (a.get("start_date_local", a.get("date", "")) or "")[:10],
            "type": a.get("type"),
            "name": a.get("name", ""),
            "tss": tss,
            "duration_min": (a.get("moving_time", 0) or 0) // 60,
            "distance_km": round((a.get("distance", 0) or 0) / 1000, 1),
            "avg_hr": a.get("average_heartrate"),
            "max_hr": a.get("max_heartrate"),
            "avg_power": a.get("average_watts"),
            "np": a.get("icu_weighted_avg_watts"),
            "intensity": a.get("icu_intensity"),
            "whoop_strain": a.get("whoop_strain"),
            "elevation_m": a.get("total_elevation_gain"),
            "_source": a.get("_source", "unknown"),
        }

    result = [_norm(a) for a in merged if a.get("type")]
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.get("/api/sleep")
async def sleep_history(days: int = Query(default=14, ge=1, le=365)):
    """Combined sleep data from Intervals.icu and Whoop."""
    # Intervals.icu sleep from wellness
    wellness = await _fetch_intervals_wellness(days)
    intervals_sleep = {}
    for d in wellness:
        date = d.get("id", d.get("date", ""))
        sleep_secs = d.get("sleepSecs", 0) or d.get("sleep_seconds", 0) or 0
        if sleep_secs > 0:
            intervals_sleep[date] = {
                "date": date,
                "source": "intervals",
                "hours": round(sleep_secs / 3600, 1),
                "score": d.get("sleepScore") or d.get("sleep_score"),
                "stages": None,
                "efficiency": None,
                "debt": None,
            }

    # Whoop sleep
    whoop_sleep_records = await _fetch_whoop_sleep(days)
    whoop_sleep = {}
    for s in whoop_sleep_records:
        score = s.get("score", {})
        if not score:
            continue
        start = (s.get("start", "") or "")[:10]
        stage = score.get("stage_summary", {})
        total_ms = stage.get("total_in_bed_time_milli", 0) or 0
        awake_ms = stage.get("total_awake_time_milli", 0) or 0
        sleep_ms = total_ms - awake_ms
        rem_ms = stage.get("total_rem_sleep_time_milli", 0) or 0
        deep_ms = stage.get("total_slow_wave_sleep_time_milli", 0) or 0
        light_ms = stage.get("total_light_sleep_time_milli", 0) or 0

        need = score.get("sleep_needed", {})
        debt_ms = need.get("need_from_sleep_debt_milli", 0) or 0

        whoop_sleep[start] = {
            "date": start,
            "source": "whoop",
            "hours": round(sleep_ms / 3600000, 1) if sleep_ms else 0,
            "score": score.get("sleep_performance_percentage"),
            "stages": {
                "rem_hours": round(rem_ms / 3600000, 1),
                "deep_hours": round(deep_ms / 3600000, 1),
                "light_hours": round(light_ms / 3600000, 1),
                "awake_min": round(awake_ms / 60000),
            },
            "efficiency": score.get("sleep_efficiency_percentage"),
            "debt": round(debt_ms / 3600000, 1),
            "respiratory_rate": score.get("respiratory_rate"),
        }

    # Merge: prefer Whoop for detail, Intervals for coverage
    all_dates = sorted(set(list(intervals_sleep.keys()) + list(whoop_sleep.keys())))
    result = []
    for date in all_dates:
        ws = whoop_sleep.get(date)
        isleep = intervals_sleep.get(date)
        if ws and isleep:
            # Combine both sources
            entry = {
                "date": date,
                "intervals_hours": isleep["hours"],
                "intervals_score": isleep["score"],
                "whoop_hours": ws["hours"],
                "whoop_score": ws["score"],
                "stages": ws["stages"],
                "efficiency": ws["efficiency"],
                "debt": ws["debt"],
                "respiratory_rate": ws.get("respiratory_rate"),
            }
        elif ws:
            entry = {
                "date": date,
                "intervals_hours": None,
                "intervals_score": None,
                "whoop_hours": ws["hours"],
                "whoop_score": ws["score"],
                "stages": ws["stages"],
                "efficiency": ws["efficiency"],
                "debt": ws["debt"],
                "respiratory_rate": ws.get("respiratory_rate"),
            }
        else:
            entry = {
                "date": date,
                "intervals_hours": isleep["hours"] if isleep else None,
                "intervals_score": isleep["score"] if isleep else None,
                "whoop_hours": None,
                "whoop_score": None,
                "stages": None,
                "efficiency": None,
                "debt": None,
                "respiratory_rate": None,
            }
        result.append(entry)

    return result


# ── Analysis ───────────────────────────────────────────────────

@app.get("/api/recovery")
async def recovery_trend():
    """Recovery composite score over recent days.

    Uses PersonalizedThresholds (dynamic, computed from 30-day history) when
    sufficient data is available — matching the bot's ``_tool_analyze_recovery``
    behaviour exactly.  Falls back to static athlete-config baselines otherwise.
    """
    wellness = await _fetch_wellness_augmented(30)      # Whoop-enriched
    activities = await _fetch_intervals_activities(30)

    # Dynamic baselines — consistent with bot's _tool_analyze_recovery
    if PersonalizedThresholds and len(wellness) >= 7:
        try:
            pt = PersonalizedThresholds(wellness, activities)
            baselines = {
                "hrv": pt.hrv_baseline,
                "rhr": pt.rhr_baseline,
                "sleep": getattr(pt, "sleep_baseline", athlete_cfg.sleep_target_hours if athlete_cfg else 7.5),
            }
        except Exception as exc:
            log.warning("PersonalizedThresholds failed in /api/recovery: %s", exc)
            baselines = _baselines()
    else:
        baselines = _baselines()

    trend = []
    for entry in wellness:
        rec = calculate_recovery_score(entry, baselines)
        trend.append({
            "date": entry.get("id", entry.get("date", "")),
            "score": rec["score"],
            "grade": rec["grade"],
            "sleep_hours": rec["sleep_hours"],
            "hrv": rec["hrv"],
            "rhr": rec["rhr"],
            "tsb": rec["tsb"],
            "signals": rec["signals"],
        })

    return {
        "baselines": baselines,
        "trend": trend,
    }


@app.get("/api/predictions")
async def race_predictions():
    """Marathon race predictions with multiple models."""
    wellness = await _fetch_wellness_augmented(30)
    activities = await _fetch_intervals_activities(30)

    latest = _latest_wellness(wellness)
    ctl = latest.get("ctl", 0)

    # Filter to runs for the predictor
    recent_runs = [a for a in activities if "run" in (a.get("type", "") or "").lower()]

    weight_kg = athlete_cfg.weight_kg if athlete_cfg else 80.0
    target_time = athlete_cfg.goal_time if athlete_cfg and athlete_cfg.goal_time else "3:00:00"

    prediction = predict_marathon(
        ctl=ctl,
        recent_runs=recent_runs,
        weight_kg=weight_kg,
        target_time=target_time,
        wellness_data=wellness,
    )

    return {
        "race_name": athlete_cfg.race_name if athlete_cfg else None,
        "race_date": athlete_cfg.race_date if athlete_cfg else None,
        "days_to_race": _days_to_race(),
        "prediction": prediction,
    }


@app.get("/api/alerts")
async def active_alerts():
    """Active alerts from the intelligence module."""
    if generate_alerts is None:
        return {"alerts": [], "message": "Alerts module not available"}

    wellness = await _fetch_wellness_augmented(14)
    activities = await _fetch_intervals_activities(14)

    try:
        alerts = generate_alerts(
            wellness=wellness,
            activities=activities,
            athlete_config=asdict(athlete_cfg) if athlete_cfg else {},
        )
        return {"alerts": alerts if isinstance(alerts, list) else []}
    except Exception as exc:
        log.error("Alert generation error: %s", exc)
        return {"alerts": [], "error": str(exc)}


@app.get("/api/compliance")
async def training_compliance(days: int = Query(default=14, ge=1, le=90)):
    """Training plan compliance: planned vs completed."""
    activities = await _fetch_intervals_activities(days)
    events = await _fetch_intervals_events(days_ahead=days)

    # Also fetch past events for the compliance window
    # Events endpoint fetches future by default; for compliance we need past
    # Use the activities and events we have -- compliance module handles date filtering
    analysis = analyze_compliance(activities, events, days=days)

    return analysis


@app.get("/api/intelligence")
async def pattern_analysis():
    """Pattern analysis from the intelligence module."""
    if analyze_patterns is None:
        return {"patterns": None, "message": "Intelligence module not available"}

    wellness = await _fetch_wellness_augmented(30)
    activities = await _fetch_intervals_activities(30)
    config = asdict(athlete_cfg) if athlete_cfg else {}

    try:
        patterns = analyze_patterns(wellness, activities, config)
        return {"patterns": patterns}
    except Exception as exc:
        log.error("Pattern analysis error: %s", exc)
        return {"patterns": None, "error": str(exc)}


# ── Thresholds & Knowledge Base ──────────────────────────────

@app.get("/api/thresholds")
async def personalized_thresholds():
    """Personalized baselines computed from athlete wellness history."""
    if PersonalizedThresholds is None:
        return {"thresholds": None, "message": "Thresholds module not available"}

    wellness = await _fetch_wellness_augmented(30)
    activities = await _fetch_intervals_activities(30)

    try:
        pt = PersonalizedThresholds(wellness, activities)
        baselines = pt.get_all_baselines()
        # Assess latest values
        assessments = {}
        if wellness:
            latest = wellness[-1]
            if latest.get("hrv"):
                a = pt.assess_hrv(float(latest["hrv"]))
                assessments["hrv"] = {"status": a.status, "value": a.value, "baseline": a.baseline,
                                      "z_score": round(a.z_score, 2), "percentile": round(a.percentile, 1),
                                      "trend": a.trend}
            if latest.get("restingHR"):
                a = pt.assess_rhr(float(latest["restingHR"]))
                assessments["rhr"] = {"status": a.status, "value": a.value, "baseline": a.baseline,
                                      "z_score": round(a.z_score, 2), "trend": a.trend}
            sleep_secs = latest.get("sleepSecs", 0) or 0
            if sleep_secs > 0:
                a = pt.assess_sleep_duration(sleep_secs / 3600)
                assessments["sleep"] = {"status": a.status, "value_h": round(a.value, 1),
                                        "baseline_h": round(a.baseline, 1), "trend": a.trend}
        return {"baselines": baselines, "assessments": assessments}
    except Exception as exc:
        log.error("Thresholds error: %s", exc)
        return {"thresholds": None, "error": str(exc)}


@app.get("/api/knowledge")
async def knowledge_base_info(
    category: str = Query(default=None),
    tags: str = Query(default=None),
    sport: str = Query(default=None),
):
    """Query the sports science knowledge base."""
    if _kb is None:
        return {"rules": [], "message": "Knowledge base not available"}

    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    rules = _kb.query(category=category, tags=tag_list, sport=sport)
    return {
        "stats": _kb.stats,
        "rules": [
            {"id": r.id, "category": r.category, "principle": r.principle,
             "application": r.application, "citation": r.citation,
             "confidence": r.confidence, "tags": r.tags, "sport_specific": r.sport_specific}
            for r in rules
        ],
    }


# ── History ────────────────────────────────────────────────────

@app.get("/api/conversations")
async def recent_conversations(limit: int = Query(default=50, ge=1, le=500)):
    """Recent coaching conversation messages."""
    if not db:
        return []
    messages = db.get_recent_messages(limit)
    return messages


@app.get("/api/nutrition")
async def nutrition_history(days: int = Query(default=7, ge=1, le=90)):
    """Nutrition log history -- aggregated daily totals."""
    if not db:
        return {"daily": [], "meals": []}

    daily = db.get_nutrition_history(days)

    # Also return today's individual meals for detail view
    today_meals = db.get_daily_nutrition(_today())

    return {
        "daily": daily,
        "today_meals": today_meals,
    }


# ── Config ─────────────────────────────────────────────────────

@app.get("/api/athlete")
async def athlete_profile():
    """Athlete profile (read-only)."""
    profile_path = BASE_DIR / "athlete_profile.json"
    if profile_path.exists():
        try:
            data = json.loads(profile_path.read_text())
            return data
        except Exception:
            pass

    # Fallback to config object
    if athlete_cfg:
        return asdict(athlete_cfg)
    return {}


# ── Phase 2 Endpoints ─────────────────────────────────────────

@app.get("/api/training-plan")
async def api_training_plan():
    """Current week's training sessions from periodization engine."""
    try:
        plan_dict = db.get_state("training_plan") if db else None
        if not plan_dict:
            return {"plan_exists": False, "sessions": [], "phase": None, "week_number": None, "target_tss": None, "week_summary": None}

        # Reconstruct plan from stored dict
        if TrainingSession is None:
            return {"plan_exists": False, "sessions": [], "error": "Periodization module unavailable"}

        def _reconstruct(plan_data):
            mesocycles = []
            for meso_data in plan_data.get("mesocycles", []):
                microcycles = []
                for mc_data in meso_data.get("microcycles", []):
                    sessions = []
                    for s_data in mc_data.get("sessions", []):
                        sessions.append(TrainingSession(**s_data))
                    mc_copy = dict(mc_data)
                    mc_copy["sessions"] = sessions
                    microcycles.append(Microcycle(**mc_copy))
                meso_copy = dict(meso_data)
                meso_copy["microcycles"] = microcycles
                mesocycles.append(Mesocycle(**meso_copy))
            plan_copy = dict(plan_data)
            plan_copy["mesocycles"] = mesocycles
            return TrainingPlan(**plan_copy)

        plan = _reconstruct(plan_dict)

        engine = _periodization
        if not engine:
            return {"plan_exists": True, "sessions": [], "phase": None, "week_number": None, "target_tss": None, "week_summary": "Periodization engine unavailable"}

        current_week = engine.get_current_week(plan)
        if not current_week:
            return {"plan_exists": True, "sessions": [], "phase": None, "week_number": None, "target_tss": None, "week_summary": "No current week found in plan"}

        sessions_out = []
        today_str = datetime.now().strftime("%Y-%m-%d")
        for s in current_week.sessions:
            day_name = datetime.strptime(s.date, "%Y-%m-%d").strftime("%a") if s.date else "?"
            sessions_out.append({
                "date": s.date,
                "day": day_name,
                "is_today": s.date == today_str,
                "session_type": s.session_type,
                "sport": s.sport,
                "name": s.name,
                "duration_minutes": s.duration_minutes,
                "target_tss": s.target_tss,
                "intensity_zone": s.intensity_zone,
                "is_key_session": s.is_key_session,
            })

        return {
            "plan_exists": True,
            "phase": current_week.phase,
            "week_number": current_week.week_number,
            "target_tss": current_week.target_weekly_tss,
            "week_summary": current_week.theme,
            "sessions": sessions_out,
        }
    except Exception as exc:
        log.warning("Training plan endpoint error: %s", exc)
        return {"plan_exists": False, "sessions": [], "error": str(exc)}


@app.get("/api/weather")
async def api_weather():
    """Current weather conditions + training adjustments."""
    try:
        wp = _weather_provider
        we = _weather_engine
        if not wp or not we:
            return {"available": False, "reason": "Location not configured or module unavailable"}

        conditions_data = await wp.fetch_current()
        conditions = WeatherConditions(
            temperature_c=conditions_data.get("temperature_c", 0),
            feels_like_c=conditions_data.get("feels_like_c", 0),
            humidity_pct=conditions_data.get("humidity_pct", 0),
            wind_speed_kmh=conditions_data.get("wind_speed_kmh", 0),
            wind_direction_deg=conditions_data.get("wind_direction_deg", 0),
            wind_gusts_kmh=conditions_data.get("wind_gusts_kmh", 0),
            precipitation_mm=conditions_data.get("precipitation_mm", 0),
            precipitation_probability=conditions_data.get("precipitation_probability", 0),
            uv_index=conditions_data.get("uv_index", 0),
            weather_code=conditions_data.get("weather_code", 0),
            description=conditions_data.get("description", ""),
        )
        adjustment = we.assess_conditions(conditions)
        pace_adjustment_pct = round((adjustment.pace_modifier - 1.0) * 100, 1)

        return {
            "available": True,
            "temperature_c": conditions.temperature_c,
            "feels_like_c": conditions.feels_like_c,
            "humidity_pct": conditions.humidity_pct,
            "wind_speed_kmh": conditions.wind_speed_kmh,
            "precipitation_mm": conditions.precipitation_mm,
            "description": conditions.description,
            "pace_adjustment_pct": pace_adjustment_pct,
            "hydration_ml_per_hr": adjustment.hydration_ml_per_hour,
            "clothing": adjustment.clothing_recommendation,
            "safety_warnings": adjustment.warnings,
        }
    except Exception as exc:
        log.warning("Weather endpoint error: %s", exc)
        return {"available": False, "reason": str(exc)}


@app.get("/api/performance-forecast")
async def api_performance_forecast(days: int = Query(default=14, ge=7, le=60)):
    """14-day CTL forecast + race-day projection."""
    try:
        w = await _fetch_wellness_augmented(30)
        a = await _fetch_intervals_activities(30)

        pf = _performance_forecaster
        if not pf:
            return {"error": "Performance forecaster unavailable"}

        if not w:
            return {"error": "No wellness data available"}

        latest = w[-1]
        current_ctl = float(latest.get("ctl", 0) or 0)
        current_atl = float(latest.get("atl", 0) or 0)
        current_tsb = current_ctl - current_atl

        forecast = pf.forecast(
            current_wellness=latest,
            wellness_history=w,
            recent_activities=a,
            horizon_days=days,
        )

        result = {
            "current_ctl": round(current_ctl, 1),
            "current_atl": round(current_atl, 1),
            "current_tsb": round(current_tsb, 1),
            "horizon_days": days,
            "predicted_ctl": forecast.get("predicted_ctl"),
            "predicted_atl": forecast.get("predicted_atl"),
            "predicted_tsb": forecast.get("predicted_tsb"),
            "trend": forecast.get("trend"),
            "confidence": forecast.get("confidence"),
            "model_type": forecast.get("model_type"),
            "race_day": None,
        }

        # Race-day projection
        if athlete_cfg and athlete_cfg.race_date:
            try:
                race_dt = datetime.strptime(athlete_cfg.race_date, "%Y-%m-%d")
                days_to_race = (race_dt - datetime.now()).days
                if 0 < days_to_race <= 180:
                    race_forecast = pf.forecast(
                        current_wellness=latest,
                        wellness_history=w,
                        recent_activities=a,
                        horizon_days=days_to_race,
                    )
                    result["race_day"] = {
                        "days_to_race": days_to_race,
                        "race_name": athlete_cfg.race_name,
                        "predicted_ctl": race_forecast.get("predicted_ctl"),
                        "predicted_tsb": race_forecast.get("predicted_tsb"),
                        "trend": race_forecast.get("trend"),
                    }
            except Exception:
                pass

        return result
    except Exception as exc:
        log.warning("Performance forecast endpoint error: %s", exc)
        return {"error": str(exc)}


# ── Strava History ────────────────────────────────────────────

@app.get("/api/strava/activities")
async def strava_activity_history(days: int = Query(default=90, ge=1, le=3650)):
    """Activity history from local Strava DB (populated by /strava sync).

    Supports up to 10 years of history — unlike Intervals.icu which caps at 365 days.
    Run /strava sync in Telegram first to populate the local database.
    """
    acts = db.get_strava_activities(days=days)
    result = []
    for a in acts:
        dist_km = (a.get("distance") or 0) / 1000
        dur_min = (a.get("moving_time") or 0) // 60
        avg_spd = a.get("average_speed") or 0
        sport_lc = (a.get("type") or "").lower()

        # Compute pace/speed in display-friendly format
        pace_display = None
        if avg_spd > 0 and sport_lc in ("run", "trailrun", "walk", "hike"):
            secs_km = 1000 / avg_spd
            pace_display = f"{int(secs_km // 60)}:{int(secs_km % 60):02d}/km"
        elif avg_spd > 0 and sport_lc in ("ride", "virtualride", "mountainbikeride", "gravelride"):
            pace_display = f"{avg_spd * 3.6:.1f} km/h"

        result.append({
            "date": (a.get("start_date_local") or "")[:10],
            "type": a.get("type"),
            "name": a.get("name", ""),
            "duration_min": dur_min,
            "distance_km": round(dist_km, 2),
            "avg_hr": a.get("average_heartrate"),
            "max_hr": a.get("max_heartrate"),
            "elevation_m": a.get("total_elevation_gain"),
            "avg_watts": a.get("average_watts"),
            "np_watts": a.get("weighted_average_watts"),
            "suffer_score": a.get("suffer_score"),
            "pace_display": pace_display,
            "strava_id": a.get("strava_id"),
        })
    return result


@app.get("/api/strava/stats")
async def strava_stats():
    """All-time Strava stats: totals by sport and monthly volume breakdown.

    Reads from local strava_activities DB — populated by /strava sync.
    """
    from collections import defaultdict

    all_acts = db.get_strava_activities()   # all time, no date filter

    sport_totals: dict = defaultdict(lambda: {"count": 0, "distance_km": 0.0, "duration_min": 0})
    monthly: dict = defaultdict(lambda: {"distance_km": 0.0, "duration_min": 0, "count": 0})

    for a in all_acts:
        sport    = (a.get("type") or "Unknown")
        dist_km  = (a.get("distance") or 0) / 1000
        dur_min  = (a.get("moving_time") or 0) // 60
        month    = (a.get("start_date_local") or "")[:7]   # YYYY-MM

        sport_totals[sport]["count"]        += 1
        sport_totals[sport]["distance_km"]  += dist_km
        sport_totals[sport]["duration_min"] += dur_min

        if month:
            monthly[month]["distance_km"]  += dist_km
            monthly[month]["duration_min"] += dur_min
            monthly[month]["count"]        += 1

    # Keep last 24 months sorted
    monthly_sorted = [
        {"month": k, **v}
        for k, v in sorted(monthly.items())
    ][-24:]

    # Sort sports by distance descending
    sport_list = [
        {"sport": k, **{sk: round(sv, 1) if isinstance(sv, float) else sv for sk, sv in v.items()}}
        for k, v in sorted(sport_totals.items(), key=lambda x: -x[1]["distance_km"])
    ]

    return {
        "total_activities": len(all_acts),
        "by_sport": sport_list,
        "monthly": monthly_sorted,
    }


# ── API Usage ─────────────────────────────────────────────────

@app.get("/api/usage")
async def api_usage(days: int = 7):
    """API usage summary with token counts and costs."""
    summary = db.get_usage_summary(days=days)
    daily = db.get_daily_cost(days=days)
    total_cost = sum(row.get("total_cost", 0) or 0 for row in summary)
    total_calls = sum(row.get("calls", 0) or 0 for row in summary)
    return {
        "period_days": days,
        "total_cost_usd": round(total_cost, 4),
        "total_calls": total_calls,
        "by_provider": summary,
        "daily": daily,
    }


# ── Static Files & SPA ────────────────────────────────────────

@app.get("/")
async def serve_index():
    """Serve the dashboard frontend."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {"message": "Dashboard frontend not found. Place index.html in static/"},
        status_code=404,
    )


# Mount static directory for CSS/JS/assets (after API routes so they take priority)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Main ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    import os
    port = int(os.environ.get("PORT", 3000))
    uvicorn.run(
        "dashboard:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
    )
