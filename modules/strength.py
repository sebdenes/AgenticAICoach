"""Strength training module — running-specific programming, progressive overload."""

from datetime import datetime, timedelta


# Exercise library — running-specific
EXERCISE_LIBRARY = {
    "lower": [
        {"name": "Single Leg Squat", "category": "quad/glute", "progression": ["bodyweight", "goblet", "barbell"]},
        {"name": "Romanian Deadlift", "category": "posterior chain", "progression": ["bodyweight", "dumbbell", "barbell"]},
        {"name": "Bulgarian Split Squat", "category": "quad/glute", "progression": ["bodyweight", "dumbbell", "barbell"]},
        {"name": "Step-ups", "category": "quad/glute", "progression": ["bodyweight", "dumbbell", "weighted vest"]},
        {"name": "Calf Raises", "category": "calf", "progression": ["double leg", "single leg", "weighted"]},
        {"name": "Hip Thrust", "category": "glute", "progression": ["bodyweight", "banded", "barbell"]},
    ],
    "stability": [
        {"name": "Single Leg Balance", "category": "proprioception"},
        {"name": "Copenhagen Adductor", "category": "adductor"},
        {"name": "Side-lying Clam", "category": "hip external rotation"},
        {"name": "Hip Hike", "category": "hip stability"},
        {"name": "Banded Walk", "category": "glute med"},
    ],
    "core": [
        {"name": "Plank", "category": "anti-extension"},
        {"name": "Side Plank", "category": "anti-lateral flexion"},
        {"name": "Dead Bug", "category": "anti-extension"},
        {"name": "Pallof Press", "category": "anti-rotation"},
        {"name": "Bird Dog", "category": "anti-rotation"},
    ],
    "plyometric": [
        {"name": "Box Jump", "category": "power"},
        {"name": "Single Leg Hop", "category": "reactive strength"},
        {"name": "Bounding", "category": "running power"},
        {"name": "A-Skip", "category": "coordination"},
    ],
}


def generate_session(phase: str = "base", focus: str = "general", duration_min: int = 30) -> dict:
    """Generate a strength session based on training phase and focus."""
    sessions = {
        "base": {
            "name": "Foundation Strength",
            "sets_reps": "3x10-12",
            "rest": "60-90sec",
            "exercises": [
                "Single Leg Squat 3x10 each",
                "Calf Raises 3x15 (slow eccentric 3sec)",
                "Romanian Deadlift 3x10",
                "Side-lying Clam 3x15 each",
                "Plank 3x45sec",
                "Dead Bug 3x10 each",
            ],
        },
        "build": {
            "name": "Build Strength",
            "sets_reps": "3x8-10",
            "rest": "90sec",
            "exercises": [
                "Bulgarian Split Squat 3x8 each",
                "Single Leg Calf Raises 3x12 (slow eccentric)",
                "Hip Thrust 3x12",
                "Copenhagen Adductor 3x8 each",
                "Side Plank 3x30sec each",
                "Pallof Press 3x10 each",
            ],
        },
        "peak": {
            "name": "Maintenance Strength",
            "sets_reps": "2x8-10",
            "rest": "60sec",
            "exercises": [
                "Step-ups 2x10 each",
                "Calf Raises 2x12",
                "Glute Bridge 2x15",
                "Plank 2x45sec",
            ],
        },
        "taper": {
            "name": "Light Maintenance",
            "sets_reps": "2x8",
            "rest": "60sec",
            "exercises": [
                "Bodyweight Squats 2x15",
                "Calf Raises 2x12",
                "Glute Bridge 2x12",
                "Plank 2x30sec",
            ],
        },
    }

    session = sessions.get(phase, sessions["base"])
    session["phase"] = phase
    session["duration_min"] = duration_min
    return session


def format_strength_context(history: list, current_phase: str = "base") -> str:
    """Format strength training context for coaching."""
    session = generate_session(current_phase)
    lines = [
        "STRENGTH CONTEXT:",
        f"Phase: {current_phase} | Recommended: {session['name']}",
        f"Recent sessions: {len(history)} in last 30 days",
    ]
    if history:
        last = history[-1]
        lines.append(f"Last session: {last.get('date', 'unknown')}")
    else:
        lines.append("No recent strength sessions recorded")
    return "\n".join(lines)
