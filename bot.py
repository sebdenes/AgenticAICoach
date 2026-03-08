#!/usr/bin/env python3
"""
Coach — Elite Endurance Performance Coaching Bot
Slim orchestrator: config, database, API clients, handlers, scheduler.

Usage: python3 bot.py
"""

import logging

from telegram.ext import Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import load_app_config, load_athlete_config, TZ, BASE_DIR
from database import Database
from intervals import IntervalsClient
from engine import CoachingEngine
from whoop import WhoopClient, start_oauth_server
from handlers import Handlers

# ── Logging ──────────────────────────────────────────────────

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "coach.log"),
    ],
)
log = logging.getLogger("coach")


# ── Main ─────────────────────────────────────────────────────

def main():
    # Load config
    app_cfg = load_app_config()
    athlete = load_athlete_config()

    required = {
        "INTERVALS_API_KEY": app_cfg.intervals_api_key,
        "INTERVALS_ATHLETE_ID": app_cfg.intervals_athlete_id,
        "TELEGRAM_BOT_TOKEN": app_cfg.telegram_bot_token,
        "TELEGRAM_CHAT_ID": app_cfg.telegram_chat_id,
        "ANTHROPIC_API_KEY": app_cfg.anthropic_api_key,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        log.error(f"Missing config in config.env: {', '.join(missing)}")
        return

    # Initialize components
    db = Database(app_cfg.db_path)
    iv = IntervalsClient(app_cfg.intervals_api_key, app_cfg.intervals_athlete_id, db)

    # Initialize Whoop client (optional — works without credentials)
    whoop = None
    if app_cfg.whoop_client_id and app_cfg.whoop_client_secret:
        whoop = WhoopClient(
            app_cfg.whoop_client_id,
            app_cfg.whoop_client_secret,
            app_cfg.whoop_redirect_uri,
            db,
        )
        start_oauth_server(whoop, port=8765)
        status = "authenticated" if whoop.is_authenticated else "awaiting auth (/whoop)"
        log.info(f"Whoop client initialized — {status}")

    # Initialize Strava client (optional — OAuth via dashboard /strava/auth)
    strava = None
    if app_cfg.strava_client_id and app_cfg.strava_client_secret:
        try:
            from strava import StravaClient
            strava = StravaClient(
                app_cfg.strava_client_id,
                app_cfg.strava_client_secret,
                app_cfg.strava_redirect_uri,
                db,
            )
            status = "authenticated" if strava.is_authenticated else "awaiting auth (/strava)"
            log.info(f"Strava client initialized — {status}")
        except Exception as e:
            log.warning(f"Strava client init failed: {e}")

    # ── Phase 2 modules (engine takes ownership) ─────────────────────────────────
    _kb = None
    _weather_provider = None
    _weather_engine = None
    _rag = None
    _simulator = None

    try:
        from modules.knowledge_base import KnowledgeBase
        _kb = KnowledgeBase()
    except Exception as e:
        log.warning(f"KnowledgeBase init failed: {e}")

    try:
        from data_providers.weather_provider import WeatherProvider
        from modules.weather import WeatherEngine
        if getattr(athlete, 'latitude', 0):
            _weather_provider = WeatherProvider(latitude=athlete.latitude, longitude=athlete.longitude)
            _weather_engine = WeatherEngine(knowledge_base=_kb)
            log.info("Weather modules initialized")
    except Exception as e:
        log.warning(f"Weather module init failed: {e}")

    try:
        from modules.rag_engine import RAGEngine
        _rag = RAGEngine(_kb)
        log.info("RAG engine initialized")
    except Exception as e:
        log.warning(f"RAG engine init failed: {e}")

    try:
        from modules.simulation import ScenarioSimulator
        _simulator = ScenarioSimulator()
        log.info("Scenario simulator initialized")
    except Exception as e:
        log.warning(f"Simulator init failed: {e}")

    engine = CoachingEngine(
        app_cfg.anthropic_api_key, athlete, db,
        iv=iv, whoop=whoop,
        weather_provider=_weather_provider,
        weather_engine=_weather_engine,
        rag=_rag,
        simulator=_simulator,
        strava=strava,
    )

    hdlrs = Handlers(iv, engine, db, athlete, app_cfg.telegram_chat_id, whoop=whoop, strava=strava)

    log.info(f"Coach initialized for {athlete.name} | Race: {athlete.race_name} ({athlete.race_date})")

    # Scheduler — start inside post_init so the event loop exists
    async def post_init(application: Application):
        scheduler = AsyncIOScheduler(timezone=TZ)
        scheduler.add_job(
            hdlrs.run_scheduled_checkin,
            CronTrigger(hour=8, minute=30, timezone=TZ),
            args=["morning", application.bot],
            id="morning",
        )
        scheduler.add_job(
            hdlrs.run_scheduled_checkin,
            CronTrigger(hour=13, minute=0, timezone=TZ),
            args=["afternoon", application.bot],
            id="afternoon",
        )
        scheduler.add_job(
            hdlrs.run_scheduled_checkin,
            CronTrigger(hour=22, minute=0, timezone=TZ),
            args=["evening", application.bot],
            id="evening",
        )
        # Sunday evening weekly report
        scheduler.add_job(
            hdlrs.run_weekly_report,
            CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=TZ),
            args=[application.bot],
            id="weekly_report",
        )

        # Weekly ML model retraining (Monday 3:00 AM — low-traffic time)
        async def retrain_models(bot):
            log.info("Running weekly model retraining")
            try:
                w = await iv.wellness(days=90, force=True)
                a = await iv.activities(days=90, force=True)
                if w and len(w) >= 14:
                    try:
                        meta = hdlrs._recovery_predictor.train(w, a)
                        log.info(f"Recovery model retrained: R²={meta.score:.2f}")
                    except Exception as e:
                        log.warning(f"Recovery model retrain failed: {e}")
                    try:
                        meta = hdlrs._performance_forecaster.train(w, a)
                        log.info(f"Performance model retrained: R²={meta.score:.2f}")
                    except Exception as e:
                        log.warning(f"Performance model retrain failed: {e}")
                else:
                    log.info("Not enough data for model retraining")
            except Exception as e:
                log.error(f"Weekly retraining error: {e}")

        scheduler.add_job(
            retrain_models,
            CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=TZ),
            args=[application.bot],
            id="weekly_retrain",
        )

        scheduler.start()
        log.info(
            "Scheduler started — check-ins 8:30/13:00/22:00 + Sunday report "
            "+ Monday retrain (Europe/Paris)"
        )

    # Build Telegram app
    app = Application.builder().token(app_cfg.telegram_bot_token).post_init(post_init).build()
    hdlrs.register(app)

    log.info("Coach bot starting polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
