"""Agent orchestrator — routes messages to specialized agents, manages memory and state.

Drop-in replacement for CoachingEngine with the same respond() signature.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic

from agents import classify_intent, get_agent
from config import AthleteConfig, TZ
from engine import run_tool_loop, _make_initial_message, _normalize_history, MODEL, MAX_TOKENS
from engine_tools import CoachTools, TOOL_SCHEMAS

if TYPE_CHECKING:
    from coaching_state_machine import CoachingStateMachine
    from database import Database
    from memory import AthleteMemory

log = logging.getLogger("coach.orchestrator")

PROMPTS_DIR = Path(__file__).parent / "prompts"


class AgentOrchestrator:
    """Multi-agent orchestrator with memory and state machine integration.

    Same respond() signature as CoachingEngine for backward compatibility.
    """

    def __init__(
        self,
        api_key: str,
        athlete: AthleteConfig,
        db: "Database",
        tools: CoachTools,
        memory: "AthleteMemory" = None,
        state_machine: "CoachingStateMachine" = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.athlete = athlete
        self.db = db
        self.tools = tools
        self.memory = memory
        self.state_machine = state_machine
        self._base_system = self._build_base_system()

    def _build_base_system(self) -> str:
        """Build the base coaching system prompt (shared across all agents)."""
        parts = []

        # 1. Base coaching philosophy
        coaching_prompt_path = PROMPTS_DIR / "coaching_system.md"
        if coaching_prompt_path.exists():
            parts.append(coaching_prompt_path.read_text().strip())

        # 2. Athlete profile
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

        profile_lines.append("\n### Target Race")
        profile_lines.append(f"Race: {a.race_name} — {a.race_date}")
        if days_to_race is not None:
            profile_lines.append(f"Days to race: {days_to_race}")
        if getattr(a, "goal_time", None):
            profile_lines.append(f"Goal time: {a.goal_time}")

        parts.append("\n".join(profile_lines))

        return "\n\n".join(parts)

    def _build_system_for_agent(
        self, agent_name: str, user_message: str
    ) -> tuple[str, list[dict]]:
        """Build the full system prompt for a specific agent.

        Returns (system_prompt, tool_schemas) tuple.
        """
        parts = [self._base_system]

        # Agent-specific prompt
        agent_config = get_agent(agent_name)
        agent_prompt = agent_config.load_prompt()
        if agent_prompt:
            parts.append(agent_prompt)

        # State machine context
        if self.state_machine:
            state_brief = self.state_machine.format_state_brief()
            parts.append(f"\n## Current Training Phase\n{state_brief}")

        # Long-term memory injection
        if self.memory and self.memory.available:
            memory_block = self.memory.get_context_block(user_message)
            if memory_block:
                parts.append(memory_block)

        # Tool-use instructions
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

        system = "\n\n".join(parts)
        tool_schemas = agent_config.tool_schemas

        return system, tool_schemas

    def _load_history(self) -> list[dict]:
        """Load recent conversation as a proper messages array."""
        messages = self.db.get_recent_messages(limit=20)
        result = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                result.append({"role": role, "content": str(content)})
        return _normalize_history(result)

    async def respond(
        self,
        user_message: str,
        checkin_type: str = None,
        image_data: dict = None,
        # Legacy params — ignored
        data_context: str = "",
        module_context: str = "",
        weather_context: str = "",
        science_context: str = "",
    ) -> str:
        """Route message to the appropriate specialized agent and return response.

        Same signature as CoachingEngine.respond() for backward compatibility.
        """
        # 1. Classify intent
        if checkin_type:
            agent_name = "daily_coach"  # check-ins always go to daily coach
        else:
            agent_name = classify_intent(user_message)

        log.info("Orchestrator routing to '%s' agent", agent_name)

        # 2. Build agent-specific system prompt + tool schemas
        system, tool_schemas = self._build_system_for_agent(agent_name, user_message)

        # 3. Build the initial user turn
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

        # 4. Load history and run tool loop
        history = self._load_history()
        messages = history + [{"role": "user", "content": initial_content}]

        final_text = await run_tool_loop(
            client=self.client,
            model=MODEL,
            system=system,
            tools=self.tools,
            tool_schemas=tool_schemas,
            messages=messages,
        )

        if not final_text:
            final_text = (
                "I gathered the data but couldn't formulate a response. Please try again."
            )

        # 5. Persist the exchange to conversation history
        self.db.add_message("user", user_message, checkin_type=checkin_type)
        self.db.add_message("assistant", final_text, checkin_type=checkin_type)

        # 6. Fire-and-forget memory extraction (non-blocking)
        if self.memory and self.memory.available:
            asyncio.ensure_future(
                self._extract_memories(user_message, final_text)
            )

        return final_text

    async def _extract_memories(self, user_message: str, assistant_response: str):
        """Extract memories from the conversation (async, non-blocking)."""
        try:
            await self.memory.extract_memories(
                user_message, assistant_response, anthropic_client=self.client
            )
        except Exception as exc:
            log.debug("Memory extraction failed (non-critical): %s", exc)

    async def analyze(self, prompt: str, data_context: str = "") -> str:
        """One-shot analysis without conversation history (backward compat)."""
        try:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._base_system,
                messages=[{"role": "user", "content": prompt}],
            )
            for block in resp.content:
                if hasattr(block, "text"):
                    return block.text
            return ""
        except Exception as exc:
            log.error("Analysis error: %s", exc)
            return ""
