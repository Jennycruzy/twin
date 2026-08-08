#!/usr/bin/env bash
#
# Alert when the nightly has gone quiet.
#
# ops/nightly-read.sh alerts when it fails. It cannot alert when it never runs — cron not
# firing, a reboot, a dead docker daemon, a renamed script — because the code that would send
# the alert is the code that did not run. Those cases leave exactly the same trace as the
# 2026-08-08 hole: a missing date and nothing else.
#
# So this runs on its own schedule and checks the one thing a healthy nightly always leaves
# behind, a recent attempt record. It runs python directly rather than through docker compose:
# a check on whether the machine is working should not require the stack it is checking to be
# up, or it fails silently for the same reason it exists.
#
# Install on the VPS, a few hours after the nightly:
#   crontab -e
#   0 8 * * * /root/twin/ops/nightly-heartbeat.sh >> /var/log/twin-nightly.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

export HOME="${HOME:-/root}"
export PATH="/opt/homebrew/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

# shellcheck source=/dev/null
[ -f .env ] && set -a && . ./.env && set +a

MAX_AGE_HOURS="${TWIN_HEARTBEAT_MAX_AGE_HOURS:-25}"

say() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }

if OUTPUT="$(python3 -m twin.heartbeat --max-age-hours "$MAX_AGE_HOURS" 2>&1)"; then
  say "$OUTPUT"
  exit 0
fi

say "$OUTPUT"

if [ -n "${TWIN_ALERT_WEBHOOK:-}" ]; then
  PAYLOAD="$(printf '{"text":%s}' \
    "$(printf 'Twin nightly heartbeat: %s' "$OUTPUT" \
      | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
  if curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
      -d "$PAYLOAD" "$TWIN_ALERT_WEBHOOK" >/dev/null 2>&1; then
    say "alert sent"
  else
    say "alert could not be sent; this log is the only signal"
  fi
else
  say "TWIN_ALERT_WEBHOOK is not set; this log is the only signal"
fi

exit 1
