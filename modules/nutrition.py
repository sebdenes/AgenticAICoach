"""Nutrition module — macro targets, fueling periodization, race nutrition."""

from datetime import datetime


def calculate_daily_targets(weight_kg: float, training_load: str = "rest") -> dict:
    """Calculate daily macro targets based on training load."""
    load_map = {
        "rest": {"kcal_kg": 30, "carb_low": 2, "carb_high": 4},
        "easy": {"kcal_kg": 37, "carb_low": 3, "carb_high": 5},
        "moderate": {"kcal_kg": 45, "carb_low": 5, "carb_high": 7},
        "hard": {"kcal_kg": 55, "carb_low": 7, "carb_high": 10},
        "race_week": {"kcal_kg": 55, "carb_low": 10, "carb_high": 12},
    }

    params = load_map.get(training_load, load_map["easy"])
    protein_g = weight_kg * 2.0  # 2g/kg
    fat_g = weight_kg * 1.0  # 1g/kg minimum
    carb_low = weight_kg * params["carb_low"]
    carb_high = weight_kg * params["carb_high"]
    kcal = weight_kg * params["kcal_kg"]

    return {
        "training_load": training_load,
        "calories": round(kcal),
        "protein_g": round(protein_g),
        "carb_range_g": f"{round(carb_low)}-{round(carb_high)}",
        "fat_g": round(fat_g),
        "protein_per_meal": f"{round(protein_g/4)}-{round(protein_g/5)}",
    }


def classify_training_load(tss: float, duration_min: float = 0) -> str:
    """Classify a day's training load for nutrition scaling."""
    if tss == 0:
        return "rest"
    elif tss < 40:
        return "easy"
    elif tss < 80:
        return "moderate"
    else:
        return "hard"


def pre_workout_fuel(session_type: str, duration_min: int) -> dict:
    """Pre-workout fueling recommendation."""
    if duration_min < 60:
        return {
            "timing": "1-2h before",
            "carbs_g": "30-40",
            "suggestion": "Banana + toast with honey, or rice cake with jam",
        }
    elif duration_min < 90:
        return {
            "timing": "2-3h before",
            "carbs_g": "50-80",
            "suggestion": "Oatmeal with banana and honey, or toast with peanut butter + juice",
        }
    else:
        return {
            "timing": "2.5-3h before",
            "carbs_g": "80-120",
            "suggestion": "Rice/pasta + light protein, or large oatmeal bowl + banana + toast",
        }


def during_workout_fuel(duration_min: int, intensity: str = "moderate") -> dict:
    """During-workout fueling recommendation."""
    if duration_min < 60:
        return {"carbs_per_hour": 0, "suggestion": "Water only"}
    elif duration_min < 90:
        return {"carbs_per_hour": "30-60", "suggestion": "Sports drink or 1 gel per 30min"}
    else:
        return {
            "carbs_per_hour": "60-90",
            "suggestion": "2 gels/hr + sports drink. Train the gut — build up gradually.",
        }


def post_workout_recovery(weight_kg: float) -> dict:
    """Post-workout recovery nutrition."""
    return {
        "timing": "Within 30-60 min",
        "protein_g": f"{round(weight_kg * 0.3)}-{round(weight_kg * 0.4)}",
        "carbs_g": f"{round(weight_kg * 1.0)}-{round(weight_kg * 1.2)}",
        "suggestion": "Greek yogurt + granola + fruit, or protein shake + banana + oats",
        "hydration": "Replace 150% of fluid lost",
    }


def format_nutrition_context(targets: dict, training_load_today: str = "easy") -> str:
    t = calculate_daily_targets(80, training_load_today)
    lines = [
        "NUTRITION TARGETS TODAY:",
        f"Load classification: {t['training_load']}",
        f"Calories: ~{t['calories']} kcal",
        f"Protein: {t['protein_g']}g ({t['protein_per_meal']}g per meal, 4-5 meals)",
        f"Carbs: {t['carb_range_g']}g",
        f"Fat: {t['fat_g']}g minimum",
    ]
    return "\n".join(lines)
