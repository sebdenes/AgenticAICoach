#!/usr/bin/env python3
"""
Agentic coaching engine — lean system prompt + tool-use loop.
Claude fetches data via tools instead of receiving pre-formatted context dumps.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path

import anthropic

from config import AthleteConfig, TZ
from database import Database
from engine_tools import CoachTools, TOOL_SCHEMAS

log = logging.getLogger("coach.engine")

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_TOOL_ITERATIONS = 10
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2000


class CoachingEngine:
    def __init__(
        self,
        api_key: str,
        athlete: AthleteConfig,
        db: Database,
        iv=None,
        whoop=None,
        weather_provider=None,
        weather_engine=None,
        rag=None,
        simulator=None,
        strava=None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.athlete = athlete
        self.db = db
        self.tools = CoachTools(
            iv=iv,
            db=db,
            athlete=athlete,
            whoop=whoop,
            weather_provider=weather_provider,
            weather_engine=weather_engine,
            rag=rag,
            simulator=simulator,
            strava=strava,
        )
        self._system = self._build_system()

    def _build_system(self) -> str:
        """Build the lean system prompt — coaching philosophy + athlete profile only.
        No pre-fetched data. Claude uses tools to get what it needs."""
        parts = []

        # 1. Base coaching philosophy
        coaching_prompt_path = PROMPTS_DIR / "coaching_system.md"
        if coaching_prompt_path.exists():
            parts.append(coaching_prompt_path.read_text().strip())

        # 2. Athlete profile (from AthleteConfig — supplements coaching_system.md)
        a = self.athlete
        today = date.today()
        days_to_race = None
        if a.race_date:
            try:
                race = date.fromisoformat(a.race_date)
                days_to_race = (race - today).days
            except ValueError:
                pass

        profile_lines = [
            "\n## Athlete Profile (from config)",
            f"Name: {a.name}",
            f"Weight: {a.weight_kg}kg",
            f"Sports: {', '.join(a.sports) if isinstance(a.sports, list) else a.sports}",
            f"Timezone: {a.timezone}",
        ]
        # Paces / thresholds
        if getattr(a, "ftp", None):
            profile_lines.append(f"FTP: {a.ftp}W")
        if getattr(a, "marathon_pace", None):
            profile_lines.append(f"Marathon pace: {a.marathon_pace}/km")
        if getattr(a, "easy_pace", None):
            profile_lines.append(f"Easy pace: {a.easy_pace}/km")
        if getattr(a, "tempo_pace", None):
            profile_lines.append(f"Tempo pace: {a.tempo_pace}/km")
        if getattr(a, "hrv_baseline", None):
            profile_lines.append(f"HRV baseline: {a.hrv_baseline}ms")
        if getattr(a, "rhr_baseline", None):
            profile_lines.append(f"RHR baseline: {a.rhr_baseline}bpm")
        if getattr(a, "sleep_target_hours", None):
            profile_lines.append(f"Sleep target: {a.sleep_target_hours}h")

        # Race info
        profile_lines.append("\n### Target Race")
        profile_lines.append(f"Race: {a.race_name} — {a.race_date}")
        if days_to_race is not None:
            profile_lines.append(f"Days to race: {days_to_race}")
        if getattr(a, "goal_time", None):
            profile_lines.append(f"Goal time: {a.goal_time}")

        parts.append("\n".join(profile_lines))

        # 3. Tool-use instructions
        now_str = datetime.now(tz=TZ).strftime("%A %d %B %Y, %H:%M %Z")
        parts.append(
            f"## Instructions\n"
            f"- Current date/time: {now_str}\n"
            f"- ALWAYS call tools to fetch data before making specific recommendations. "
            f"Never invent data values.\n"
            f"- Call multiple tools in parallel when you need data from several sources "
            f"simultaneously.\n"
            f"- You can call tools multiple times with different parameters to drill deeper.\n"
            f"- After gathering data, synthesize it into concise, actionable coaching advice.\n"
            f"- Be direct and specific. Reference actual numbers from the data you fetch.\n"
            f"- Format responses for Telegram (use *bold*, plain text; avoid HTML; "
            f"keep under 800 words)."
        )

        return "\n\n".join(parts)

    def _load_history(self) -> list[dict]:
        """Load recent conversation as a proper messages array."""
        # get_recent_messages returns list[dict] with 'role' and 'content' keys,
        # already in chronological order (reversed inside the method).
        messages = self.db.get_recent_messages(limit=20)
        result = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                result.append({"role": role, "content": str(content)})
        # Ensure alternating user/assistant (Claude API requirement)
        return _normalize_history(result)

    async def respond(
        self,
        user_message: str,
        checkin_type: str = None,
        image_data: dict = None,
        # Legacy params kept for backward compat — ignored, tools fetch data now
        data_context: str = "",
        module_context: str = "",
        weather_context: str = "",
        science_context: str = "",
    ) -> str:
        """Run the agentic tool-use loop and return the final coaching response.

        Args:
            user_message: The athlete's text message or voice transcript.
            checkin_type: If set, prepends the check-in protocol prompt.
            image_data: Optional dict {"data": b64_str, "media_type": "image/jpeg"}
                        for multimodal (photo) messages. The image is sent to Claude
                        vision but NOT persisted in history to avoid DB bloat.
        """
        # Build the initial user turn (prepend check-in protocol if applicable)
        text_content = _make_initial_message(user_message, checkin_type, PROMPTS_DIR)

        if image_data:
            initial_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_data.get("media_type", "image/jpeg"),
                        "data": image_data["data"],
                    },
                },
                {"type": "text", "text": text_content},
            ]
        else:
            initial_content = text_content

        # Load history and append the new turn
        history = self._load_history()
        messages = history + [{"role": "user", "content": initial_content}]

        response = None
        for iteration in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            log.debug(
                "Engine iteration %d: stop_reason=%s tool_calls=%d",
                iteration + 1,
                response.stop_reason,
                sum(1 for b in response.content if b.type == "tool_use"),
            )

            if response.stop_reason != "tool_use":
                break

            # Extract all tool_use blocks from this turn
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            # Execute all tool calls in parallel
            results = await asyncio.gather(
                *[self.tools.execute(tu.name, tu.input) for tu in tool_uses]
            )

            # Log each tool call for observability
            for tu, result in zip(tool_uses, results):
                log.info(
                    "Tool call: %s(%s) -> %d chars",
                    tu.name,
                    tu.input,
                    len(result),
                )

            # Append assistant turn + all tool results, then continue the loop
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": result,
                        }
                        for tu, result in zip(tool_uses, results)
                    ],
                }
            )
        else:
            log.warning("Tool-use loop hit max iterations (%d)", MAX_TOOL_ITERATIONS)

        if response is None:
            return "Sorry, I could not generate a response."

        # Extract the final text content from Claude's last response
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        if not final_text:
            final_text = (
                "I gathered the data but couldn't formulate a response. Please try again."
            )

        # Persist the exchange to conversation history
        self.db.add_message("user", user_message, checkin_type=checkin_type)
        self.db.add_message("assistant", final_text, checkin_type=checkin_type)

        return final_text

    async def analyze(self, prompt: str, data_context: str = "") -> str:
        """One-shot analysis without conversation history (for modules).
        Kept for backward compat with any callers that use engine.analyze()."""
        try:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system,
                messages=[{"role": "user", "content": prompt}],
            )
            for block in resp.content:
                if hasattr(block, "text"):
                    return block.text
            return ""
        except Exception as exc:
            log.error("Analysis error: %s", exc)
            return ""


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_initial_message(
    user_message: str, checkin_type: str | None, prompts_dir: Path
) -> str:
    """Prepend the check-in protocol text if this is a scheduled check-in."""
    if not checkin_type:
        return user_message
    protocol_path = prompts_dir / f"{checkin_type}_checkin.md"
    if protocol_path.exists():
        protocol = protocol_path.read_text().strip()
        return f"{protocol}\n\n---\n\n{user_message}"
    return user_message


def _normalize_history(messages: list[dict]) -> list[dict]:
    """Ensure messages alternate user/assistant.
    Drops extra consecutive same-role messages (keeps the first of each run).
    Drops any leading assistant messages (Claude API requires user first)."""
    if not messages:
        return []
    normalized = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] != normalized[-1]["role"]:
            normalized.append(msg)
        # else: skip — duplicate role in a row
    # Claude requires the first message to be from 'user'
    while normalized and normalized[0]["role"] != "user":
        normalized.pop(0)
    return normalized
