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
from apscheduler.triggers.interval import IntervalTrigger

from config import load_app_config, load_athlete_config, ConfigError, TZ, BASE_DIR
from database import Database
from intervals import IntervalsClient
from engine import CoachingEngine
from whoop import WhoopClient, start_oauth_server
from handlers import Handlers
from logging_config import setup_logging

# ── Logging (re-configured after config load) ───────────────

LOG_DIR = BASE_DIR / "logs"
setup_logging(LOG_DIR)
log = logging.getLogger("coach")


# ── Main ─────────────────────────────────────────────────────

def main():
    # Load config
    app_cfg = load_app_config()
    athlete = load_athlete_config()

    # Validate configuration
    errors = app_cfg.validate()
    athlete_errors = athlete.validate()
    if athlete_errors:
        for e in athlete_errors:
            log.warning(f"Athlete config: {e}")
    if errors:
        log.error(f"Config validation failed: {'; '.join(errors)}")
        return

    # Re-configure logging with configured level
    if app_cfg.log_level != "INFO":
        setup_logging(LOG_DIR, level=app_cfg.log_level)

    # Initialize components
    db = Database(app_cfg.db_path)

    # Run any pending DB migrations (safe to run every startup — tracks applied versions)
    try:
        from migrations.runner import run_migrations
        applied = run_migrations(app_cfg.db_path)
        if applied:
            log.info(f"DB migrations applied: {', '.join(applied)}")
    except Exception as e:
        log.warning(f"Migration runner failed (non-fatal): {e}")

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

    # ── Phase 2: Multi-agent architecture ─────────────────────────────────────

    # Coaching state machine
    state_machine = None
    try:
        from coaching_state_machine import CoachingStateMachine
        state_machine = CoachingStateMachine(db, athlete)
        log.info(f"State machine initialized — current phase: {state_machine.current_state}")
    except Exception as e:
        log.warning(f"State machine init failed: {e}")

    # Long-term athlete memory
    _memory = None
    try:
        from memory import AthleteMemory
        # Reuse the vector store from the knowledge base (same ChromaDB instance)
        _vector_store = None
        if _kb:
            try:
                _vector_store = _kb.vector_store
            except AttributeError:
                from modules.vector_store import VectorStore
                _vector_store = VectorStore()
        else:
            from modules.vector_store import VectorStore
            _vector_store = VectorStore()
        _memory = AthleteMemory(db, _vector_store)
        log.info(f"Athlete memory initialized — {_memory.count()} memories stored")
    except Exception as e:
        log.warning(f"Athlete memory init failed: {e}")

    # Agent orchestrator (drop-in replacement for engine)
    orchestrator = None
    try:
        from orchestrator import AgentOrchestrator
        from engine_tools import CoachTools
        tools = CoachTools(
            iv=iv, db=db, athlete=athlete, whoop=whoop,
            weather_provider=_weather_provider, weather_engine=_weather_engine,
            rag=_rag, simulator=_simulator, strava=strava,
        )
        orchestrator = AgentOrchestrator(
            api_key=app_cfg.anthropic_api_key,
            athlete=athlete,
            db=db,
            tools=tools,
            memory=_memory,
            state_machine=state_machine,
        )
        log.info("Agent orchestrator initialized (3 agents: daily_coach, analysis, planning)")
    except Exception as e:
        log.warning(f"Orchestrator init failed — falling back to single engine: {e}")

    # Use orchestrator if available, otherwise fall back to single engine
    active_engine = orchestrator if orchestrator else engine

    # Initialize autonomous coaching reactor
    reactor = None
    try:
        from reactor import CoachingReactor
        reactor = CoachingReactor(
            iv=iv, db=db, athlete=athlete, whoop=whoop, strava=strava,
            state_machine=state_machine,
        )
        log.info("Coaching reactor initialized")
    except Exception as e:
        log.warning(f"Reactor init failed: {e}")

    hdlrs = Handlers(iv, active_engine, db, athlete, app_cfg.telegram_chat_id, whoop=whoop, strava=strava, reactor=reactor)

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

        # Post-activity Strava notification — every 5 minutes
        scheduler.add_job(
            hdlrs.run_activity_notification,
            IntervalTrigger(minutes=5),
            args=[application.bot],
            id="activity_check",
        )

        # Morning reactor — runs at 07:00 before the 08:30 check-in
        if reactor:
            async def run_morning_reactor(bot):
                log.info("Running morning reactor (07:00)")
                try:
                    result = await reactor.run_morning()
                    # Push critical alerts immediately
                    critical = reactor.get_critical_alerts(result.get("alerts", []))
                    for alert in critical:
                        try:
                            msg = reactor.format_alert_message(alert)
                            await bot.send_message(
                                chat_id=app_cfg.telegram_chat_id,
                                text=msg,
                                parse_mode="Markdown",
                            )
                            reactor.mark_alert_pushed(alert)
                            log.info("Pushed critical alert: %s", alert.get("title"))
                        except Exception as exc:
                            log.error("Failed to push alert: %s", exc)
                except Exception as exc:
                    log.error("Morning reactor failed: %s", exc)

            scheduler.add_job(
                run_morning_reactor,
                CronTrigger(hour=7, minute=0, timezone=TZ),
                args=[application.bot],
                id="morning_reactor",
            )

        scheduler.start()
        log.info(
            "Scheduler started — reactor 07:00 + check-ins 8:30/13:00/22:00 + Sunday report "
            "+ Monday retrain + activity notifications every 5min (Europe/Paris)"
        )

    # Build Telegram app
    app = Application.builder().token(app_cfg.telegram_bot_token).post_init(post_init).build()
    hdlrs.register(app)

    log.info("Coach bot starting polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
