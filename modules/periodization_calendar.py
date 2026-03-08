"""Periodization calendar -- push/sync TrainingPlan sessions to Intervals.icu."""

from __future__ import annotations

import json
import logging
from datetime import datetime

log = logging.getLogger("coach.periodization_calendar")


class PeriodizationCalendar:
    """Push/sync TrainingPlan sessions to Intervals.icu calendar.

    Uses the IntervalsClient to create planned events and compare them
    with completed activities for compliance tracking.
    """

    def __init__(self, intervals_client):
        """Initialize with an IntervalsClient instance.

        Parameters
        ----------
        intervals_client : intervals.IntervalsClient
            Authenticated client for the Intervals.icu API.
        """
        self._client = intervals_client

    async def push_week(self, microcycle) -> list[dict]:
        """Push a single week's sessions to Intervals.icu as planned events.

        Uses IntervalsClient._post() for auth and URL construction.
        """
        results = []
        for session in microcycle.sessions:
            payload = self._session_to_event(session)
            if session.session_type == "rest":
                payload["category"] = "NOTE"
            try:
                result = await self._client._post("events", payload)
                results.append(result)
                log.info("Created event: %s on %s", session.name, session.date)
            except Exception as exc:
                log.error("Failed to create event %s on %s: %s", session.name, session.date, exc)
                results.append({"error": str(exc), "session": session.name, "date": session.date})
        return results

    async def clear_planned_events(self, oldest: str, newest: str) -> int:
        """Delete existing WORKOUT/NOTE events in a date range before pushing a new plan.

        Prevents calendar bloat from repeated /plan or /replan calls.
        """
        try:
            existing = await self._client.events_range(oldest, newest)
        except Exception as exc:
            log.warning("Failed to fetch existing events for cleanup: %s", exc)
            return 0

        deleted = 0
        for event in existing:
            cat = event.get("category", "")
            if cat in ("WORKOUT", "NOTE"):
                try:
                    await self._client.delete_event(event["id"])
                    deleted += 1
                except Exception as exc:
                    log.warning("Failed to delete event %s: %s", event.get("id"), exc)
        log.info("Cleared %d existing planned events (%s to %s)", deleted, oldest, newest)
        return deleted

    async def push_plan(self, plan) -> dict:
        """Push an entire training plan to Intervals.icu.

        Clears existing WORKOUT/NOTE events in the plan's date range first,
        then creates new events. Returns summary dict with created/errors/deleted counts.
        """
        # Determine date range from plan
        all_dates = [
            s.date
            for meso in plan.mesocycles
            for mc in meso.microcycles
            for s in mc.sessions
            if s.date
        ]
        deleted = 0
        if all_dates:
            deleted = await self.clear_planned_events(min(all_dates), max(all_dates))

        created, errors = 0, 0
        for meso in plan.mesocycles:
            for mc in meso.microcycles:
                results = await self.push_week(mc)
                for r in results:
                    if "error" in r:
                        errors += 1
                    else:
                        created += 1
        log.info("Plan push complete: %d created, %d errors, %d deleted", created, errors, deleted)
        return {"created": created, "errors": errors, "deleted": deleted, "total": created + errors}

    async def sync_completion(self, plan, activities: list[dict]) -> dict:
        """Compare planned sessions with completed activities.

        Matches by date and sport type to determine compliance.

        Parameters
        ----------
        plan : TrainingPlan
            The training plan to check against.
        activities : list[dict]
            Intervals.icu activity data (list of activity dicts).

        Returns
        -------
        dict
            Summary with keys: completed, missed, extra, details.
        """
        # Index activities by date + sport
        activity_index: dict[str, list[dict]] = {}
        for act in activities:
            date = (act.get("start_date_local", act.get("date", "")) or "")[:10]
            if date:
                activity_index.setdefault(date, []).append(act)

        completed = 0
        missed = 0
        extra = 0
        details: list[dict] = []
        matched_dates: set[str] = set()

        today = datetime.now().strftime("%Y-%m-%d")

        for meso in plan.mesocycles:
            for mc in meso.microcycles:
                for session in mc.sessions:
                    if session.date > today:
                        # Future session -- skip
                        continue
                    if session.session_type == "rest":
                        continue

                    date_activities = activity_index.get(session.date, [])
                    sport_lower = (session.sport or "").lower()

                    # Find a matching activity by sport type
                    match = None
                    for act in date_activities:
                        act_type = (act.get("type", "") or "").lower()
                        if sport_lower in act_type or act_type in sport_lower:
                            match = act
                            break
                        # Fuzzy: "Run" matches "Run", "Ride" matches "Ride"
                        if sport_lower[:3] == act_type[:3] and len(act_type) >= 3:
                            match = act
                            break

                    if match:
                        completed += 1
                        matched_dates.add(session.date)
                        details.append({
                            "date": session.date,
                            "planned": session.name,
                            "actual": match.get("name", "Unknown"),
                            "status": "completed",
                            "planned_tss": session.target_tss,
                            "actual_tss": match.get("icu_training_load", 0) or 0,
                        })
                    else:
                        missed += 1
                        details.append({
                            "date": session.date,
                            "planned": session.name,
                            "actual": None,
                            "status": "missed",
                            "planned_tss": session.target_tss,
                            "actual_tss": 0,
                        })

        # Count extra activities not in the plan
        for date, acts in activity_index.items():
            if date > today:
                continue
            for act in acts:
                if date not in matched_dates:
                    extra += 1

        return {
            "completed": completed,
            "missed": missed,
            "extra": extra,
            "details": details,
        }

    def _session_to_event(self, session) -> dict:
        """Convert a TrainingSession to an Intervals.icu event payload.

        Follows the pattern from push_boston_plan.py.

        Parameters
        ----------
        session : TrainingSession
            The session to convert.

        Returns
        -------
        dict
            Event payload for the Intervals.icu API.
        """
        category = "WORKOUT"
        if session.session_type == "rest":
            category = "NOTE"

        payload = {
            "category": category,
            "start_date_local": f"{session.date}T09:00:00",
            "name": session.name,
            "type": session.sport,
            "description": session.description,
        }

        if session.duration_minutes and session.duration_minutes > 0:
            payload["moving_time"] = session.duration_minutes * 60

        if session.target_tss and session.target_tss > 0:
            payload["icu_training_load"] = session.target_tss

        return payload
