#!/bin/bash
# Send a message via Telegram Bot API
# Usage: ./send_telegram.sh "Your message here"
# Or pipe: echo "message" | ./send_telegram.sh -
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../config.env"

if [ "${1:-}" = "-" ]; then
    MESSAGE=$(cat)
else
    MESSAGE="${1:?Usage: send_telegram.sh \"message\"}"
fi

python3 -c "
import json, urllib.request, sys

message = sys.stdin.read()
payload = json.dumps({
    'chat_id': '${TELEGRAM_CHAT_ID}',
    'text': message,
    'parse_mode': 'Markdown'
}).encode('utf-8')

req = urllib.request.Request(
    'https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage',
    data=payload,
    headers={'Content-Type': 'application/json'}
)
try:
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    if result.get('ok'):
        print('Message sent successfully')
    else:
        print(f'Error: {result}', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f'Failed to send: {e}', file=sys.stderr)
    sys.exit(1)
" <<< "$MESSAGE"
