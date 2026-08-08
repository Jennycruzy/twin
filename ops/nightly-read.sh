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
# Because that history is success-only, a failed night would otherwise be invisible — an
# absent date reads the same whether nobody ran the job or the job ran and failed. So every
# attempt, successful or not, also appends to examples/history/attempts.jsonl, and a failure
# commits and pushes that record on its way out. See twin/attempt.py.
#
# Direct invocation is the VPS cron mode: it commits and pushes after a successful run.
# `make nightly` sets NIGHTLY_AUTOCOMMIT=0 so a Mac or another operator can inspect the
# generated evidence and commit it with their normal Git credentials.
#
# Install on the VPS:
#   crontab -e
#   17 3 * * * /root/twin/ops/nightly-read.sh >> /var/log/twin-nightly.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

# cron runs with a minimal environment. git needs HOME to find the credential helper that
# gh installed, and docker compose needs a PATH that includes it.
export HOME="${HOME:-/root}"
export PATH="/opt/homebrew/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

NIGHTLY_AUTOCOMMIT="${NIGHTLY_AUTOCOMMIT:-1}"
if [ "$NIGHTLY_AUTOCOMMIT" != "0" ] && [ "$NIGHTLY_AUTOCOMMIT" != "1" ]; then
  echo "NIGHTLY_AUTOCOMMIT must be 0 or 1" >&2
  exit 2
fi

HISTORY="examples/history/nightly.jsonl"
SCORES="examples/history/fragility.jsonl"
ATTEMPTS="examples/history/attempts.jsonl"
RUN_DATE="$(date -u +%Y-%m-%d)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
VERIFICATION_ARTIFACT="examples/verification/nightly-${RUN_DATE}.txt"
TMP_DIR="$(mktemp -d)"

say() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }

# How far the run got. Every stage sets this before it starts, so a failure anywhere is
# recorded against the step that actually failed rather than against the script as a whole.
STAGE="startup"
ATTEMPT_RECORDED=0
# Set only once *this* run has written the artifact. Testing for the file on disk instead
# would let a re-run on a date that already has one attach a previous run's output to this
# run's failure, which is precisely the kind of borrowed evidence these records exist to stop.
VERIFICATION_WRITTEN=0

record_attempt() {
  local status="$1" detail="$2"
  ATTEMPT_RECORDED=1
  local artifact=()
  [ "$VERIFICATION_WRITTEN" = "1" ] && artifact=(--verification-artifact "$VERIFICATION_ARTIFACT")
  docker compose run --rm -T twin python -m twin.attempt \
    --history "$ATTEMPTS" --status "$status" --stage "$STAGE" \
    --attempted-at "$STARTED_AT" --detail "$detail" "${artifact[@]}" || {
      say "could not record the attempt; the run outcome is only in this log"
      return 0
    }
}

# A failed night must leave a record saying so. The success-only history cannot: it appends
# nothing, and a reader cannot tell a failed run from a night nobody ran. So the failure is
# recorded, committed, and pushed on the way out — disclosing the miss is the last thing the
# run does, not something an operator has to remember to do the next morning.
on_exit() {
  local code=$?
  if [ "$code" -ne 0 ] && [ "$ATTEMPT_RECORDED" -eq 0 ]; then
    say "nightly failed at stage ${STAGE} (exit ${code}); recording the miss"
    record_attempt failed "stage ${STAGE} exited ${code}"
    if [ "$NIGHTLY_AUTOCOMMIT" = "1" ] && ! git diff --quiet -- "$ATTEMPTS"; then
      git config user.name jennycruzy
      git config user.email jennycruzy@users.noreply.github.com
      git add "$ATTEMPTS"
      [ "$VERIFICATION_WRITTEN" = "1" ] && git add "$VERIFICATION_ARTIFACT"
      git commit -q -m "Record the failed nightly attempt for ${RUN_DATE}" && git push -q origin main \
        && say "miss recorded and pushed"
    fi
  fi
  rm -rf "$TMP_DIR"
}
trap on_exit EXIT

say "starting nightly read"

# The stack is expected to be up already. Bring up anything that is not, rather than
# assuming: a container restarted by the host would otherwise fail the read at 03:17 with
# nobody watching.
docker compose up -d --wait warehouse datahub-gms datahub-frontend

STAGE="estate verification"
say "verifying the estate"
docker compose run --rm -T twin python -m estate.verify_estate

STAGE="source-column verification"
say "running the source-column verification"
VERIFICATION_TMP="$TMP_DIR/verification.txt"
{
  printf '# nightly source-column verification\n# generated by ops/nightly-read.sh at %s from commit %s\n# source: make run TARGET=commerce SCENARIO=scenarios/merchant_id_nulled_at_source.yml\n\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse --short HEAD)"
  docker compose run --rm -T twin python -m twin.run --target commerce \
    scenarios/merchant_id_nulled_at_source.yml
} 2>&1 | tee "$VERIFICATION_TMP"
mv "$VERIFICATION_TMP" "$VERIFICATION_ARTIFACT"
VERIFICATION_WRITTEN=1

TEST_LOG="$TMP_DIR/pytest.txt"
STAGE="test suite"
say "running the test suite"
docker compose run --rm -T twin python -m pytest -q 2>&1 | tee "$TEST_LOG"
TESTS_PASSED="$(sed -nE 's/.*([0-9]+) passed.*/\1/p' "$TEST_LOG" | tail -1)"
if [ -z "$TESTS_PASSED" ]; then
  say "could not find the pytest passed count"
  exit 1
fi

read -r VERIFICATION_PRECISION VERIFICATION_RECALL < <(
  sed -nE 's/.*predicted [0-9]+ .* precision ([0-9.]+)   recall ([0-9.]+).*/\1 \2/p' \
    "$VERIFICATION_ARTIFACT" | tail -1
)
if [ -z "${VERIFICATION_PRECISION:-}" ] || [ -z "${VERIFICATION_RECALL:-}" ]; then
  say "could not find precision and recall in the actual verification result"
  exit 1
fi

STAGE="MCP read"
say "reading the estate over MCP"
docker compose run --rm -T twin python -m twin.read

STAGE="fragility scoring"
say "scoring fragility"
docker compose run --rm -T twin python -m twin.score --append-history "$SCORES"

STAGE="DataHub write-back"
say "writing fragility scores back to DataHub"
docker compose run --rm -T twin python -m twin.write

STAGE="history append"
say "recording the successful nightly run"
docker compose run --rm -T twin python -m twin.read --target commerce --cached \
  --append-history "$HISTORY" \
  --tests-passed "$TESTS_PASSED" \
  --verification-precision "$VERIFICATION_PRECISION" \
  --verification-recall "$VERIFICATION_RECALL" \
  --pipeline-status succeeded \
  --verification-artifact "$VERIFICATION_ARTIFACT"

STAGE="report render"
say "rendering the judge-facing report"
make --no-print-directory report

if git diff --quiet -- "$HISTORY" "$SCORES"; then
  say "no new history line; the read must have failed"
  exit 1
fi

STAGE="complete"
record_attempt succeeded "read, scored, wrote back, and appended history"

if [ "$NIGHTLY_AUTOCOMMIT" = "0" ]; then
  say "evidence generated; commit reports/, examples/history/ and examples/verification/ when reviewed"
  exit 0
fi

git config user.name jennycruzy
git config user.email jennycruzy@users.noreply.github.com
git add "$HISTORY" "$SCORES" "$ATTEMPTS" "$VERIFICATION_ARTIFACT" reports
git commit -q -m "Record the nightly estate read for $(date -u +%Y-%m-%d)"
git push -q origin main

say "recorded and pushed"
