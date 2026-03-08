"""Telegram handlers — rich UX with inline keyboards, charts, and quick commands."""

from __future__ import annotations

import io
import base64
import logging
import asyncio
import tempfile
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.constants import ChatAction

from config import TZ, AthleteConfig
from database import Database
from intervals import IntervalsClient
from engine import CoachingEngine
from charts import sleep_chart, fitness_chart, hrv_chart, weekly_load_chart
from modules.nutrition import classify_training_load
from modules.mfp import parse_meal_from_text, get_daily_summary, format_nutrition_tracking_context, is_meal_log, get_targets_for_load
from modules.knowledge_base import KnowledgeBase
from modules.periodization import PeriodizationEngine
from modules.athlete_models import RecoveryPredictor, PerformanceForecaster
from modules.periodization_calendar import PeriodizationCalendar
from whoop import WhoopClient, wait_for_auth_code

log = logging.getLogger("coach.handlers")


class Handlers:
    """Registers all Telegram handlers and manages data flow."""

    def __init__(
        self,
        iv: IntervalsClient,
        engine: CoachingEngine,
        db: Database,
        athlete: AthleteConfig,
        chat_id: str,
        whoop: WhoopClient = None,
        strava=None,
    ):
        self.iv = iv
        self.engine = engine
        self.db = db
        self.athlete = athlete
        self.chat_id = chat_id
        self.whoop = whoop
        self.strava = strava
        # Knowledge base (still needed for periodization)
        self._kb = KnowledgeBase()
        # Phase 2 modules
        self._periodization = PeriodizationEngine(athlete, knowledge_base=self._kb)
        self._recovery_predictor = RecoveryPredictor()
        self._performance_forecaster = PerformanceForecaster()
        self._calendar = PeriodizationCalendar(iv)

    def register(self, app):
        """Register all handlers on the Telegram Application."""
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("data", self.cmd_data))
        app.add_handler(CommandHandler("week", self.cmd_week))
        app.add_handler(CommandHandler("checkin", self.cmd_checkin))
        app.add_handler(CommandHandler("sleep", self.cmd_sleep))
        app.add_handler(CommandHandler("fitness", self.cmd_fitness))
        app.add_handler(CommandHandler("hrv", self.cmd_hrv))
        app.add_handler(CommandHandler("recovery", self.cmd_recovery))
        app.add_handler(CommandHandler("nutrition", self.cmd_nutrition))
        app.add_handler(CommandHandler("strength", self.cmd_strength))
        app.add_handler(CommandHandler("boston", self.cmd_boston))
        app.add_handler(CommandHandler("predict", self.cmd_predict))
        app.add_handler(CommandHandler("compliance", self.cmd_compliance))
        app.add_handler(CommandHandler("adapt", self.cmd_adapt))
        app.add_handler(CommandHandler("log", self.cmd_log_meal))
        app.add_handler(CommandHandler("macros", self.cmd_macros))
        app.add_handler(CommandHandler("whoop", self.cmd_whoop))
        app.add_handler(CommandHandler("strava", self.cmd_strava))
        app.add_handler(CommandHandler("strain", self.cmd_strain))
        app.add_handler(CommandHandler("whoopsleep", self.cmd_whoop_sleep))
        app.add_handler(CommandHandler("explain", self.cmd_explain))
        app.add_handler(CommandHandler("plan", self.cmd_plan))
        app.add_handler(CommandHandler("replan", self.cmd_replan))
        app.add_handler(CommandHandler("whatif", self.cmd_whatif))
        app.add_handler(CommandHandler("weather", self.cmd_weather))
        app.add_handler(CommandHandler("forecast", self.cmd_forecast))
        app.add_handler(CommandHandler("retrain", self.cmd_retrain))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        app.add_handler(MessageHandler(filters.PHOTO, self.on_photo))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))

    # ── Helper: gather data ───────────────────────────────────

    async def _gather(self, days_w=7, days_a=3, days_e=2, include_whoop=True) -> tuple:
        """Fetch and return (wellness, activities, events, formatted_context)."""
        import asyncio

        coros = [
            self.iv.wellness(days_w),
            self.iv.activities(days_a),
            self.iv.events(days_e),
        ]
        # Fetch Whoop data in parallel if authenticated
        fetch_whoop = include_whoop and self.whoop and self.whoop.is_authenticated
        if fetch_whoop:
            coros.append(self.whoop.all_data(days_w))

        results = await asyncio.gather(*coros, return_exceptions=True)
        w = results[0] if not isinstance(results[0], Exception) else []
        a = results[1] if not isinstance(results[1], Exception) else []
        e = results[2] if not isinstance(results[2], Exception) else []

        parts = [
            IntervalsClient.fmt_wellness(w),
            IntervalsClient.fmt_activities(a),
            IntervalsClient.fmt_events(e),
        ]
        if fetch_whoop and len(results) > 3 and not isinstance(results[3], Exception):
            parts.append(WhoopClient.fmt_all(results[3]))

        ctx = "\n\n".join(parts)
        return w, a, e, ctx

    async def _typing(self, update: Update):
        await update.effective_chat.send_action(ChatAction.TYPING)

    async def _safe_reply(self, update_or_chat, text: str, reply_markup=None, bot=None):
        """Send reply, falling back to plain text if Markdown fails."""
        kwargs = {"parse_mode": "Markdown"}
        if reply_markup:
            kwargs["reply_markup"] = reply_markup

        # If called with a chat_id string (for scheduled messages)
        if isinstance(update_or_chat, str) and bot:
            try:
                await bot.send_message(chat_id=update_or_chat, text=text, **kwargs)
            except Exception:
                kwargs.pop("parse_mode", None)
                try:
                    await bot.send_message(chat_id=update_or_chat, text=text, **kwargs)
                except Exception as exc:
                    log.error(f"Send failed: {exc}")
            return

        # Normal Update reply
        try:
            await update_or_chat.message.reply_text(text, **kwargs)
        except Exception:
            kwargs.pop("parse_mode", None)
            try:
                await update_or_chat.message.reply_text(text, **kwargs)
            except Exception as exc:
                await update_or_chat.message.reply_text(f"Error: {exc}")

    async def _send_chart(self, update: Update, chart_bytes: bytes, caption: str = ""):
        """Send a chart image to the user."""
        if chart_bytes:
            await update.message.reply_photo(
                photo=io.BytesIO(chart_bytes),
                caption=caption,
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("No data available for chart.")

    # ── Commands ──────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Recovery", callback_data="recovery"),
                InlineKeyboardButton("Sleep", callback_data="sleep"),
            ],
            [
                InlineKeyboardButton("Fitness", callback_data="fitness"),
                InlineKeyboardButton("Nutrition", callback_data="nutrition"),
            ],
            [
                InlineKeyboardButton("Today's Plan", callback_data="plan_today"),
                InlineKeyboardButton("Boston", callback_data="boston"),
            ],
        ])
        await update.message.reply_text(
            "*Coach is online.*\n\n"
            "I pull your Intervals.icu + Whoop data in real-time.\n\n"
            "*Ask me anything:*\n"
            "_How should I train today?_\n"
            "_What should I eat before my run?_\n"
            "_How's my sleep trend?_\n\n"
            "*Commands:*\n"
            "/sleep — sleep analysis + chart\n"
            "/fitness — CTL/ATL/TSB chart\n"
            "/recovery — recovery score\n"
            "/predict — marathon prediction\n"
            "/nutrition — daily macro targets\n"
            "/log — log a meal (e.g. /log lunch: rice chicken 500cal 40p)\n"
            "/macros — today's macro tracker\n"
            "/strength — strength session\n"
            "/compliance — plan adherence\n"
            "/adapt — training adaptation\n"
            "/plan — training plan (this week + overview)\n"
            "/replan — regenerate training plan\n"
            "/whatif — simulate workout impact\n"
            "/weather — current conditions + adjustments\n"
            "/forecast — performance forecast (14d)\n"
            "/whoop — Whoop status + connect\n"
            "/strain — Whoop strain + recovery\n"
            "/whoopsleep — Whoop sleep stages\n"
            "/boston — race countdown\n"
            "/week — weekly summary\n"
            "/help — all commands\n\n"
            "Voice notes supported. Check-ins: *8:30 · 13:00 · 22:00*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    async def cmd_explain(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show the reasoning behind the last coaching recommendation."""
        await self._typing(update)
        text = await self.engine.respond(
            "Explain the reasoning behind the last coaching recommendation."
        )
        await self._safe_reply(update, text)

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "*Coach Commands*\n\n"
            "*Analysis:*\n"
            "/sleep — Sleep analysis + chart\n"
            "/fitness — CTL/ATL/TSB chart\n"
            "/hrv — HRV + RHR trends\n"
            "/recovery — Recovery readiness score\n"
            "/predict — Marathon time prediction\n"
            "/compliance — Plan adherence report\n"
            "/adapt — Training adaptation status\n"
            "/explain — Reasoning behind last recommendation\n\n"
            "*Nutrition:*\n"
            "/nutrition — Daily macro targets\n"
            "/log — Log a meal (e.g. /log lunch: pasta 600cal 30p 80c 15f)\n"
            "/macros — Today's macro tracker\n\n"
            "*Whoop:*\n"
            "/whoop — Connect or view Whoop status\n"
            "/strain — Strain + recovery dashboard\n"
            "/whoopsleep — Sleep stages + efficiency\n\n"
            "*Training:*\n"
            "/strength — Strength session for current phase\n"
            "/plan — Training plan (this week + overview)\n"
            "/replan — Regenerate training plan from current fitness\n"
            "/whatif — Simulate workout impact (e.g. /whatif 3h easy ride)\n"
            "/weather — Current conditions + training adjustments\n"
            "/forecast — ML performance forecast (14-day CTL trend)\n"
            "/retrain — Retrain ML models from latest data\n"
            "/boston — Boston Marathon countdown\n"
            "/week — Full weekly summary\n"
            "/data — Raw data dump\n"
            "/checkin — Manual check-in\n\n"
            "Or just send any message for coaching.",
            parse_mode="Markdown",
        )

    async def cmd_data(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond("Show me my latest training data and biometrics.")
        await self._safe_reply(update, text)

    async def cmd_week(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Give me a summary of this week's training and what's planned ahead."
        )
        await self._safe_reply(update, text)

        # Send charts — still need raw data for charting
        _, a, _, _ = await self._gather(days_w=14, days_a=14, days_e=7)
        chart = weekly_load_chart(a)
        if chart:
            await self._send_chart(update, chart, "Weekly Training Load")

    async def cmd_checkin(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        hour = datetime.now(TZ).hour
        if hour < 11:
            ctype = "morning"
        elif hour < 18:
            ctype = "afternoon"
        else:
            ctype = "evening"
        await self._typing(update)
        text = await self.engine.respond(
            "Give me a comprehensive coaching check-in.",
            checkin_type=ctype,
        )
        await self._safe_reply(update, text)

    async def cmd_sleep(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Analyze my recent sleep patterns and give me coaching recommendations."
        )
        await self._safe_reply(update, text)

        # Still need raw wellness data for the chart
        w, _, _, _ = await self._gather(days_w=14)
        chart = sleep_chart(w)
        if chart:
            await self._send_chart(update, chart, "14-Day Sleep Trend")

    async def cmd_fitness(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Analyze my current fitness and training load (CTL, ATL, TSB, ramp rate)."
        )
        await self._safe_reply(update, text)

        # Still need raw wellness data for the chart
        w, _, _, _ = await self._gather(days_w=14)
        chart = fitness_chart(w)
        if chart:
            await self._send_chart(update, chart, "Fitness & Fatigue")

    async def cmd_hrv(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Analyze my HRV trends and what they mean for training today."
        )
        await self._safe_reply(update, text)

        # Still need raw wellness data for the chart
        w, _, _, _ = await self._gather(days_w=14)
        chart = hrv_chart(w)
        if chart:
            await self._send_chart(update, chart, "HRV & RHR Trends")

    async def cmd_recovery(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Assess my current recovery status and give recommendations for today."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Sleep Details", callback_data="sleep"),
                InlineKeyboardButton("HRV Trends", callback_data="hrv"),
            ],
        ])
        await self._safe_reply(update, text, reply_markup=keyboard)

    async def cmd_nutrition(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Calculate my nutrition and macro targets for today's training load."
        )
        await self._safe_reply(update, text)

    async def cmd_strength(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond("What strength training should I do today?")
        await self._safe_reply(update, text)

    async def cmd_boston(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond("Analyze my race readiness for Boston Marathon.")
        await self._safe_reply(update, text)

    async def cmd_predict(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Predict my marathon race time based on current fitness."
        )
        await self._safe_reply(update, text)

    async def cmd_compliance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Show me my training compliance — how well am I following the plan?"
        )
        await self._safe_reply(update, text)

    async def cmd_adapt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._typing(update)
        text = await self.engine.respond(
            "Should my training plan be adapted based on recent data?"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Recovery", callback_data="recovery"),
                InlineKeyboardButton("Sleep", callback_data="sleep"),
            ],
        ])
        await self._safe_reply(update, text, reply_markup=keyboard)

    # ── Phase 2 Commands ──────────────────────────────────────

    async def cmd_plan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show current training plan (this week + overview)."""
        await self._typing(update)
        try:
            # Check for existing plan in state
            plan_data = self.db.get_state("training_plan")

            if not plan_data:
                # Generate a new plan
                w, _, _, _ = await self._gather(days_w=7)
                latest = w[-1] if w else {}
                current_ctl = float(latest.get("ctl", 0) or 0)
                plan = self._periodization.generate_plan(current_ctl)
                # Store as state
                import dataclasses
                plan_dict = dataclasses.asdict(plan)
                self.db.set_state("training_plan", plan_dict)
                plan_data = plan_dict
                # Push new plan to Intervals.icu calendar
                push_msg = ""
                try:
                    push_result = await self._calendar.push_plan(plan)
                    push_msg = f"\n\n📅 *Pushed to Intervals.icu:* {push_result['created']} sessions"
                    if push_result['errors']:
                        push_msg += f" ({push_result['errors']} failed)"
                except Exception as exc:
                    log.warning(f"Calendar push failed: {exc}")
                    push_msg = "\n\n⚠️ _Calendar push failed — check logs_"
            else:
                push_msg = ""

            # Reconstruct plan for formatting
            from modules.periodization import TrainingPlan, Mesocycle, Microcycle, TrainingSession
            plan = self._reconstruct_plan(plan_data)

            # Get current week
            current_week = self._periodization.get_current_week(plan)
            lines = []
            if current_week:
                lines.append(self._periodization.format_week_summary(current_week))
            else:
                lines.append("No sessions scheduled for this week.")

            lines.append("")
            lines.append(self._periodization.format_plan_overview(plan))

            text = "\n".join(lines)
            # Truncate for Telegram
            if len(text) > 3900:
                text = text[:3900] + "\n..."
            await update.message.reply_text(f"```\n{text}\n```" + push_msg, parse_mode="Markdown")
        except Exception as exc:
            log.warning(f"Plan command failed: {exc}")
            await update.message.reply_text(f"Could not generate plan: {exc}")

    async def cmd_replan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Re-generate plan based on current state."""
        await self._typing(update)
        try:
            w, _, _, _ = await self._gather(days_w=7)
            latest = w[-1] if w else {}
            current_ctl = float(latest.get("ctl", 0) or 0)

            plan = self._periodization.generate_plan(current_ctl)

            import dataclasses
            plan_dict = dataclasses.asdict(plan)
            self.db.set_state("training_plan", plan_dict)

            # Push new plan to Intervals.icu calendar
            push_msg = ""
            try:
                push_result = await self._calendar.push_plan(plan)
                push_msg = f"\n\n📅 *Pushed to Intervals.icu:* {push_result['created']} sessions"
                if push_result['errors']:
                    push_msg += f" ({push_result['errors']} failed)"
                push_msg += "\n_⚠️ Old planned events not auto-deleted — remove manually if needed_"
            except Exception as exc:
                log.warning(f"Calendar push (replan) failed: {exc}")
                push_msg = "\n\n⚠️ _Calendar push failed — check logs_"

            overview = self._periodization.format_plan_overview(plan)
            if len(overview) > 3900:
                overview = overview[:3900] + "\n..."
            await update.message.reply_text(
                f"*Plan regenerated* (CTL: {current_ctl:.0f})\n\n```\n{overview}\n```" + push_msg,
                parse_mode="Markdown",
            )
        except Exception as exc:
            log.warning(f"Replan command failed: {exc}")
            await update.message.reply_text(f"Could not regenerate plan: {exc}")

    async def cmd_whatif(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Run scenario simulation: /whatif 3h easy ride tomorrow"""
        await self._typing(update)
        text = update.message.text
        args = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""

        if not args:
            await update.message.reply_text(
                "*What-If Simulator*\n\n"
                "Usage: /whatif <workout description>\n\n"
                "Examples:\n"
                "/whatif 3h easy ride\n"
                "/whatif 10km tempo run\n"
                "/whatif 90min Z2 ride\n"
                "/whatif intervals 8x400m",
                parse_mode="Markdown",
            )
            return

        text = await self.engine.respond(f"Simulate the impact of this workout: {args}")
        await self._safe_reply(update, text)

    async def cmd_weather(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Current conditions and training adjustments."""
        await self._typing(update)
        text = await self.engine.respond(
            "What are the current weather conditions and how should I adjust my training today?"
        )
        await self._safe_reply(update, text)

    async def cmd_forecast(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show ML performance forecast."""
        await self._typing(update)
        text = await self.engine.respond(
            "Show me my performance forecast and race readiness prediction."
        )
        await self._safe_reply(update, text)

    async def cmd_retrain(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Trigger ML model retraining from latest data."""
        await self._typing(update)
        w, a, _, _ = await self._gather(days_w=90, days_a=90)
        if not w or len(w) < 14:
            await self._safe_reply(
                update, "⚠️ Not enough data to train (need 14+ days of wellness)."
            )
            return

        results = []

        # Recovery predictor
        try:
            meta = self._recovery_predictor.train(w, a)
            results.append(
                f"✅ Recovery model: R²={meta.score:.2f}, "
                f"{meta.training_samples} samples"
            )
        except Exception as e:
            results.append(f"❌ Recovery model failed: {e}")

        # Performance forecaster
        try:
            meta = self._performance_forecaster.train(w, a)
            results.append(
                f"✅ Performance model: R²={meta.score:.2f}, "
                f"{meta.training_samples} samples"
            )
        except Exception as e:
            results.append(f"❌ Performance model failed: {e}")

        await self._safe_reply(
            update, "🔄 *Model Retraining*\n\n" + "\n".join(results)
        )

    def _reconstruct_plan(self, plan_data: dict):
        """Reconstruct a TrainingPlan object from a stored dict."""
        from modules.periodization import TrainingPlan, Mesocycle, Microcycle, TrainingSession
        mesocycles = []
        for meso_data in plan_data.get("mesocycles", []):
            microcycles = []
            for mc_data in meso_data.get("microcycles", []):
                sessions = []
                for s_data in mc_data.get("sessions", []):
                    sessions.append(TrainingSession(**s_data))
                mc_data_copy = dict(mc_data)
                mc_data_copy["sessions"] = sessions
                microcycles.append(Microcycle(**mc_data_copy))
            meso_data_copy = dict(meso_data)
            meso_data_copy["microcycles"] = microcycles
            mesocycles.append(Mesocycle(**meso_data_copy))
        plan_data_copy = dict(plan_data)
        plan_data_copy["mesocycles"] = mesocycles
        return TrainingPlan(**plan_data_copy)

    async def cmd_log_meal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Log a meal: /log lunch: chicken rice 600cal 40p 80c 15f"""
        text = update.message.text
        # Remove the /log command prefix
        meal_text = text[4:].strip() if text.startswith("/log") else text.strip()

        if not meal_text:
            await update.message.reply_text(
                "*How to log meals:*\n"
                "/log breakfast: oatmeal banana 400cal 15p 60c 10f\n"
                "/log lunch: chicken rice 600cal 40p 80c 15f\n"
                "/log dinner: salmon pasta 700cal 45p 90c 20f\n"
                "/log snack: protein bar 25p 30c 10f\n\n"
                "Calories auto-calculate from macros if omitted.",
                parse_mode="Markdown",
            )
            return

        meal = parse_meal_from_text(meal_text)
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        # Store in DB
        self.db.log_meal(
            date=today,
            meal_type=meal["meal_type"],
            description=meal["description"],
            calories=meal.get("calories"),
            protein=meal.get("protein_g"),
            carbs=meal.get("carbs_g"),
            fat=meal.get("fat_g"),
        )

        # Build response
        parts = [f"Logged *{meal['meal_type']}*: {meal['description']}"]
        if meal.get("has_macros"):
            parts.append(
                f"{meal.get('calories', '?')} kcal | "
                f"P:{meal.get('protein_g', '?')}g C:{meal.get('carbs_g', '?')}g F:{meal.get('fat_g', '?')}g"
            )
        else:
            parts.append("_No macros — add calories/macros for tracking_")

        # Show daily progress
        day_meals = self.db.get_daily_nutrition(today)
        if day_meals:
            total_cal = sum(m.get("calories") or 0 for m in day_meals)
            total_p = sum(m.get("protein_g") or 0 for m in day_meals)
            total_c = sum(m.get("carbs_g") or 0 for m in day_meals)
            total_f = sum(m.get("fat_g") or 0 for m in day_meals)
            parts.append(f"\n*Today's totals:* {total_cal:.0f} kcal | P:{total_p:.0f}g C:{total_c:.0f}g F:{total_f:.0f}g")

        await update.message.reply_text("\n".join(parts), parse_mode="Markdown")

    async def cmd_macros(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show today's macro tracking summary."""
        await self._typing(update)
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        day_meals = self.db.get_daily_nutrition(today)

        if not day_meals:
            await update.message.reply_text(
                "No meals logged today. Use /log to track meals.\n"
                "Example: /log lunch: chicken rice 600cal 40p 80c 15f"
            )
            return

        # Get training load for targets
        _, a, _, _ = await self._gather(days_a=1)
        today_tss = sum(
            act.get("icu_training_load", 0) or 0
            for act in a
            if (act.get("start_date_local", "") or "")[:10] == today
        )
        load = classify_training_load(today_tss)
        targets = get_targets_for_load(load, self.athlete.weight_kg)

        # Parse stored meals into format expected by get_daily_summary
        parsed_meals = []
        for m in day_meals:
            parsed_meals.append({
                "meal_type": m.get("meal_type", "unknown"),
                "description": m.get("description", ""),
                "calories": m.get("calories"),
                "protein_g": m.get("protein_g"),
                "carbs_g": m.get("carbs_g"),
                "fat_g": m.get("fat_g"),
                "has_macros": any([m.get("protein_g"), m.get("carbs_g"), m.get("fat_g")]),
            })

        summary = get_daily_summary(parsed_meals, targets)
        text = format_nutrition_tracking_context(summary, targets)
        await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")

    # ── Whoop Commands ─────────────────────────────────────────

    async def cmd_whoop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Connect Whoop or show Whoop status."""
        if not self.whoop:
            await update.message.reply_text("Whoop integration not configured.")
            return

        if self.whoop.is_authenticated:
            # Show status + recent data
            await self._typing(update)
            try:
                data = await self.whoop.all_data(3)
                text = "*Whoop Connected*\n\n"

                # Latest recovery
                if data["recovery"]:
                    latest = data["recovery"][0]
                    score = latest.get("score", {})
                    text += (
                        f"*Latest Recovery:* {score.get('recovery_score', 'N/A')}%\n"
                        f"HRV: {(score.get('hrv_rmssd_milli', 0) or 0):.1f}ms | "
                        f"RHR: {score.get('resting_heart_rate', 'N/A')} | "
                        f"SpO2: {score.get('spo2_percentage', 'N/A')}%\n\n"
                    )

                # Today's strain
                if data["cycles"]:
                    latest = data["cycles"][0]
                    cs = latest.get("score", {})
                    text += (
                        f"*Today's Strain:* {cs.get('strain', 0):.1f}/21\n"
                        f"Calories: {(cs.get('kilojoule', 0) or 0)/4.184:.0f} kcal\n\n"
                    )

                # Recent workouts
                if data["workouts"]:
                    from whoop import SPORT_MAP
                    text += "*Recent Workouts:*\n"
                    for w in data["workouts"][:5]:
                        ws = w.get("score", {})
                        sport = SPORT_MAP.get(w.get("sport_id", -1), "Activity")
                        text += f"  {sport} — Strain: {ws.get('strain', 0):.1f}\n"

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Recovery", callback_data="recovery"),
                        InlineKeyboardButton("Sleep", callback_data="sleep"),
                    ],
                ])
                await self._safe_reply(update, text, reply_markup=keyboard)
            except Exception as exc:
                log.error(f"Whoop data fetch failed: {exc}")
                await update.message.reply_text(f"Whoop connected but data fetch failed: {exc}")
        else:
            # Start OAuth flow
            auth_url = self.whoop.get_auth_url()
            await update.message.reply_text(
                "*Connect Whoop*\n\n"
                f"[Click here to authorize]({auth_url})\n\n"
                "_After authorizing, your Whoop data will be integrated into all coaching._",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            # Wait for callback in background
            asyncio.create_task(self._wait_whoop_auth(update))

    async def _wait_whoop_auth(self, update: Update):
        """Wait for Whoop OAuth callback and notify user."""
        success = await wait_for_auth_code(self.whoop, timeout=300)
        if success:
            await update.message.reply_text(
                "Whoop connected! Your recovery, strain, sleep, and workout data "
                "is now integrated into all coaching.\n\n"
                "Try /strain or /whoopsleep to see your data."
            )
        else:
            log.warning("Whoop OAuth timed out or failed")

    async def cmd_strain(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show Whoop strain and recovery dashboard."""
        if not self.whoop or not self.whoop.is_authenticated:
            await update.message.reply_text("Connect Whoop first with /whoop")
            return

        await self._typing(update)
        text = await self.engine.respond(
            "Analyze my Whoop strain and recovery data. "
            "Compare Whoop recovery scores with Intervals.icu TSB. "
            "Correlate strain with training load. "
            "What does the data tell us about my readiness?"
        )
        await self._safe_reply(update, text)

    async def cmd_whoop_sleep(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show detailed Whoop sleep analysis."""
        if not self.whoop or not self.whoop.is_authenticated:
            await update.message.reply_text("Connect Whoop first with /whoop")
            return

        await self._typing(update)
        text = await self.engine.respond(
            "Deep sleep analysis using both Whoop and Intervals.icu data. "
            "Analyze sleep stages (REM, deep, light), sleep efficiency, "
            "performance scores, and correlation with recovery. "
            "Give specific recommendations to improve sleep quality."
        )
        await self._safe_reply(update, text)

        # Send the Intervals sleep chart
        w, _, _, _ = await self._gather(days_w=14, include_whoop=False)
        chart = sleep_chart(w)
        if chart:
            await self._send_chart(update, chart, "14-Day Sleep Trend (Intervals.icu)")

    # ── Strava Commands ────────────────────────────────────────

    async def cmd_strava(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Connect Strava, show status, or sync full activity history.

        Usage
        -----
        /strava        — show connection status + recent activities
        /strava sync   — pull ALL historical activities into local DB
        """
        if not self.strava:
            await update.message.reply_text(
                "Strava integration not configured.\n"
                "Add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to config.env."
            )
            return

        args = ctx.args or []

        # ── /strava sync — full history backfill ──────────────────────────
        if args and args[0].lower() == "sync":
            if not self.strava.is_authenticated:
                await update.message.reply_text(
                    "Connect Strava first: /strava\n"
                    "Then run /strava sync to pull your full history."
                )
                return

            msg = await update.message.reply_text(
                "⏳ Syncing full Strava history — paginating all activities...\n"
                "_This may take 30–60 seconds for large histories._",
                parse_mode="Markdown",
            )
            try:
                result = await self.strava.sync_all_history()
                total = self.db.count_strava_activities()
                await msg.edit_text(
                    f"✅ *Strava History Sync Complete*\n\n"
                    f"• Activities fetched from API: {result['fetched']}\n"
                    f"• Newly stored (no duplicates): {result['new']}\n"
                    f"• Total in local DB: {total}\n"
                    f"• API pages processed: {result['pages']}\n\n"
                    f"_Your full Strava history is now available for coaching. "
                    f"Intervals.icu data still takes priority when both sources "
                    f"have the same activity (it carries TSS/training load)._",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                log.error("Strava sync failed: %s", exc)
                await msg.edit_text(f"❌ Sync failed: {exc}")
            return

        # ── /strava — status or auth link ────────────────────────────────
        if self.strava.is_authenticated:
            await self._typing(update)
            try:
                acts = await self.strava.activities(days=3)
                total_stored = self.db.count_strava_activities()
                lines = [
                    f"*Strava Connected* — {len(acts)} activities (last 3 days)\n"
                    f"_Local DB: {total_stored} activities stored_\n"
                ]
                for a in acts[:5]:
                    name = a.get("name", "Activity")
                    sport = a.get("type", "")
                    dist_km = (a.get("distance") or 0) / 1000
                    dur_min = (a.get("moving_time") or 0) // 60
                    avg_hr = a.get("average_heartrate")
                    hr_str = f" | ❤️ {avg_hr:.0f}" if avg_hr else ""
                    lines.append(
                        f"  *{sport}:* {name}\n"
                        f"  {dist_km:.1f}km · {dur_min}min{hr_str}"
                    )
                if total_stored == 0:
                    lines.append(
                        "\n_Tip: run /strava sync to pull your full Strava history._"
                    )
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            except Exception as exc:
                log.error("Strava data fetch failed: %s", exc)
                await update.message.reply_text(f"Strava connected but fetch failed: {exc}")
        else:
            await update.message.reply_text(
                "*Connect Strava*\n\n"
                "[Click here to authorize](http://localhost:3000/strava/auth)\n\n"
                "_After authorizing, Strava activities will supplement Intervals.icu — "
                "your morning run will appear in coaching even before Intervals.icu syncs._\n\n"
                "_Then run /strava sync to pull your full activity history._",
                parse_mode="Markdown",
            )

    # ── Inline Keyboard Callbacks ─────────────────────────────

    async def on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        action = query.data
        # Create a pseudo-update for reuse
        if action == "recovery":
            await self.cmd_recovery(update, ctx)
        elif action == "sleep":
            await self.cmd_sleep(update, ctx)
        elif action == "fitness":
            await self.cmd_fitness(update, ctx)
        elif action == "nutrition":
            await self.cmd_nutrition(update, ctx)
        elif action == "boston":
            await self.cmd_boston(update, ctx)
        elif action == "predict":
            await self.cmd_predict(update, ctx)
        elif action == "adapt":
            await self.cmd_adapt(update, ctx)
        elif action == "plan_today":
            await self._handle_plan_today(update, ctx)

    async def _handle_plan_today(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle 'Today's Plan' button — show planned workout + recovery status."""
        chat = update.effective_chat
        await chat.send_action(ChatAction.TYPING)

        text = await self.engine.respond(
            "What's my plan for today? Show the planned workout, adjust based on recovery, "
            "and give pre-workout nutrition advice."
        )

        # Reply to the callback message's chat
        try:
            await chat.send_message(text, parse_mode="Markdown")
        except Exception:
            await chat.send_message(text)

    # ── Voice Notes ────────────────────────────────────────────

    async def on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle voice messages — transcribe via Whisper-compatible API and coach."""
        await self._typing(update)
        voice = update.message.voice or update.message.audio
        if not voice:
            await update.message.reply_text("Could not process audio.")
            return

        try:
            # Download voice file
            file = await ctx.bot.get_file(voice.file_id)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await file.download_to_drive(tmp.name)
                tmp_path = tmp.name

            # Transcribe using Claude — send as coaching context
            duration = voice.duration or 0
            await update.message.reply_text(
                f"_Voice note received ({duration}s). Transcribing..._",
                parse_mode="Markdown",
            )

            # Use Anthropic's audio transcription or fallback to description
            transcript = await self._transcribe_voice(tmp_path)

            if not transcript:
                await update.message.reply_text(
                    "Could not transcribe audio. Try sending a text message instead."
                )
                return

            # Show transcript
            await update.message.reply_text(
                f"_Heard:_ \"{transcript}\"",
                parse_mode="Markdown",
            )

            # Process as coaching message
            text = await self.engine.respond(
                f"[Voice message from athlete]: {transcript}"
            )
            await self._safe_reply(update, text)

            # Cleanup
            import os
            os.unlink(tmp_path)

        except Exception as e:
            log.error(f"Voice processing error: {e}")
            await update.message.reply_text(
                "Could not process voice note. Try sending a text message instead."
            )

    async def _transcribe_voice(self, file_path: str) -> str | None:
        """Transcribe voice file using OpenAI Whisper API or local fallback."""
        try:
            import subprocess
            # Try local whisper if installed (pip install openai-whisper)
            result = subprocess.run(
                ["whisper", file_path, "--model", "base", "--output_format", "txt"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                # Whisper outputs a .txt file alongside the input
                txt_path = file_path.rsplit(".", 1)[0] + ".txt"
                import os
                if os.path.exists(txt_path):
                    transcript = open(txt_path).read().strip()
                    os.unlink(txt_path)
                    return transcript
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            # Fallback: use speech_recognition library
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            # Convert ogg to wav first
            import subprocess
            wav_path = file_path.rsplit(".", 1)[0] + ".wav"
            subprocess.run(
                ["ffmpeg", "-i", file_path, "-ar", "16000", "-ac", "1", wav_path, "-y"],
                capture_output=True, timeout=15,
            )
            import os
            if os.path.exists(wav_path):
                with sr.AudioFile(wav_path) as source:
                    audio = recognizer.record(source)
                transcript = recognizer.recognize_google(audio)
                os.unlink(wav_path)
                return transcript
        except Exception:
            pass

        return None

    # ── Photos / Images ────────────────────────────────────────

    async def on_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages — pass to Claude vision with optional caption."""
        await self._typing(update)
        try:
            # Telegram delivers photos at multiple sizes; take the largest
            photo = update.message.photo[-1]
            file = await ctx.bot.get_file(photo.file_id)

            buf = io.BytesIO()
            await file.download_to_memory(buf)
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            # Use caption as the question; fall back to a sensible default
            caption = (
                update.message.caption
                or "Please analyze this image. If it's a training activity or Strava "
                   "screenshot, give me coaching insights about the workout."
            )

            text = await self.engine.respond(
                caption,
                image_data={"data": img_b64, "media_type": "image/jpeg"},
            )
            await self._safe_reply(update, text)

        except Exception as e:
            log.error("Photo processing error: %s", e)
            await update.message.reply_text(
                "Could not process image. Try describing it in text instead."
            )

    # ── Free-form Messages ────────────────────────────────────

    async def on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_msg = update.message.text
        if not user_msg:
            return

        # Auto-detect meal logging from free text
        if is_meal_log(user_msg):
            update.message.text = f"/log {user_msg}"
            await self.cmd_log_meal(update, ctx)
            return

        await self._typing(update)
        text = await self.engine.respond(user_msg)
        await self._safe_reply(update, text)

    # ── Scheduled Check-ins ───────────────────────────────────

    async def run_scheduled_checkin(self, checkin_type: str, bot):
        """Run a scheduled check-in and send to the configured chat."""
        log.info(f"Running scheduled {checkin_type} check-in")
        text = await self.engine.respond(
            f"Run the {checkin_type} check-in for {self.athlete.name}.",
            checkin_type=checkin_type,
        )
        await bot.send_message(chat_id=self.chat_id, text=text, parse_mode="Markdown")
        log.info(f"{checkin_type} check-in sent")

    async def run_weekly_report(self, bot):
        """Run the automated Sunday evening weekly report."""
        log.info("Running weekly report")
        text = await self.engine.respond(
            "Generate the weekly training report with analysis and next week's preview."
        )
        await bot.send_message(chat_id=self.chat_id, text=text, parse_mode="Markdown")

        # Send charts — still need raw data for charting
        w, a, _, _ = await self._gather(days_w=14, days_a=14, days_e=7)
        charts = [
            (weekly_load_chart(a), "Weekly Training Load"),
            (sleep_chart(w), "Sleep Trend"),
            (fitness_chart(w), "Fitness & Fatigue"),
        ]
        for chart_bytes, caption in charts:
            if chart_bytes:
                try:
                    await bot.send_photo(
                        chat_id=self.chat_id,
                        photo=io.BytesIO(chart_bytes),
                        caption=caption,
                    )
                except Exception as e:
                    log.error(f"Chart send failed: {e}")

        log.info("Weekly report sent")

    # ── Utilities ─────────────────────────────────────────────

    def _get_training_phase(self) -> str:
        """Determine current training phase from race date proximity."""
        if not self.athlete.race_date:
            return "base"
        race_dt = datetime.strptime(self.athlete.race_date, "%Y-%m-%d")
        days_out = (race_dt - datetime.now()).days
        if days_out <= 0:
            return "base"
        elif days_out <= 10:
            return "taper"
        elif days_out <= 21:
            return "peak"
        elif days_out <= 42:
            return "build"
        else:
            return "base"
