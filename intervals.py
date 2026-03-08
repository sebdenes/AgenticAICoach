"""Intervals.icu API client with smart caching and data persistence."""

import json
import logging
from datetime import datetime, timedelta

import httpx

from database import Database

log = logging.getLogger("coach.intervals")


class IntervalsClient:
    def __init__(self, api_key: str, athlete_id: str, db: Database):
        self.auth = httpx.BasicAuth("API_KEY", api_key)
        self.athlete_id = athlete_id
        self.base = f"https://intervals.icu/api/v1/athlete/{athlete_id}"
        self.db = db

    async def _get(self, path: str, params: dict = None):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self.base}/{path}", auth=self.auth, params=params)
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, data: dict):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{self.base}/{path}", auth=self.auth, json=data)
            r.raise_for_status()
            return r.json()

    async def _put(self, path: str, data: dict):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.put(f"{self.base}/{path}", auth=self.auth, json=data)
            r.raise_for_status()
            return r.json()

    async def _delete(self, path: str):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.delete(f"{self.base}/{path}", auth=self.auth)
            r.raise_for_status()
            return r.status_code

    async def create_event(self, event: dict):
        """Create a calendar event on Intervals.icu."""
        return await self._post("events", event)

    async def update_event(self, event_id: int, updates: dict):
        """Update an existing calendar event on Intervals.icu (partial update)."""
        return await self._put(f"events/{event_id}", updates)

    async def delete_event(self, event_id: int):
        """Delete a calendar event from Intervals.icu."""
        return await self._delete(f"events/{event_id}")

    async def events_range(self, oldest: str, newest: str) -> list:
        """Fetch all events in a date range."""
        return await self._get("events", {"oldest": oldest, "newest": newest})

    # ── Fetchers with caching + DB storage ────────────────

    async def wellness(self, days: int = 14, force: bool = False) -> list:
        cache_key = f"wellness_{days}"
        if not force:
            cached = self.db.cache_get(cache_key)
            if cached:
                return json.loads(cached)

        oldest = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        newest = datetime.now().strftime("%Y-%m-%d")
        data = await self._get("wellness", {"oldest": oldest, "newest": newest})

        # Store in DB for long-term history
        for d in data:
            self.db.store_wellness(d["id"], d)

        # Cache for short-term reuse
        self.db.cache_set(cache_key, json.dumps(data), ttl_minutes=10)
        return data

    async def activities(self, days: int = 7, force: bool = False) -> list:
        cache_key = f"activities_{days}"
        if not force:
            cached = self.db.cache_get(cache_key)
            if cached:
                return json.loads(cached)

        oldest = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        newest = datetime.now().strftime("%Y-%m-%d")
        data = await self._get("activities", {"oldest": oldest, "newest": newest})

        for a in data:
            if a.get("type"):
                self.db.store_activity(
                    str(a.get("id", "")),
                    a.get("start_date_local", "")[:10],
                    a,
                )

        self.db.cache_set(cache_key, json.dumps(data), ttl_minutes=10)
        return data

    async def events(self, days_ahead: int = 3, force: bool = False) -> list:
        cache_key = f"events_{days_ahead}"
        if not force:
            cached = self.db.cache_get(cache_key)
            if cached:
                return json.loads(cached)

        oldest = datetime.now().strftime("%Y-%m-%d")
        newest = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        data = await self._get("events", {"oldest": oldest, "newest": newest})

        self.db.cache_set(cache_key, json.dumps(data), ttl_minutes=30)
        return data

    # ── Formatters ────────────────────────────────────────

    @staticmethod
    def fmt_wellness(data: list) -> str:
        lines = []
        sleep_hours, hrvs, rhrs = [], [], []
        for d in data:
            sleep_s = d.get("sleepSecs", 0) or 0
            sleep_h = sleep_s / 3600
            hrv = d.get("hrv")
            rhr = d.get("restingHR")
            ctl = d.get("ctl", 0)
            atl = d.get("atl", 0)
            tsb = ctl - atl
            if sleep_h > 0:
                sleep_hours.append(sleep_h)
            if hrv:
                hrvs.append(hrv)
            if rhr:
                rhrs.append(rhr)
            lines.append(
                f"{d['id']} | CTL:{ctl:.1f} ATL:{atl:.1f} TSB:{tsb:.1f} | "
                f"RHR:{rhr or 'N/A'} HRV:{f'{hrv:.1f}' if hrv else 'N/A'} | "
                f"Sleep:{f'{sleep_h:.1f}h' if sleep_h else 'N/A'} Score:{d.get('sleepScore', 'N/A')}"
            )
        summary = []
        if sleep_hours:
            avg = sum(sleep_hours) / len(sleep_hours)
            debt = sum(max(0, 7.5 - h) for h in sleep_hours[-7:])
            summary.append(f"Sleep avg: {avg:.1f}h | 7d debt: {debt:.1f}h")
        if hrvs:
            summary.append(f"HRV avg: {sum(hrvs)/len(hrvs):.1f} | latest: {hrvs[-1]:.1f}")
        if rhrs:
            summary.append(f"RHR avg: {sum(rhrs)/len(rhrs):.0f} | latest: {rhrs[-1]}")
        return "WELLNESS:\n" + "\n".join(lines) + "\n\nSUMMARY:\n" + "\n".join(summary)

    @staticmethod
    def fmt_activities(data: list) -> str:
        lines = []
        total_tss = 0
        for a in data:
            if not a.get("type"):
                continue
            dur = (a.get("moving_time", 0) or 0) // 60
            tss = a.get("icu_training_load", 0) or 0
            total_tss += tss
            dist = (a.get("distance", 0) or 0) / 1000
            lines.append(
                f"{a.get('start_date_local','')[:10]} | {a['type']} | "
                f"{a.get('name','')[:50]} | {dur}min | {dist:.1f}km | "
                f"TSS:{tss} | IF:{a.get('icu_intensity', 0) or 0:.0f}% | "
                f"AvgHR:{a.get('average_heartrate', 'N/A')}"
            )
        return f"ACTIVITIES:\n" + ("\n".join(lines) if lines else "No activities.") + f"\nTotal TSS: {total_tss:.0f}"

    @staticmethod
    def fmt_events(data: list) -> str:
        lines = []
        for e in data:
            date = e.get("start_date_local", e.get("date", ""))[:10]
            desc = (e.get("description", "") or "")[:200]
            lines.append(f"{date} | {e.get('category','')} | {e.get('name','')}")
            if desc:
                lines.append(f"  {desc}")
        return "PLANNED:\n" + ("\n".join(lines) if lines else "No planned sessions.")
