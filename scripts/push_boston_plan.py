#!/usr/bin/env python3
"""
Boston Marathon Training Plan — Push to Intervals.icu
6 weeks: March 8 → April 20, 2026

Athlete: Sebastien Denes, 80kg, FTP 315W
Context: 2 months off running, aerobic base from cycling, 21km long run 2.5 months ago
Strategy: Rebuild run frequency → increase duration → add quality → taper
Marathon pace estimate: 5:00-5:10/km (conservative given detraining)
"""

import json
import urllib.request
import os
from datetime import datetime, timedelta

API_KEY = "2n5tjrtz5xzzeffljjrkjtu9g"
ATHLETE_ID = "i112009"
BASE_URL = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"

import base64
auth = base64.b64encode(f"API_KEY:{API_KEY}".encode()).decode()

def create_event(date_str, name, sport_type, description, moving_time_secs=None, indoor=True, category="WORKOUT"):
    """Create an event in Intervals.icu"""
    payload = {
        "category": category,
        "start_date_local": f"{date_str}T09:00:00",
        "name": name,
        "type": sport_type,
        "description": description,
        "indoor": indoor,
    }
    if moving_time_secs:
        payload["moving_time"] = moving_time_secs

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}"
        },
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read())
        print(f"  ✓ {date_str} | {name}")
        return result
    except Exception as e:
        print(f"  ✗ {date_str} | {name} | Error: {e}")
        return None


# ============================================================
# WEEK 1: March 8-14 — REBUILD (Frequency Focus)
# Goal: 4 runs, all easy Z2, reintroduce running load
# Run volume: ~25km | Cycling: 2 easy rides
# ============================================================
print("\n=== WEEK 1: REBUILD (March 8-14) ===")

create_event("2026-03-08", "🏃 Easy Run — Comeback",
    "Run",
    "REBUILD WEEK 1\n\n"
    "Easy Z2 run. Goal is just to run again.\n"
    "• 30 min easy (HR <140)\n"
    "• Walk breaks are fine if needed\n"
    "• Pay attention to any joint/tendon pain\n"
    "• Target pace: 5:30-6:00/km\n\n"
    "Post-run: 10min stretching (calves, hip flexors, quads)",
    moving_time_secs=1800)

create_event("2026-03-09", "🚴 Easy Spin — Recovery",
    "Ride",
    "Active recovery spin.\n"
    "• 45 min Z1-Z2 (<75% FTP)\n"
    "• Cadence 85-95rpm\n"
    "• Keep HR under 130",
    moving_time_secs=2700)

create_event("2026-03-10", "💪 Strength — Run Foundations",
    "Workout",
    "Running-specific strength (30 min):\n"
    "• Single leg squats 3x10 each\n"
    "• Calf raises 3x15 (slow eccentric)\n"
    "• Glute bridges 3x12\n"
    "• Side-lying clams 3x15 each\n"
    "• Plank 3x45sec\n"
    "• Dead bugs 3x10 each\n\n"
    "Focus: injury prevention, hip/glute activation")

create_event("2026-03-11", "🏃 Easy Run",
    "Run",
    "Easy Z2 run.\n"
    "• 35 min easy (HR <140)\n"
    "• Smooth and relaxed\n"
    "• Target pace: 5:20-5:50/km\n"
    "• Focus on cadence ~170-175spm",
    moving_time_secs=2100)

create_event("2026-03-12", "🚴 Easy Endurance Ride",
    "Ride",
    "Aerobic maintenance.\n"
    "• 60 min Z2 (75-85% FTP)\n"
    "• Steady effort\n"
    "• Supports running recovery while maintaining cycling fitness",
    moving_time_secs=3600)

create_event("2026-03-13", "🏃 Easy Run",
    "Run",
    "Easy Z2 run.\n"
    "• 35 min easy (HR <140)\n"
    "• Target pace: 5:20-5:50/km\n"
    "• If legs feel heavy from yesterday's ride, slow down — no ego",
    moving_time_secs=2100)

create_event("2026-03-14", "🏃 Long Run — Week 1",
    "Run",
    "First long run back. EASY pace only.\n"
    "• 50 min Z2 (HR <140)\n"
    "• Target pace: 5:30-6:00/km\n"
    "• Practice fueling: take 30g carb at 30min mark\n"
    "• Walk 1min every 15min if needed\n\n"
    "This is about time on feet, not pace.",
    moving_time_secs=3000)


# ============================================================
# WEEK 2: March 15-21 — REBUILD (Duration Increase)
# Goal: 4 runs, extend duration, add 1 ride
# Run volume: ~35km
# ============================================================
print("\n=== WEEK 2: REBUILD (March 15-21) ===")

create_event("2026-03-15", "Rest Day",
    "Note",
    "Full rest. Stretch, hydrate, sleep.\n"
    "Target: 7.5+ hours tonight.",
    category="NOTE")

create_event("2026-03-16", "🏃 Easy Run",
    "Run",
    "• 40 min easy Z2 (HR <140)\n"
    "• Target pace: 5:15-5:45/km\n"
    "• Should feel controlled and comfortable\n"
    "• Check in with calves and Achilles",
    moving_time_secs=2400)

create_event("2026-03-17", "💪 Strength + 🚴 Easy Spin",
    "Workout",
    "AM: Strength (30 min)\n"
    "• Single leg squats 3x12 each\n"
    "• Calf raises 3x15 (slow eccentric)\n"
    "• Romanian deadlift 3x10\n"
    "• Side plank 3x30sec each\n"
    "• Copenhagen adductors 3x8 each\n\n"
    "PM: Easy spin 40min Z1-Z2")

create_event("2026-03-18", "🏃 Easy Run",
    "Run",
    "• 40 min easy Z2 (HR <140)\n"
    "• Target pace: 5:15-5:45/km\n"
    "• Focus on relaxed shoulders, quick feet",
    moving_time_secs=2400)

create_event("2026-03-19", "🚴 Endurance Ride",
    "Ride",
    "Aerobic maintenance.\n"
    "• 60 min Z2 (75-85% FTP)\n"
    "• Legs should feel OK — if not, drop to 45min Z1",
    moving_time_secs=3600)

create_event("2026-03-20", "🏃 Easy Run + Strides",
    "Run",
    "• 35 min easy Z2\n"
    "• Last 5 min: 4x20sec strides at 4:15/km pace with 40sec jog\n"
    "• Strides are NOT sprints — smooth acceleration, fast turnover\n"
    "• This introduces neuromuscular stimulus safely",
    moving_time_secs=2400)

create_event("2026-03-21", "🏃 Long Run — Week 2",
    "Run",
    "Building time on feet.\n"
    "• 65 min Z2 (HR <140)\n"
    "• Target pace: 5:20-5:50/km\n"
    "• Practice fueling: 30g carbs at 30min + 30g at 55min\n"
    "• Flat route if possible\n\n"
    "Distance target: ~12-13km",
    moving_time_secs=3900)


# ============================================================
# WEEK 3: March 22-28 — BUILD (Add Quality)
# Goal: 5 runs, introduce first tempo session
# Run volume: ~42km
# ============================================================
print("\n=== WEEK 3: BUILD (March 22-28) ===")

create_event("2026-03-22", "Rest Day",
    "Note",
    "Full rest after long run. Prioritize sleep + nutrition.",
    category="NOTE")

create_event("2026-03-23", "🏃 Easy Run",
    "Run",
    "• 40 min easy Z2 (HR <140)\n"
    "• Recovery from yesterday's long run\n"
    "• Easy pace: 5:20-5:50/km",
    moving_time_secs=2400)

create_event("2026-03-24", "🚴 Easy Spin",
    "Ride",
    "• 45 min Z1-Z2 easy spin\n"
    "• Active recovery, flush legs\n"
    "• HR under 130",
    moving_time_secs=2700)

create_event("2026-03-25", "🏃 Marathon Pace Intro",
    "Run",
    "FIRST QUALITY SESSION — proceed only if legs feel good.\n\n"
    "• 15 min easy warmup\n"
    "• 3 x 5 min at marathon pace (5:00-5:10/km) with 2 min easy jog\n"
    "• 10 min easy cooldown\n\n"
    "Total: ~45 min\n"
    "HR during MP intervals: 150-158 (Z3)\n"
    "If HR drifts above 160, slow down — your marathon pace isn't there yet.\n\n"
    "Practice taking a gel during the warmup.",
    moving_time_secs=2700)

create_event("2026-03-26", "💪 Strength",
    "Workout",
    "Running-specific strength (30 min):\n"
    "• Step-ups 3x10 each\n"
    "• Single leg calf raises 3x12\n"
    "• Bulgarian split squats 3x8 each\n"
    "• Hip hikes 3x12 each\n"
    "• Pallof press 3x10 each\n"
    "• Plank 3x60sec")

create_event("2026-03-27", "🏃 Easy Run",
    "Run",
    "• 40 min easy Z2\n"
    "• Shake out from yesterday's quality session\n"
    "• Easy pace only: 5:20-5:50/km",
    moving_time_secs=2400)

create_event("2026-03-28", "🏃 Long Run — Week 3",
    "Run",
    "Key session. Longest run since December.\n\n"
    "• 80 min total\n"
    "• First 55 min: easy Z2 (5:20-5:50/km)\n"
    "• Last 25 min: marathon pace (5:00-5:10/km)\n"
    "• HR cap: 155 in the MP section\n\n"
    "FUELING — practice race nutrition:\n"
    "• Gel/drink at 25min, 50min, 70min (aim for 45-60g carb/hr)\n"
    "• Water every 20min\n\n"
    "Distance target: ~15-16km",
    moving_time_secs=4800)


# ============================================================
# WEEK 4: March 29 - April 4 — RECOVERY WEEK
# Aligned with Intervals.icu recovery week note
# Goal: Absorb training, reduce volume 30%, maintain frequency
# Run volume: ~28km
# ============================================================
print("\n=== WEEK 4: RECOVERY (March 29 - April 4) ===")

create_event("2026-03-29", "Rest Day",
    "Note",
    "Recovery week begins. Sleep is your #1 training tool this week.\n"
    "Target: 8 hours every night this week.",
    category="NOTE")

create_event("2026-03-30", "🏃 Easy Run",
    "Run",
    "Recovery week — easy only.\n"
    "• 35 min Z2 easy (HR <135)\n"
    "• Slower than usual is fine\n"
    "• Focus on how your body feels — note any niggles",
    moving_time_secs=2100)

create_event("2026-03-31", "🚴 Easy Spin",
    "Ride",
    "• 45 min Z1-Z2\n"
    "• Pure recovery spin\n"
    "• HR under 125",
    moving_time_secs=2700)

create_event("2026-04-01", "🏃 Easy Run + Strides",
    "Run",
    "• 30 min easy Z2\n"
    "• 4 x 20sec strides at 4:15/km with 40sec jog\n"
    "• Keep it light and snappy",
    moving_time_secs=2100)

create_event("2026-04-02", "💪 Light Strength",
    "Workout",
    "Reduced volume strength (20 min):\n"
    "• Bodyweight squats 2x15\n"
    "• Calf raises 2x12\n"
    "• Glute bridges 2x12\n"
    "• Plank 2x45sec\n\n"
    "Last heavy strength session before Boston. After this, maintenance only.")

create_event("2026-04-03", "🏃 Easy Run",
    "Run",
    "• 30 min easy Z2 (HR <135)\n"
    "• Gentle and short",
    moving_time_secs=1800)

create_event("2026-04-04", "🏃 Long Run — Recovery Week",
    "Run",
    "Shorter long run — recovery week.\n"
    "• 60 min easy Z2 (5:30-6:00/km)\n"
    "• No marathon pace work today\n"
    "• Practice race-day nutrition: 60g carb/hr\n\n"
    "How do the legs feel after 3 weeks of rebuilding?",
    moving_time_secs=3600)


# ============================================================
# WEEK 5: April 5-11 — PEAK WEEK
# Goal: Highest quality, final long run, then begin taper
# Run volume: ~45km
# ============================================================
print("\n=== WEEK 5: PEAK (April 5-11) ===")

create_event("2026-04-05", "Rest Day",
    "Note",
    "Rest before peak week. Sleep well tonight — big week ahead.",
    category="NOTE")

create_event("2026-04-06", "🏃 Easy Run",
    "Run",
    "• 40 min easy Z2\n"
    "• Legs should feel fresh from recovery week\n"
    "• Pace: 5:15-5:45/km",
    moving_time_secs=2400)

create_event("2026-04-07", "🏃 Marathon Pace Session",
    "Run",
    "KEY SESSION — your most important workout before Boston.\n\n"
    "• 15 min easy warmup\n"
    "• 25 min continuous at marathon pace (5:00-5:10/km)\n"
    "• 5 min easy jog\n"
    "• 10 min at marathon pace\n"
    "• 10 min easy cooldown\n\n"
    "Total: ~65 min\n"
    "HR during MP: 150-158\n\n"
    "FUELING: Take 30g carbs at 20min and 45min.\n"
    "This is a DRESS REHEARSAL — wear race-day shoes and kit.\n"
    "After this session you'll know if 5:00/km is realistic.",
    moving_time_secs=3900)

create_event("2026-04-08", "🚴 Recovery Spin",
    "Ride",
    "• 40 min Z1 only\n"
    "• Flush legs after yesterday's key session\n"
    "• HR under 120",
    moving_time_secs=2400)

create_event("2026-04-09", "🏃 Easy Run",
    "Run",
    "• 35 min easy Z2\n"
    "• Recovery after MP session\n"
    "• If legs are heavy, cut to 25min",
    moving_time_secs=2100)

create_event("2026-04-10", "🏃 Easy Run + Strides",
    "Run",
    "• 30 min easy Z2\n"
    "• 6 x 20sec strides (smooth, not sprinting)\n"
    "• Last day of meaningful running stimulus before taper",
    moving_time_secs=2100)

create_event("2026-04-11", "🏃 Long Run — FINAL",
    "Run",
    "LAST LONG RUN before Boston.\n\n"
    "• 90 min total\n"
    "• First 50 min: easy Z2 (5:20-5:50/km)\n"
    "• 30 min: marathon pace (5:00-5:10/km)\n"
    "• Last 10 min: easy cooldown\n\n"
    "FULL RACE NUTRITION REHEARSAL:\n"
    "• 60-90g carb/hr using your race-day products\n"
    "• Same timing as race day\n"
    "• Same breakfast 2.5h before\n\n"
    "Distance target: ~17-18km\n\n"
    "After today: TAPER BEGINS. Trust the work.",
    moving_time_secs=5400)


# ============================================================
# WEEK 6: April 12-18 — TAPER
# Goal: Reduce volume 50-60%, maintain light intensity, carb load
# Run volume: ~18km
# ============================================================
print("\n=== WEEK 6: TAPER (April 12-18) ===")

create_event("2026-04-12", "Rest Day",
    "Note",
    "Taper begins. Trust the fitness you've built.\n"
    "More sleep, more carbs, less training.\n"
    "Every hour of sleep this week = free speed on race day.",
    category="NOTE")

create_event("2026-04-13", "🏃 Easy Run + Strides",
    "Run",
    "TAPER RUN 1\n"
    "• 30 min easy Z2\n"
    "• 4 x 20sec strides at marathon pace\n"
    "• Short and sharp — keep the legs turning over",
    moving_time_secs=2100)

create_event("2026-04-14", "🚴 Easy Spin",
    "Ride",
    "• 30 min Z1 spin\n"
    "• Very light — just blood flow\n"
    "• Last ride before Boston",
    moving_time_secs=1800)

create_event("2026-04-15", "🏃 Sharpener",
    "Run",
    "TAPER RUN 2\n"
    "• 10 min easy warmup\n"
    "• 3 x 3 min at marathon pace (5:00-5:10/km) with 2 min jog\n"
    "• 10 min easy cooldown\n\n"
    "Total: ~35 min\n"
    "This reminds your legs what race pace feels like.\n"
    "Should feel EASY — if it doesn't, the pace target needs adjusting.",
    moving_time_secs=2100)

create_event("2026-04-16", "Rest Day",
    "Note",
    "Rest. Light walking only.\n"
    "Begin carb loading tonight (10-12g carb/kg = 800-960g carbs/day).\n"
    "Reduce fiber and fat from today.",
    category="NOTE")

create_event("2026-04-17", "🏃 Shakeout Run",
    "Run",
    "FINAL RUN BEFORE BOSTON\n"
    "• 20 min very easy\n"
    "• 2 x 30sec at marathon pace\n"
    "• That's it. Done.\n\n"
    "CARB LOADING DAY 2: 800-960g carbs today.\n"
    "Low fiber, low fat, familiar foods.\n"
    "White rice, pasta, bread, sports drinks, juice.",
    moving_time_secs=1500)

create_event("2026-04-18", "Rest — Race Prep",
    "Note",
    "CARB LOADING DAY 3 (final).\n"
    "• 800-960g carbs today\n"
    "• Low fiber, low fat\n"
    "• Lay out ALL race gear tonight\n"
    "• Review race-day nutrition plan\n"
    "• Light walking only\n"
    "• Bed by 21:30 — aim for 9 hours\n\n"
    "Pack list: shoes, kit, gels (with backups), watch, bib, sunscreen",
    category="NOTE")

create_event("2026-04-19", "Rest — Race Eve",
    "Note",
    "REST. No running.\n\n"
    "• Easy walk OK, nothing else\n"
    "• Normal meals, keep carbs high\n"
    "• Final meal by 19:00 — familiar foods, moderate portions\n"
    "• Reduce fluid intake by 20:00 to avoid overnight waking\n"
    "• Bed by 21:00\n"
    "• You won't sleep great — that's normal. Don't stress.\n\n"
    "RACE DAY PLAN:\n"
    "• Wake 3h before gun\n"
    "• Breakfast: 150-200g carbs (toast + jam, banana, sports drink)\n"
    "• Top-up gel 15min before start\n"
    "• Race fueling: 60g carb/hr from km 5",
    category="NOTE")

create_event("2026-04-20", "🏃 BOSTON MARATHON",
    "Run",
    "RACE DAY — BOSTON MARATHON 🏁\n\n"
    "PACING STRATEGY:\n"
    "• Start conservative: 5:10-5:15/km for first 10km\n"
    "• If feeling good at half: hold 5:05-5:10/km\n"
    "• Newton Hills (km 26-34): effort-based, not pace-based\n"
    "• Heartbreak Hill: stay smooth, don't fight it\n"
    "• Last 8km: whatever you have left\n\n"
    "FUELING:\n"
    "• Gel every 25-30min starting at km 5\n"
    "• Target 60g carb/hr\n"
    "• Water at every aid station\n"
    "• Electrolytes every other station\n\n"
    "HR CEILING: 158bpm for first half. Race effort, not race pace.\n\n"
    "GO GET IT, SEBASTIEN. 🇫🇷",
    moving_time_secs=12600,
    indoor=False)


print("\n✅ Boston Marathon training plan pushed to Intervals.icu!")
print("44 sessions created across 6 weeks.")
