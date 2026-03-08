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

    # ── Data Fetchers ──────────────────────────────────────────

    async def activities(self, days: int = 7) -> list[dict]:
        """Fetch recent activities from Strava.

        Returns a list of activity dicts with fields matching Intervals.icu schema
        where possible, so they can be merged transparently.
        """
        cache_key = f"strava_activities_{days}"
        cached = self.db.cache_get(cache_key)
        if cached:
            return json.loads(cached)

        # Unix timestamp for `days` ago
        after = int(time.time()) - (days * 86400)

        try:
            raw = await self._get("athlete/activities", params={
                "after": after,
                "per_page": 50,
            })
        except Exception as exc:
            log.warning("Strava activities fetch failed: %s", exc)
            return []

        if not isinstance(raw, list):
            log.warning("Strava activities: unexpected response type %s", type(raw))
            return []

        # Normalize to a field schema compatible with Intervals.icu activities
        activities = []
        for act in raw:
            # Strava uses sport_type (newer) or type (older)
            sport = act.get("sport_type") or act.get("type") or "Unknown"
            activities.append({
                # Core fields (match Intervals schema for deduplication)
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
                # Pace (m/s — convert to min/km if needed)
                "average_speed": act.get("average_speed"),
                # Strava-specific extras
                "strava_id": act.get("id"),
                "kudos_count": act.get("kudos_count"),
                "suffer_score": act.get("suffer_score"),
                # Source tag — tells Claude TSS is not available
                "_source": "strava",
            })

        self.db.cache_set(cache_key, json.dumps(activities), ttl_minutes=5)
        log.info("Fetched %d activities from Strava (last %d days)", len(activities), days)
        return activities

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
