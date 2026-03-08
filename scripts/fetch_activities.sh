#!/bin/bash
# Fetch recent activities from Intervals.icu
# Usage: ./fetch_activities.sh [days_back]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../config.env"

DAYS_BACK="${1:-7}"
OLDEST=$(date -v-${DAYS_BACK}d +%Y-%m-%d 2>/dev/null || date -d "${DAYS_BACK} days ago" +%Y-%m-%d)
NEWEST=$(date +%Y-%m-%d)

curl -s -u "API_KEY:${INTERVALS_API_KEY}" \
  "https://intervals.icu/api/v1/athlete/${INTERVALS_ATHLETE_ID}/activities?oldest=${OLDEST}&newest=${NEWEST}" | \
python3 -c "
import json, sys

data = json.load(sys.stdin)

print('=== ACTIVITIES (last ${DAYS_BACK} days) ===')
print()

total_tss = 0
total_time = 0
activity_count = 0

for a in data:
    if not a.get('type'):
        continue
    activity_count += 1
    dur_min = (a.get('moving_time', 0) or 0) // 60
    tss = a.get('icu_training_load', 0) or 0
    total_tss += tss
    total_time += dur_min
    intensity = a.get('icu_intensity', 0) or 0
    avg_hr = a.get('average_heartrate', 'N/A')
    max_hr = a.get('max_heartrate', 'N/A')
    avg_watts = a.get('average_watts', 'N/A')
    np = a.get('icu_weighted_avg_watts', 'N/A')
    dist_km = (a.get('distance', 0) or 0) / 1000

    print(f'{a.get(\"start_date_local\", \"\")[:10]} | {a.get(\"type\", \"\")} | {a.get(\"name\", \"\")[:55]}')
    print(f'  Duration: {dur_min}min | Distance: {dist_km:.1f}km | TSS: {tss} | IF: {intensity:.0f}%')
    print(f'  AvgHR: {avg_hr} | MaxHR: {max_hr} | AvgPower: {avg_watts} | NP: {np}')
    print()

print(f'=== TOTALS: {activity_count} activities | {total_time}min | {total_tss:.0f} TSS ===')
"
