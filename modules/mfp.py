"""MyFitnessPal / manual nutrition tracking module.

MyFitnessPal shut down their public developer API in late 2022 and the
unofficial python-myfitnesspal library (which relied on web scraping) has
been unreliable since 2023 due to authentication changes, CAPTCHAs, and
anti-scraping measures.  There is no stable programmatic integration path
as of 2025-2026.

This module therefore implements a **manual nutrition tracking** system
that lets athletes log meals via Telegram text messages.  A lightweight
parser handles natural-language inputs such as:

    "lunch: chicken rice 600cal 40p 80c 15f"
    "breakfast: oatmeal with banana"
    "snack: protein shake 250cal 30p 20c 5f"

When macros are not provided the meal is still recorded so the coach can
give qualitative feedback.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack", "pre-workout", "post-workout")

# Default targets for an 80 kg endurance athlete.
# Protein is fixed at 2 g/kg; carbs and fat scale with training load.
DEFAULT_TARGETS: Dict[str, Dict[str, float]] = {
    "rest":     {"calories": 2400, "protein_g": 160, "carbs_g": 240, "fat_g": 80},
    "easy":     {"calories": 2960, "protein_g": 160, "carbs_g": 320, "fat_g": 80},
    "moderate": {"calories": 3600, "protein_g": 160, "carbs_g": 480, "fat_g": 80},
    "hard":     {"calories": 4400, "protein_g": 160, "carbs_g": 640, "fat_g": 80},
}


# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

_CAL_RE = re.compile(r"(\d+)\s*(?:cal|kcal)", re.IGNORECASE)
_PROTEIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*p(?:rot(?:ein)?)?(?:\b|$)", re.IGNORECASE)
_CARBS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*c(?:arb(?:s)?)?(?:\b|$)", re.IGNORECASE)
_FAT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*f(?:at)?(?:\b|$)", re.IGNORECASE)

# Compact macro format: "40p/80c/15f" or "40p 80c 15f"
_COMPACT_MACROS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*p[\s/]+(\d+(?:\.\d+)?)\s*c[\s/]+(\d+(?:\.\d+)?)\s*f",
    re.IGNORECASE,
)


def _extract_float(pattern: re.Pattern, text: str) -> Optional[float]:
    """Return the first numeric match for *pattern* in *text*, or None."""
    m = pattern.search(text)
    return float(m.group(1)) if m else None


def _detect_meal_type(text: str) -> str:
    """Detect meal type from the beginning of the message text."""
    lower = text.lower().strip()
    for mt in MEAL_TYPES:
        if lower.startswith(mt):
            return mt
    # Heuristic aliases
    if lower.startswith("brekk") or lower.startswith("bf:") or lower.startswith("bf "):
        return "breakfast"
    if lower.startswith("din") or lower.startswith("supper"):
        return "dinner"
    if lower.startswith("preworkout") or lower.startswith("pre workout"):
        return "pre-workout"
    if lower.startswith("postworkout") or lower.startswith("post workout"):
        return "post-workout"
    return "snack"  # default when no keyword found


def _strip_meal_prefix(text: str) -> str:
    """Remove the meal-type prefix and colon/dash separator."""
    # e.g. "lunch: chicken rice 600cal" -> "chicken rice 600cal"
    stripped = re.sub(
        r"^(?:breakfast|lunch|dinner|snack|pre[- ]?workout|post[- ]?workout)"
        r"\s*[:—\-]?\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    return stripped.strip()


def _strip_macro_tokens(text: str) -> str:
    """Remove calorie and macro tokens so only the food description remains."""
    text = _CAL_RE.sub("", text)
    text = _COMPACT_MACROS_RE.sub("", text)
    text = _PROTEIN_RE.sub("", text)
    text = _CARBS_RE.sub("", text)
    text = _FAT_RE.sub("", text)
    # Clean up stray separators
    text = re.sub(r"[/,]+\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_meal_from_text(text: str) -> Dict[str, Any]:
    """Parse a natural-language meal log into a structured dict.

    Supported formats:
        "lunch: chicken rice 600cal 40p 80c 15f"
        "breakfast: oatmeal with banana"
        "snack: protein shake 250kcal 30p 20c 5f"
        "dinner: salmon pasta 700cal 45p/90c/20f"
        "post-workout: shake 300cal 40p 30c 5f"

    Returns
    -------
    dict
        Keys: meal_type, description, calories, protein_g, carbs_g, fat_g,
              timestamp, has_macros.
        Numeric fields are ``None`` when not supplied by the user.
    """
    meal_type = _detect_meal_type(text)
    body = _strip_meal_prefix(text)

    # Try compact format first: "40p/80c/15f"
    compact = _COMPACT_MACROS_RE.search(body)
    if compact:
        protein_g = float(compact.group(1))
        carbs_g = float(compact.group(2))
        fat_g = float(compact.group(3))
    else:
        protein_g = _extract_float(_PROTEIN_RE, body)
        carbs_g = _extract_float(_CARBS_RE, body)
        fat_g = _extract_float(_FAT_RE, body)

    calories = _extract_float(_CAL_RE, body)

    # If calories not stated but all macros present, compute from macros.
    if calories is None and all(v is not None for v in (protein_g, carbs_g, fat_g)):
        calories = round(protein_g * 4 + carbs_g * 4 + fat_g * 9)

    description = _strip_macro_tokens(body) or body
    has_macros = all(v is not None for v in (protein_g, carbs_g, fat_g))

    return {
        "meal_type": meal_type,
        "description": description,
        "calories": int(calories) if calories is not None else None,
        "protein_g": round(protein_g, 1) if protein_g is not None else None,
        "carbs_g": round(carbs_g, 1) if carbs_g is not None else None,
        "fat_g": round(fat_g, 1) if fat_g is not None else None,
        "has_macros": has_macros,
        "timestamp": datetime.now().isoformat(timespec="minutes"),
    }


def get_daily_summary(
    meals_today: List[Dict[str, Any]],
    targets: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Aggregate a list of parsed meals into daily totals.

    Parameters
    ----------
    meals_today:
        Each item is a dict as returned by :func:`parse_meal_from_text`.
    targets:
        Optional target dict with keys ``calories``, ``protein_g``,
        ``carbs_g``, ``fat_g``.  Falls back to ``DEFAULT_TARGETS["easy"]``.

    Returns
    -------
    dict
        ``totals``:   summed macros across all logged meals.
        ``remaining``: how much is left vs. targets (negative = over).
        ``pct``:       percentage of target reached for each macro.
        ``meal_count``: number of meals logged.
        ``meals_with_macros``: count of meals that had full macro data.
        ``by_meal_type``: breakdown per meal type.
    """
    if targets is None:
        targets = DEFAULT_TARGETS["easy"]

    totals: Dict[str, float] = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    by_meal: Dict[str, Dict[str, float]] = {}
    meals_with_macros = 0

    for meal in meals_today:
        mt = meal.get("meal_type", "snack")
        if mt not in by_meal:
            by_meal[mt] = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}

        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            val = meal.get(key)
            if val is not None:
                totals[key] += val
                by_meal[mt][key] += val

        if meal.get("has_macros"):
            meals_with_macros += 1

    remaining = {k: round(targets[k] - totals[k]) for k in totals}
    pct = {
        k: round(totals[k] / targets[k] * 100) if targets.get(k) else 0
        for k in totals
    }

    return {
        "totals": {k: round(v) for k, v in totals.items()},
        "remaining": remaining,
        "pct": pct,
        "targets": targets,
        "meal_count": len(meals_today),
        "meals_with_macros": meals_with_macros,
        "by_meal_type": by_meal,
    }


def format_nutrition_tracking_context(
    summary: Dict[str, Any],
    targets: Optional[Dict[str, float]] = None,
) -> str:
    """Format a daily nutrition summary into human-readable text for the coach.

    Parameters
    ----------
    summary:
        Output of :func:`get_daily_summary`.
    targets:
        Override targets; if ``None`` uses whatever was in the summary.

    Returns
    -------
    str
        Multi-line text block suitable for inclusion in a Telegram message
        or coach context prompt.
    """
    t = targets or summary.get("targets") or DEFAULT_TARGETS["easy"]
    totals = summary["totals"]
    remaining = summary["remaining"]
    pct = summary["pct"]

    lines: list[str] = []
    lines.append("NUTRITION TRACKING TODAY:")
    lines.append(f"Meals logged: {summary['meal_count']} "
                 f"({summary['meals_with_macros']} with full macros)")
    lines.append("")

    # Totals vs targets
    lines.append(
        f"Calories:  {totals['calories']:>5} / {int(t['calories']):<5}  "
        f"({pct['calories']}%)  [{remaining['calories']:+d} remaining]"
    )
    lines.append(
        f"Protein:  {totals['protein_g']:>5}g / {int(t['protein_g'])}g "
        f"({pct['protein_g']}%)  [{remaining['protein_g']:+d}g remaining]"
    )
    lines.append(
        f"Carbs:    {totals['carbs_g']:>5}g / {int(t['carbs_g'])}g "
        f"({pct['carbs_g']}%)  [{remaining['carbs_g']:+d}g remaining]"
    )
    lines.append(
        f"Fat:      {totals['fat_g']:>5}g / {int(t['fat_g'])}g "
        f"({pct['fat_g']}%)  [{remaining['fat_g']:+d}g remaining]"
    )

    # Per-meal-type breakdown
    by_meal = summary.get("by_meal_type", {})
    if by_meal:
        lines.append("")
        lines.append("By meal:")
        for mt in MEAL_TYPES:
            if mt not in by_meal:
                continue
            m = by_meal[mt]
            lines.append(
                f"  {mt:<14} {int(m['calories']):>4} kcal  "
                f"P {int(m['protein_g']):>3}g  "
                f"C {int(m['carbs_g']):>3}g  "
                f"F {int(m['fat_g']):>3}g"
            )

    # Qualitative flags
    lines.append("")
    if pct["protein_g"] < 50 and summary["meal_count"] >= 2:
        lines.append("! Protein intake lagging -- prioritise a high-protein meal next.")
    if remaining["protein_g"] > 0 and remaining["protein_g"] <= 40:
        lines.append("~ Close to protein target -- one more portion of lean protein will do it.")
    if pct["calories"] > 110:
        lines.append("! Over calorie target for the day.")
    if summary["meals_with_macros"] < summary["meal_count"]:
        missing = summary["meal_count"] - summary["meals_with_macros"]
        lines.append(
            f"Note: {missing} meal(s) logged without macros. "
            "Totals may undercount actual intake."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def get_targets_for_load(
    training_load: str = "easy",
    weight_kg: float = 80.0,
) -> Dict[str, float]:
    """Return macro targets for a given training load, scaled to body weight.

    This mirrors :func:`modules.nutrition.calculate_daily_targets` but
    returns a flat dict compatible with :func:`get_daily_summary`.
    """
    load_map = {
        "rest":     {"kcal_kg": 30, "carb_g_kg": 3.0, "fat_g_kg": 1.0},
        "easy":     {"kcal_kg": 37, "carb_g_kg": 4.0, "fat_g_kg": 1.0},
        "moderate": {"kcal_kg": 45, "carb_g_kg": 6.0, "fat_g_kg": 1.0},
        "hard":     {"kcal_kg": 55, "carb_g_kg": 8.0, "fat_g_kg": 1.0},
    }
    params = load_map.get(training_load, load_map["easy"])
    return {
        "calories": round(weight_kg * params["kcal_kg"]),
        "protein_g": round(weight_kg * 2.0),
        "carbs_g": round(weight_kg * params["carb_g_kg"]),
        "fat_g": round(weight_kg * params["fat_g_kg"]),
    }


def is_meal_log(text: str) -> bool:
    """Heuristic check: does *text* look like a meal-logging message?

    Returns ``True`` when the message starts with a known meal keyword or
    contains calorie/macro tokens.  Useful for the Telegram handler to
    decide whether to route a message to :func:`parse_meal_from_text`.
    """
    lower = text.lower().strip()
    # Starts with a meal keyword
    for mt in MEAL_TYPES:
        if lower.startswith(mt):
            return True
    # Contains calorie or macro shorthand
    if _CAL_RE.search(text):
        return True
    if _COMPACT_MACROS_RE.search(text):
        return True
    return False
