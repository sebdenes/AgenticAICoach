#!/bin/bash
# Fetch wellness data (sleep, HRV, RHR, CTL/ATL/TSB) from Intervals.icu
# Usage: ./fetch_wellness.sh [days_back]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../config.env"

DAYS_BACK="${1:-14}"
OLDEST=$(date -v-${DAYS_BACK}d +%Y-%m-%d 2>/dev/null || date -d "${DAYS_BACK} days ago" +%Y-%m-%d)
NEWEST=$(date +%Y-%m-%d)

curl -s -u "API_KEY:${INTERVALS_API_KEY}" \
  "https://intervals.icu/api/v1/athlete/${INTERVALS_ATHLETE_ID}/wellness?oldest=${OLDEST}&newest=${NEWEST}" | \
python3 -c "
import json, sys

data = json.load(sys.stdin)

print('=== WELLNESS DATA ===')
print(f'Period: ${OLDEST} to ${NEWEST}')
print()

# Summary stats
sleep_hours = []
hrvs = []
rhrs = []
ctls = []

for d in data:
    sleep_s = d.get('sleepSecs', 0) or 0
    sleep_h = sleep_s / 3600
    hrv = d.get('hrv')
    rhr = d.get('restingHR')
    ctl = d.get('ctl', 0)
    atl = d.get('atl', 0)
    sleep_score = d.get('sleepScore', 'N/A')
    steps = d.get('steps', 'N/A')

    if sleep_h > 0:
        sleep_hours.append(sleep_h)
    if hrv:
        hrvs.append(hrv)
    if rhr:
        rhrs.append(rhr)
    ctls.append(ctl)

    tsb = ctl - atl
    sleep_str = f'{sleep_h:.1f}h' if sleep_h > 0 else 'N/A'
    hrv_str = f'{hrv:.1f}' if hrv else 'N/A'
    rhr_str = str(rhr) if rhr else 'N/A'

    print(f'{d[\"id\"]} | CTL:{ctl:.1f} ATL:{atl:.1f} TSB:{tsb:.1f} | RHR:{rhr_str} HRV:{hrv_str} | Sleep:{sleep_str} Score:{sleep_score} | Steps:{steps}')

print()
print('=== SUMMARY ===')
if sleep_hours:
    print(f'Sleep avg: {sum(sleep_hours)/len(sleep_hours):.1f}h | Min: {min(sleep_hours):.1f}h | Max: {max(sleep_hours):.1f}h')
    target = 7.5
    debt = sum(max(0, target - h) for h in sleep_hours[-7:])
    print(f'7-day sleep debt vs {target}h target: {debt:.1f}h')
if hrvs:
    print(f'HRV avg: {sum(hrvs)/len(hrvs):.1f} | Min: {min(hrvs):.1f} | Max: {max(hrvs):.1f} | Latest: {hrvs[-1]:.1f}')
if rhrs:
    print(f'RHR avg: {sum(rhrs)/len(rhrs):.0f} | Min: {min(rhrs)} | Max: {max(rhrs)} | Latest: {rhrs[-1]}')
if ctls:
    print(f'CTL current: {ctls[-1]:.1f} | 14d ago: {ctls[0]:.1f} | Trend: {\"rising\" if ctls[-1] > ctls[0] else \"declining\"}')
"
