#!/bin/bash
set -euo pipefail

CHROME_BINARY="${JOB_AGENT_CHROME_BINARY:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
CHROME_PORT="${JOB_AGENT_CHROME_PORT:-9223}"
CHROME_PROFILE="${JOB_AGENT_CHROME_PROFILE:-$HOME/chrome-debug-profile}"

"$CHROME_BINARY" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CHROME_PORT" \
  --user-data-dir="$CHROME_PROFILE"
