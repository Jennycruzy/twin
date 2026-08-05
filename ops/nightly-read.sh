#!/usr/bin/env bash
#
# The nightly read, run from a host cron rather than from CI.
#
# Twin's fragility trend is a claim about change over time, and it is only worth anything if
# the runs actually happened — accumulated history cannot be manufactured later. The GitHub
# Actions workflow in .github/workflows/twin-nightly.yml does this job on a runner that
# builds the estate from nothing. This script does the same job against a host where the
# stack is already up, which is the right shape for a machine that runs Twin continuously.
#
# Either path appends to the same append-only file. A line is written only when a read
# genuinely succeeded, and the estate is verified first, so a broken estate produces no
# history rather than a line asserting something that was not checked.
#
# Install:
#   crontab -e
#   17 3 * * * /root/twin/ops/nightly-read.sh >> /var/log/twin-nightly.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

# cron runs with a minimal environment. git needs HOME to find the credential helper that
# gh installed, and docker compose needs a PATH that includes it.
export HOME="${HOME:-/root}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

HISTORY="examples/history/nightly.jsonl"
SCORES="examples/history/fragility.jsonl"

say() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }

say "starting nightly read"

# The stack is expected to be up already. Bring up anything that is not, rather than
# assuming: a container restarted by the host would otherwise fail the read at 03:17 with
# nobody watching.
docker compose up -d --wait warehouse datahub-gms datahub-frontend

say "verifying the estate"
docker compose run --rm -T twin python -m estate.verify_estate

say "reading the estate over MCP"
docker compose run --rm -T twin python -m twin.read --append-history "$HISTORY"

say "scoring fragility"
docker compose run --rm -T twin python -m twin.score --append-history "$SCORES"

if git diff --quiet -- "$HISTORY" "$SCORES"; then
  say "no new history line; the read must have failed"
  exit 1
fi

git config user.name jennycruzy
git config user.email jennycruzy@users.noreply.github.com
git add "$HISTORY" "$SCORES"
git commit -q -m "Record the nightly estate read for $(date -u +%Y-%m-%d)"
git push -q origin main

say "recorded and pushed"
