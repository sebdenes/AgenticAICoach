#!/bin/bash
# Fetch planned events/workouts from Intervals.icu
# Usage: ./fetch_events.sh [days_ahead]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../config.env"

DAYS_AHEAD="${1:-3}"
OLDEST=$(date +%Y-%m-%d)
NEWEST=$(date -v+${DAYS_AHEAD}d +%Y-%m-%d 2>/dev/null || date -d "${DAYS_AHEAD} days" +%Y-%m-%d)

curl -s -u "API_KEY:${INTERVALS_API_KEY}" \
  "https://intervals.icu/api/v1/athlete/${INTERVALS_ATHLETE_ID}/events?oldest=${OLDEST}&newest=${NEWEST}" | \
python3 -c "
import json, sys

data = json.load(sys.stdin)

print('=== PLANNED EVENTS (next ${DAYS_AHEAD} days) ===')
print()

if not data:
    print('No planned workouts found.')
else:
    for e in data:
        print(f'{e.get(\"start_date_local\", e.get(\"date\", \"\"))[:10]} | {e.get(\"category\", \"\")} | {e.get(\"name\", \"\")}')
        if e.get('description'):
            print(f'  Description: {e[\"description\"][:100]}')
        if e.get('icu_training_load'):
            print(f'  Planned TSS: {e[\"icu_training_load\"]}')
        print()
"
