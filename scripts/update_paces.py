#!/usr/bin/env python3
"""Update all Intervals.icu events with corrected paces for 3:00 marathon target."""

import json
import urllib.request
import base64

API_KEY = "2n5tjrtz5xzzeffljjrkjtu9g"
ATHLETE_ID = "i112009"
auth = base64.b64encode(f"API_KEY:{API_KEY}".encode()).decode()

def update_event(event_id, description):
    payload = json.dumps({"description": description}).encode("utf-8")
    req = urllib.request.Request(
        f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events/{event_id}",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        method="PUT"
    )
    try:
        resp = urllib.request.urlopen(req)
        return True
    except Exception as e:
        print(f"  Error updating {event_id}: {e}")
        return False

# Map of event_id -> new description
# Marathon pace: 4:16/km | Easy: 5:00-5:30/km | Strides: 3:50-4:00/km
updates = {
    97024784: (  # Easy Run — Comeback
        "REBUILD WEEK 1\n\n"
        "Easy Z2 run. Goal is just to run again.\n"
        "• 30 min easy (HR <140)\n"
        "• Walk breaks are fine if needed\n"
        "• Pay attention to any joint/tendon pain\n"
        "• Target pace: 5:00-5:30/km\n\n"
        "Post-run: 10min stretching (calves, hip flexors, quads)"
    ),
    97024788: (  # Easy Run Mar 11
        "• 35 min easy (HR <140)\n"
        "• Smooth and relaxed\n"
        "• Target pace: 4:50-5:20/km\n"
        "• Focus on cadence ~175-180spm"
    ),
    97024792: (  # Easy Run Mar 13
        "• 35 min easy (HR <140)\n"
        "• Target pace: 4:50-5:20/km\n"
        "• If legs feel heavy from yesterday's ride, slow down — no ego"
    ),
    97024793: (  # Long Run — Week 1
        "First long run back. EASY pace only.\n"
        "• 50 min Z2 (HR <140)\n"
        "• Target pace: 5:00-5:30/km\n"
        "• Practice fueling: take 30g carb at 30min mark\n"
        "• Walk 1min every 15min if needed\n\n"
        "This is about time on feet, not pace."
    ),
    97024797: (  # Easy Run Mar 16
        "• 40 min easy Z2 (HR <140)\n"
        "• Target pace: 4:50-5:20/km\n"
        "• Should feel controlled and comfortable\n"
        "• Check in with calves and Achilles"
    ),
    97024799: (  # Easy Run Mar 18
        "• 40 min easy Z2 (HR <140)\n"
        "• Target pace: 4:50-5:20/km\n"
        "• Focus on relaxed shoulders, quick feet"
    ),
    97024802: (  # Long Run — Week 2
        "Building time on feet.\n"
        "• 65 min Z2 (HR <140)\n"
        "• Target pace: 4:55-5:20/km\n"
        "• Practice fueling: 30g carbs at 30min + 30g at 55min\n"
        "• Flat route if possible\n\n"
        "Distance target: ~13-14km"
    ),
    97024804: (  # Easy Run Mar 23
        "• 40 min easy Z2 (HR <140)\n"
        "• Recovery from yesterday's long run\n"
        "• Easy pace: 4:50-5:20/km"
    ),
    97024806: (  # Marathon Pace Intro
        "FIRST QUALITY SESSION — proceed only if legs feel good.\n\n"
        "• 15 min easy warmup\n"
        "• 3 x 5 min at marathon pace (4:16/km) with 2 min easy jog\n"
        "• 10 min easy cooldown\n\n"
        "Total: ~45 min\n"
        "HR during MP intervals: 150-158 (Z3)\n"
        "If HR drifts above 162, slow down.\n\n"
        "3:00 marathon = 4:16/km. This is the pace you need to own.\n"
        "Practice taking a gel during the warmup."
    ),
    97024809: (  # Easy Run Mar 27
        "• 40 min easy Z2\n"
        "• Shake out from MP session\n"
        "• Easy pace only: 4:50-5:20/km"
    ),
    97024810: (  # Long Run — Week 3
        "Key session. Longest run since December.\n\n"
        "• 80 min total\n"
        "• First 55 min: easy Z2 (4:55-5:20/km)\n"
        "• Last 25 min: marathon pace (4:16/km)\n"
        "• HR cap: 158 in the MP section\n\n"
        "FUELING — practice race nutrition:\n"
        "• Gel/drink at 25min, 50min, 70min (aim for 45-60g carb/hr)\n"
        "• Water every 20min\n\n"
        "Distance target: ~16-17km"
    ),
    97024819: (  # Long Run — Recovery Week
        "Shorter long run — recovery week.\n"
        "• 60 min easy Z2 (5:00-5:30/km)\n"
        "• No marathon pace work today\n"
        "• Practice race-day nutrition: 60g carb/hr\n\n"
        "How do the legs feel after 3 weeks of rebuilding?"
    ),
    97024821: (  # Easy Run Apr 6
        "• 40 min easy Z2\n"
        "• Legs should feel fresh from recovery week\n"
        "• Pace: 4:50-5:15/km"
    ),
    97024822: (  # Marathon Pace Session — KEY
        "KEY SESSION — your most important workout before Boston.\n\n"
        "• 15 min easy warmup\n"
        "• 25 min continuous at marathon pace (4:16/km)\n"
        "• 5 min easy jog\n"
        "• 10 min at marathon pace (4:16/km)\n"
        "• 10 min easy cooldown\n\n"
        "Total: ~65 min\n"
        "HR during MP: 150-158\n\n"
        "FUELING: Take 30g carbs at 20min and 45min.\n"
        "This is a DRESS REHEARSAL — wear race-day shoes and kit.\n"
        "35min at 4:16/km. If this feels controlled, 3:00 is on.\n"
        "If HR drifts above 162, we adjust the race target."
    ),
    97024827: (  # Long Run — FINAL
        "LAST LONG RUN before Boston.\n\n"
        "• 90 min total\n"
        "• First 50 min: easy Z2 (4:55-5:20/km)\n"
        "• 30 min: marathon pace (4:16/km)\n"
        "• Last 10 min: easy cooldown\n\n"
        "FULL RACE NUTRITION REHEARSAL:\n"
        "• 60-90g carb/hr using your race-day products\n"
        "• Same timing as race day\n"
        "• Same breakfast 2.5h before\n\n"
        "Distance target: ~18-19km\n\n"
        "After today: TAPER BEGINS. Trust the work."
    ),
    97024833: (  # Sharpener
        "TAPER RUN 2\n"
        "• 10 min easy warmup\n"
        "• 3 x 3 min at marathon pace (4:16/km) with 2 min jog\n"
        "• 10 min easy cooldown\n\n"
        "Total: ~35 min\n"
        "This reminds your legs what 4:16/km feels like.\n"
        "Should feel EASY — if it doesn't, the race target needs adjusting."
    ),
    97024839: (  # BOSTON MARATHON
        "RACE DAY — BOSTON MARATHON 🏁\n\n"
        "TARGET: 3:00:00 (4:16/km)\n\n"
        "PACING STRATEGY:\n"
        "• Km 1-10: 4:18-4:20/km (bank NOTHING, stay patient)\n"
        "• Km 10-21: 4:16/km (settle into rhythm)\n"
        "• Km 21-25: 4:14-4:16/km (if feeling strong)\n"
        "• Newton Hills km 26-34: effort-based, accept 4:20-4:25\n"
        "• Heartbreak Hill: stay smooth, don't fight it\n"
        "• Km 35-42: empty the tank\n\n"
        "FUELING:\n"
        "• Gel every 25min starting at km 5\n"
        "• Target 60g carb/hr minimum\n"
        "• Water at every aid station\n"
        "• Electrolytes every other station\n\n"
        "HR CEILING: 155bpm through halfway. If above 158 at half, back off.\n"
        "Boston is downhill early — don't let gravity set your pace.\n\n"
        "GO GET IT, SEBASTIEN. 🇫🇷"
    ),
}

print("Updating pace targets for 3:00 marathon (4:16/km)...\n")

for eid, desc in updates.items():
    ok = update_event(eid, desc)
    # Get the event name for confirmation
    if ok:
        print(f"  ✓ Updated event {eid}")
    else:
        print(f"  ✗ Failed event {eid}")

print(f"\n✅ Updated {len(updates)} events with 3:00 marathon pacing.")
