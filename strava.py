"""Strava API client with OAuth 2.0 flow, token management, and activity fetching."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from database import Database

log = logging.getLogger("coach.strava")

AUTH_URL  = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE  = "https://www.strava.com/api/v3"

SCOPES = "activity:read_all"


class StravaClient:
    """Strava API client with OAuth 2.0 and automatic token refresh.

    Tokens expire every 6 hours — _ensure_token() auto-refreshes transparently.
    On first run the user must authorize via /strava → dashboard OAuth flow.

    Activity data strategy
    ----------------------
    - ``activities(days)``     — DB-first for history; live API for last 2 days
    - ``sync_all_history()``   — paginate ALL activities → INSERT OR IGNORE in DB
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        db: Database,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.db = db
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: datetime | None = None
        self._load_tokens()

    # ── Auth status ────────────────────────────────────────────

    @property
    def is_authenticated(self) -> bool:
        # Reload from DB if not yet in memory — catches the case where the
        # dashboard completed OAuth after the bot process started.
        if self._refresh_token is None:
            self._load_tokens()
        return self._refresh_token is not None

    def get_auth_url(self) -> str:
        """Generate the Strava OAuth authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": SCOPES,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    # ── Token management ───────────────────────────────────────

    async def exchange_code(self, code: str) -> bool:
        """Exchange OAuth authorization code for access + refresh tokens."""
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
            })
            if r.status_code == 200:
                self._save_tokens(r.json())
                log.info("Strava OAuth tokens obtained successfully")
                return True
            log.error("Strava token exchange failed: %s %s", r.status_code, r.text)
            return False

    async def _refresh(self) -> bool:
        """Refresh the access token using the stored refresh token."""
        if not self._refresh_token:
            return False
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            })
            if r.status_code == 200:
                self._save_tokens(r.json())
                log.info("Strava token refreshed")
                return True
            log.error("Strava token refresh failed: %s", r.status_code)
            self._access_token = None
            return False

    def _save_tokens(self, data: dict):
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        # Strava returns `expires_at` (Unix timestamp) or `expires_in` (seconds)
        if "expires_at" in data:
            self._token_expiry = datetime.fromtimestamp(data["expires_at"])
        elif "expires_in" in data:
            self._token_expiry = datetime.now() + timedelta(seconds=data["expires_in"] - 60)

        self.db.set_state("strava_access_token", self._access_token)
        if self._refresh_token:
            self.db.set_state("strava_refresh_token", self._refresh_token)
        if self._token_expiry:
            self.db.set_state("strava_token_expiry", self._token_expiry.isoformat())

    def _load_tokens(self):
        self._access_token = self.db.get_state("strava_access_token")
        self._refresh_token = self.db.get_state("strava_refresh_token")
        expiry = self.db.get_state("strava_token_expiry")
        if expiry:
            try:
                self._token_expiry = datetime.fromisoformat(expiry)
            except ValueError:
                pass
        if self._refresh_token:
            log.info("Strava tokens loaded from database")

    async def _ensure_token(self):
        """Ensure a valid access token, refreshing if expired."""
        if not self._refresh_token:
            raise ValueError("Not authenticated with Strava. Use /strava to connect.")
        if not self._access_token or (
            self._token_expiry and datetime.now() >= self._token_expiry
        ):
            if not await self._refresh():
                raise ValueError("Strava token expired. Re-authenticate with /strava.")

    # ── HTTP helpers ───────────────────────────────────────────

    async def _get(self, path: str, params: dict = None) -> list | dict:
        """Authenticated GET request to Strava API."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{API_BASE}/{path}",
                headers={"Authorization": f"Bearer {self._access_token}"},
                params=params or {},
            )
            if r.status_code == 401:
                if await self._refresh():
                    r = await c.get(
                        f"{API_BASE}/{path}",
                        headers={"Authorization": f"Bearer {self._access_token}"},
                        params=params or {},
                    )
                else:
                    raise ValueError("Strava auth failed. Use /strava to reconnect.")
            r.raise_for_status()
            return r.json()

    # ── Normalization ──────────────────────────────────────────

    def _normalize_activity(self, act: dict) -> dict:
        """Normalize a raw Strava API activity dict to our internal schema."""
        sport = act.get("sport_type") or act.get("type") or "Unknown"
        return {
            # Core fields — match Intervals.icu schema for deduplication
            "type": sport,
            "name": act.get("name", ""),
            "start_date_local": act.get("start_date_local", ""),
            # Duration / distance
            "moving_time": act.get("moving_time"),
            "elapsed_time": act.get("elapsed_time"),
            "distance": act.get("distance"),
            # Heart rate
            "average_heartrate": act.get("average_heartrate"),
            "max_heartrate": act.get("max_heartrate"),
            # Power (cycling)
            "average_watts": act.get("average_watts"),
            "weighted_average_watts": act.get("weighted_average_watts"),
            # Elevation
            "total_elevation_gain": act.get("total_elevation_gain"),
            # Pace (m/s)
            "average_speed": act.get("average_speed"),
            # Strava-specific
            "strava_id": act.get("id"),
            "kudos_count": act.get("kudos_count"),
            "suffer_score": act.get("suffer_score"),
            # Source tag — tells Claude that TSS/training load is unavailable
            "_source": "strava",
        }

    # ── Data Fetchers ──────────────────────────────────────────

    async def activities(self, days: int = 7) -> list[dict]:
        """Fetch activities from Strava, combining local DB history + live API.

        Strategy
        --------
        1. Always query live API for the last 2 days (real-time gap filling —
           catches activities uploaded before Intervals.icu syncs them).
           Results are cached 5 min and stored in DB as a side effect.
        2. Query local DB for the full requested window (populated by sync_all_history).
        3. If DB has data → merge DB + live (dedup by strava_id) and return.
        4. If DB is empty (no sync yet) → fall back to full live API call.

        Parameters
        ----------
        days : int
            How many days back to retrieve.  Range 1–3650.
        """
        days = max(1, min(days, 3650))

        # --- Step 1: live API for the last 2 days (real-time freshness) ---
        live_days = min(days, 2)
        cache_key = "strava_activities_live"
        cached = self.db.cache_get(cache_key)
        if cached:
            live_acts = json.loads(cached)
        else:
            after = int(time.time()) - (live_days * 86400)
            try:
                raw = await self._get("athlete/activities", params={
                    "after": after,
                    "per_page": 50,
                })
                live_acts = [
                    self._normalize_activity(a)
                    for a in (raw if isinstance(raw, list) else [])
                ]
                self.db.cache_set(cache_key, json.dumps(live_acts), ttl_minutes=5)
                # Store new activities in DB as a side effect
                for act in live_acts:
                    self.db.store_strava_activity(act)
                log.debug(
                    "Strava live fetch: %d activities (last %d days)",
                    len(live_acts), live_days,
                )
            except Exception as exc:
                log.warning("Strava live activities fetch failed: %s", exc)
                live_acts = []

        # --- Step 2: DB history for the full window ---
        db_acts = self.db.get_strava_activities(days=days)

        if db_acts:
            # Merge: DB as primary, append live activities not already stored
            db_ids = {a.get("strava_id") for a in db_acts}
            for la in live_acts:
                if la.get("strava_id") not in db_ids:
                    db_acts.append(la)
            log.info(
                "Strava activities: %d from DB + live merge (last %d days)",
                len(db_acts), days,
            )
            return db_acts

        # --- Step 4: DB empty — fall back to full live API call ---
        if days <= live_days:
            return live_acts  # already fetched above

        after = int(time.time()) - (days * 86400)
        try:
            raw = await self._get("athlete/activities", params={
                "after": after,
                "per_page": 50,
            })
            acts = [
                self._normalize_activity(a)
                for a in (raw if isinstance(raw, list) else [])
            ]
            log.info(
                "Strava activities: %d from live API (last %d days, no DB history)",
                len(acts), days,
            )
            return acts
        except Exception as exc:
            log.warning("Strava full-window fetch failed: %s", exc)
            return live_acts

    async def sync_all_history(self) -> dict:
        """Paginate through ALL Strava activities and store in local DB.

        Uses INSERT OR IGNORE on strava_id — completely safe to re-run.
        No activities are duplicated; only genuinely new ones are counted.

        Returns
        -------
        dict
            {"fetched": total_fetched, "new": total_new, "pages": pages}
        """
        await self._ensure_token()

        total_fetched = 0
        total_new = 0
        page = 1

        while True:
            try:
                raw = await self._get("athlete/activities", params={
                    "per_page": 50,
                    "page": page,
                })
            except Exception as exc:
                log.warning("Strava history sync page %d failed: %s", page, exc)
                break

            if not isinstance(raw, list) or not raw:
                break  # no more pages

            total_fetched += len(raw)
            for act in raw:
                normalized = self._normalize_activity(act)
                is_new = self.db.store_strava_activity(normalized)
                if is_new:
                    total_new += 1

            log.info(
                "Strava sync page %d: %d activities (%d new so far)",
                page, len(raw), total_new,
            )

            if len(raw) < 50:
                break  # last page (partial)

            page += 1

        log.info(
            "Strava history sync complete: %d fetched, %d new (%d pages)",
            total_fetched, total_new, page,
        )
        return {"fetched": total_fetched, "new": total_new, "pages": page}

    async def athlete(self) -> dict:
        """Fetch the authenticated athlete's profile."""
        return await self._get("athlete")

    async def is_connected(self) -> bool:
        """Check whether Strava is connected and tokens are valid."""
        if not self.is_authenticated:
            return False
        try:
            await self._ensure_token()
            return True
        except Exception:
            return False
