"""Whoop API client with OAuth 2.0 flow, token management, and data fetching."""

from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlencode, parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import httpx

from database import Database

log = logging.getLogger("coach.whoop")

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer/v2"

SCOPES = "offline read:profile read:body_measurement read:cycles read:sleep read:recovery read:workout"

# Whoop sport ID → name mapping
SPORT_MAP = {
    -1: "Activity", 0: "Running", 1: "Cycling", 2: "Baseball",
    3: "Basketball", 4: "Rowing", 5: "Fencing", 6: "Field Hockey",
    8: "Football", 10: "Golf", 11: "Ice Hockey", 12: "Lacrosse",
    13: "Rugby", 14: "Sailing", 15: "Skiing", 16: "Soccer",
    17: "Softball", 18: "Squash", 19: "Swimming", 20: "Tennis",
    21: "Track & Field", 22: "Volleyball", 24: "Wrestling",
    25: "Boxing", 27: "Dance", 28: "Pilates", 29: "Yoga",
    30: "Weightlifting", 31: "CrossFit", 32: "Functional Fitness",
    33: "Duathlon", 34: "Gymnastics", 35: "Hiking/Rucking",
    36: "Horseback Riding", 37: "Kayaking", 38: "Martial Arts",
    39: "Mountain Biking", 42: "Spinning", 43: "Surfing",
    44: "Walking", 45: "Water Polo", 47: "Triathlon",
    48: "Snowboarding", 49: "Trapeze", 51: "Climbing",
    52: "Jumping Rope", 56: "Barre", 57: "High Intensity Interval Training",
    58: "Circus", 59: "Massage/Bodywork", 62: "Meditation",
    63: "Other", 64: "Diving", 65: "Operations – Loss of Limb",
    66: "Motor Racing", 70: "Obstacle Course Racing", 71: "Strength Trainer",
    73: "Assault Bike", 74: "Kickboxing", 75: "Stretching",
    76: "Skateboarding", 82: "Training", 83: "Pickleball",
    84: "Padel", 85: "Badminton", 86: "Table Tennis",
    87: "Disc Golf", 88: "Roller Skating",
    123: "Strength (MSK)",
}


class WhoopClient:
    """Whoop API client with OAuth 2.0 and automatic token refresh."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, db: Database):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.db = db
        self._access_token = None
        self._refresh_token = None
        self._token_expiry = None
        self._auth_event = asyncio.Event()
        self._load_tokens()

    @property
    def is_authenticated(self) -> bool:
        return self._access_token is not None

    def get_auth_url(self) -> str:
        """Generate the OAuth authorization URL."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": "coach_whoop_auth",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> bool:
        """Exchange authorization code for access + refresh tokens."""
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(TOKEN_URL, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            if r.status_code == 200:
                self._save_tokens(r.json())
                log.info("Whoop OAuth tokens obtained successfully")
                return True
            log.error(f"Whoop token exchange failed: {r.status_code} {r.text}")
            return False

    async def _refresh(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self._refresh_token:
            return False
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(TOKEN_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "offline",
            })
            if r.status_code == 200:
                self._save_tokens(r.json())
                log.info("Whoop token refreshed")
                return True
            log.error(f"Whoop token refresh failed: {r.status_code}")
            self._access_token = None
            return False

    def _save_tokens(self, data: dict):
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        expires_in = data.get("expires_in", 3600)
        self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

        self.db.set_state("whoop_access_token", self._access_token)
        if self._refresh_token:
            self.db.set_state("whoop_refresh_token", self._refresh_token)
        self.db.set_state("whoop_token_expiry", self._token_expiry.isoformat())

    def _load_tokens(self):
        self._access_token = self.db.get_state("whoop_access_token")
        self._refresh_token = self.db.get_state("whoop_refresh_token")
        expiry = self.db.get_state("whoop_token_expiry")
        if expiry:
            self._token_expiry = datetime.fromisoformat(expiry)
        if self._access_token:
            log.info("Whoop tokens loaded from database")

    async def _ensure_token(self):
        if not self._access_token:
            raise ValueError("Not authenticated with Whoop. Use /whoop to connect.")
        if self._token_expiry and datetime.now() >= self._token_expiry:
            if not await self._refresh():
                raise ValueError("Whoop token expired. Re-authenticate with /whoop.")

    async def _get(self, path: str, params: dict = None) -> dict:
        """Make authenticated GET request to Whoop API."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{API_BASE}/{path}",
                headers={"Authorization": f"Bearer {self._access_token}"},
                params=params,
            )
            if r.status_code == 401:
                # Try refresh once
                if await self._refresh():
                    r = await c.get(
                        f"{API_BASE}/{path}",
                        headers={"Authorization": f"Bearer {self._access_token}"},
                        params=params,
                    )
                else:
                    raise ValueError("Whoop authentication failed. Use /whoop to reconnect.")
            r.raise_for_status()
            return r.json()

    async def _paged_get(self, path: str, limit: int = 25, max_records: int = 100) -> list:
        """Fetch paginated endpoint, collecting all records."""
        records = []
        next_token = None
        while len(records) < max_records:
            params = {"limit": limit}
            if next_token:
                params["nextToken"] = next_token
            data = await self._get(path, params)
            batch = data.get("records", [])
            records.extend(batch)
            next_token = data.get("next_token")
            if not next_token or not batch:
                break
        return records[:max_records]

    # ── Data Fetchers ──────────────────────────────────────

    async def recovery(self, days: int = 7) -> list:
        """Fetch recovery records."""
        cache_key = f"whoop_recovery_{days}"
        cached = self.db.cache_get(cache_key)
        if cached:
            return json.loads(cached)

        records = await self._paged_get("recovery", max_records=days)
        self.db.cache_set(cache_key, json.dumps(records), ttl_minutes=15)
        return records

    async def sleep(self, days: int = 7) -> list:
        """Fetch sleep records."""
        cache_key = f"whoop_sleep_{days}"
        cached = self.db.cache_get(cache_key)
        if cached:
            return json.loads(cached)

        records = await self._paged_get("activity/sleep", max_records=days)
        self.db.cache_set(cache_key, json.dumps(records), ttl_minutes=15)
        return records

    async def workouts(self, days: int = 7) -> list:
        """Fetch workout records."""
        cache_key = f"whoop_workouts_{days}"
        cached = self.db.cache_get(cache_key)
        if cached:
            return json.loads(cached)

        records = await self._paged_get("activity/workout", max_records=days * 3)
        self.db.cache_set(cache_key, json.dumps(records), ttl_minutes=15)
        return records

    async def cycles(self, days: int = 7) -> list:
        """Fetch physiological cycle records."""
        cache_key = f"whoop_cycles_{days}"
        cached = self.db.cache_get(cache_key)
        if cached:
            return json.loads(cached)

        records = await self._paged_get("cycle", max_records=days)
        self.db.cache_set(cache_key, json.dumps(records), ttl_minutes=15)
        return records

    async def profile(self) -> dict:
        """Fetch user profile."""
        return await self._get("user/profile/basic")

    async def body(self) -> dict:
        """Fetch body measurements."""
        return await self._get("user/measurement/body")

    async def all_data(self, days: int = 7) -> dict:
        """Fetch all data types in parallel."""
        recovery, sleep, workouts, cycles = await asyncio.gather(
            self.recovery(days),
            self.sleep(days),
            self.workouts(days),
            self.cycles(days),
            return_exceptions=True,
        )
        return {
            "recovery": recovery if not isinstance(recovery, Exception) else [],
            "sleep": sleep if not isinstance(sleep, Exception) else [],
            "workouts": workouts if not isinstance(workouts, Exception) else [],
            "cycles": cycles if not isinstance(cycles, Exception) else [],
        }

    # ── Formatters ─────────────────────────────────────────

    @staticmethod
    def fmt_recovery(records: list) -> str:
        if not records:
            return "WHOOP RECOVERY: No data"
        lines = ["WHOOP RECOVERY:"]
        for r in records:
            score = r.get("score", {})
            if not score:
                continue
            hrv = score.get("hrv_rmssd_milli", 0) or 0  # already in ms
            rhr = score.get("resting_heart_rate", 0) or 0
            spo2 = score.get("spo2_percentage", 0) or 0
            skin = score.get("skin_temp_celsius", 0) or 0
            lines.append(
                f"  Recovery: {score.get('recovery_score', 'N/A')}% | "
                f"HRV: {hrv:.1f}ms | "
                f"RHR: {rhr:.0f} | "
                f"SpO2: {spo2:.1f}% | "
                f"Skin Temp: {skin:.1f}°C"
            )
        return "\n".join(lines)

    @staticmethod
    def fmt_sleep(records: list) -> str:
        if not records:
            return "WHOOP SLEEP: No data"
        lines = ["WHOOP SLEEP:"]
        for s in records:
            score = s.get("score", {})
            if not score:
                continue
            stage = score.get("stage_summary", {})
            total_ms = stage.get("total_in_bed_time_milli", 0) or 0
            rem_ms = stage.get("total_rem_sleep_time_milli", 0) or 0
            deep_ms = stage.get("total_slow_wave_sleep_time_milli", 0) or 0
            light_ms = stage.get("total_light_sleep_time_milli", 0) or 0
            awake_ms = stage.get("total_awake_time_milli", 0) or 0
            sleep_ms = total_ms - awake_ms  # actual sleep time
            resp_rate = score.get("respiratory_rate", 0)

            # Sleep need breakdown
            need = score.get("sleep_needed", {})
            need_total = sum([
                need.get("baseline_milli", 0) or 0,
                need.get("need_from_sleep_debt_milli", 0) or 0,
                need.get("need_from_recent_strain_milli", 0) or 0,
            ]) - (need.get("need_from_recent_nap_milli", 0) or 0)
            debt_ms = need.get("need_from_sleep_debt_milli", 0) or 0

            start = s.get("start", "")[:10]
            lines.append(
                f"  {start} | Perf: {score.get('sleep_performance_percentage', 'N/A')}% | "
                f"InBed: {total_ms/3600000:.1f}h | Sleep: {sleep_ms/3600000:.1f}h | "
                f"REM: {rem_ms/3600000:.1f}h | Deep: {deep_ms/3600000:.1f}h | "
                f"Light: {light_ms/3600000:.1f}h | Awake: {awake_ms/60000:.0f}min | "
                f"Efficiency: {score.get('sleep_efficiency_percentage', 'N/A'):.0f}% | "
                f"RespRate: {resp_rate:.1f} | "
                f"Need: {need_total/3600000:.1f}h (debt: {debt_ms/3600000:.1f}h)"
            )

        # Summary stats
        perfs = [s["score"]["sleep_performance_percentage"]
                 for s in records if s.get("score", {}).get("sleep_performance_percentage")]
        if perfs:
            lines.append(f"  Avg Performance: {sum(perfs)/len(perfs):.0f}%")
        return "\n".join(lines)

    @staticmethod
    def fmt_workouts(records: list) -> str:
        if not records:
            return "WHOOP WORKOUTS: No data"
        lines = ["WHOOP WORKOUTS:"]
        for w in records:
            score = w.get("score", {})
            if not score:
                continue
            sport_id = w.get("sport_id", -1)
            sport = SPORT_MAP.get(sport_id) or w.get("sport_name", f"Sport({sport_id})").replace("_", " ").title()
            start = w.get("start", "")[:16].replace("T", " ")
            strain = score.get("strain", 0)
            avg_hr = score.get("average_heart_rate", "N/A")
            max_hr = score.get("max_heart_rate", "N/A")
            cal = (score.get("kilojoule", 0) or 0) / 4.184
            zones = score.get("zone_duration", {})
            zone_str = _format_zones(zones)
            lines.append(
                f"  {start} | {sport} | Strain: {strain:.1f} | "
                f"AvgHR: {avg_hr} MaxHR: {max_hr} | "
                f"Cal: {cal:.0f} | {zone_str}"
            )
        return "\n".join(lines)

    @staticmethod
    def fmt_cycles(records: list) -> str:
        if not records:
            return "WHOOP DAILY STRAIN: No data"
        lines = ["WHOOP DAILY STRAIN:"]
        for c in records:
            score = c.get("score", {})
            if not score:
                continue
            day = c.get("start", "")[:10]
            strain = score.get("strain", 0)
            cal = (score.get("kilojoule", 0) or 0) / 4.184
            avg_hr = score.get("average_heart_rate", "N/A")
            max_hr = score.get("max_heart_rate", "N/A")
            lines.append(
                f"  {day} | Strain: {strain:.1f} | "
                f"Cal: {cal:.0f} kcal | "
                f"AvgHR: {avg_hr} MaxHR: {max_hr}"
            )
        return "\n".join(lines)

    @staticmethod
    def fmt_all(data: dict) -> str:
        """Format all Whoop data into a context string."""
        parts = []
        if data.get("recovery"):
            parts.append(WhoopClient.fmt_recovery(data["recovery"]))
        if data.get("sleep"):
            parts.append(WhoopClient.fmt_sleep(data["sleep"]))
        if data.get("workouts"):
            parts.append(WhoopClient.fmt_workouts(data["workouts"]))
        if data.get("cycles"):
            parts.append(WhoopClient.fmt_cycles(data["cycles"]))
        return "\n\n".join(parts) if parts else "WHOOP: No data available"


def _format_zones(zones: dict) -> str:
    """Format HR zone durations."""
    if not zones:
        return ""
    parts = []
    for key in sorted(zones.keys()):
        mins = (zones[key] or 0) / 60000
        if mins > 0:
            parts.append(f"Z{key}:{mins:.0f}m")
    return " ".join(parts)


# ── OAuth Callback Server ──────────────────────────────────


class _OAuthHandler(BaseHTTPRequestHandler):
    """Handle the OAuth callback from Whoop."""

    whoop_client = None  # Set by start_oauth_server
    auth_result = None

    def do_GET(self):
        parsed = urlparse(self.path)

        # Serve privacy policy
        if parsed.path == "/privacy" or parsed.path == "/privacy_policy.html":
            self._serve_privacy_policy()
            return

        # Handle OAuth callback
        if parsed.path == "/whoop/callback":
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]

            if error:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h1>Authorization Failed</h1><p>Error: {error}</p>".encode())
                _OAuthHandler.auth_result = {"error": error}
                return

            if code:
                _OAuthHandler.auth_result = {"code": code}
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Whoop Connected!</h1>"
                    b"<p>You can close this window and return to Telegram.</p>"
                    b"<script>setTimeout(()=>window.close(), 3000)</script>"
                )
                return

        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not found")

    def _serve_privacy_policy(self):
        from config import BASE_DIR
        pp_path = BASE_DIR / "privacy_policy.html"
        if pp_path.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(pp_path.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        log.debug(f"OAuth server: {format % args}")


def start_oauth_server(whoop_client: WhoopClient, port: int = 8765):
    """Start the OAuth callback server in a background thread."""
    _OAuthHandler.whoop_client = whoop_client
    _OAuthHandler.auth_result = None

    server = HTTPServer(("0.0.0.0", port), _OAuthHandler)
    server.timeout = 1

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"OAuth callback server started on port {port}")
    return server


async def wait_for_auth_code(whoop_client: WhoopClient, timeout: int = 300) -> bool:
    """Wait for the OAuth callback and exchange the code for tokens."""
    start = datetime.now()
    while (datetime.now() - start).seconds < timeout:
        if _OAuthHandler.auth_result:
            result = _OAuthHandler.auth_result
            _OAuthHandler.auth_result = None
            if "code" in result:
                return await whoop_client.exchange_code(result["code"])
            return False
        await asyncio.sleep(1)
    return False
