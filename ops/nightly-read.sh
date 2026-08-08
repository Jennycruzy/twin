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
# Every configured estate is read, not just the first one. Cross-estate generalisation is one
# of Twin's claims, and a claim proved once by hand and never again is a snapshot, not
# evidence. Each estate keeps its own history under examples/history/<target>/, because
# fragility scores are positions within an estate and combining two estates' numbers into one
# trend would make both meaningless.
#
# A line is written only when a read genuinely succeeded, and the estate is verified first, so
# a broken estate produces no history rather than a line asserting something that was not
# checked. Because that history is success-only, a failed night would otherwise be invisible —
# an absent date reads the same whether nobody ran the job or the job ran and failed. So every
# attempt, successful or not, appends to examples/history/attempts.jsonl, and a failure
# commits and pushes that record on its way out. See twin/attempt.py.
#
# Failures also alert, if TWIN_ALERT_WEBHOOK is set in .env. Recording a miss makes it
# durable; alerting is what makes it noticed on the day rather than whenever somebody next
# looks. Note that this covers a run that failed, not a run that never started — cron not
# firing, or the box being down, leaves no record and sends nothing. That case belongs to
# ops/nightly-heartbeat.sh, which alerts on silence.
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

# Every target with a config, unless the caller narrows it.
TARGETS="${TWIN_NIGHTLY_TARGETS:-$(ls targets/*.yml | xargs -n1 basename | sed 's/\.yml$//' | tr '\n' ' ')}"

ATTEMPTS="examples/history/attempts.jsonl"
RUN_DATE="$(date -u +%Y-%m-%d)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
TMP_DIR="$(mktemp -d)"

say() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }

# shellcheck source=/dev/null
[ -f .env ] && set -a && . ./.env && set +a

# How far the run got, and for which estate. Every stage sets these before it starts, so a
# failure is recorded against the step and the estate that actually failed rather than
# against the script as a whole.
STAGE="startup"
CURRENT_TARGET=""
ATTEMPT_RECORDED=0
# Set only once *this* run has written the artifact. Testing for the file on disk instead
# would let a re-run on a date that already has one attach a previous run's output to this
# run's failure, which is precisely the kind of borrowed evidence these records exist to stop.
VERIFICATION_WRITTEN=0
VERIFICATION_ARTIFACT=""

alert() {
  local text="$1"
  [ -z "${TWIN_ALERT_WEBHOOK:-}" ] && return 0
  curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
    -d "$(printf '{"text":%s}' "$(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
    "$TWIN_ALERT_WEBHOOK" >/dev/null 2>&1 \
    && say "alert sent" \
    || say "alert could not be sent; the miss is still recorded in $ATTEMPTS"
}

record_attempt() {
  local status="$1" detail="$2"
  local artifact=()
  [ "$VERIFICATION_WRITTEN" = "1" ] && artifact=(--verification-artifact "$VERIFICATION_ARTIFACT")
  docker compose run --rm -T twin python -m twin.attempt \
    --history "$ATTEMPTS" --status "$status" --stage "$STAGE" \
    --target "$CURRENT_TARGET" \
    --attempted-at "$STARTED_AT" --detail "$detail" "${artifact[@]}" || {
      say "could not record the attempt; the run outcome is only in this log"
      return 0
    }
}

# A failed night must leave a record saying so. The success-only history cannot: it appends
# nothing, and a reader cannot tell a failed run from a night nobody ran. So the failure is
# recorded, committed, pushed, and alerted on the way out — disclosing the miss is the last
# thing the run does, not something an operator has to remember to do the next morning.
on_exit() {
  local code=$?
  if [ "$code" -ne 0 ] && [ "$ATTEMPT_RECORDED" -eq 0 ]; then
    ATTEMPT_RECORDED=1
    say "nightly failed at stage ${STAGE}${CURRENT_TARGET:+ for ${CURRENT_TARGET}} (exit ${code}); recording the miss"
    record_attempt failed "stage ${STAGE} exited ${code}"
    if [ "$NIGHTLY_AUTOCOMMIT" = "1" ] && ! git diff --quiet -- "$ATTEMPTS"; then
      git config user.name jennycruzy
      git config user.email jennycruzy@users.noreply.github.com
      git add "$ATTEMPTS"
      [ "$VERIFICATION_WRITTEN" = "1" ] && git add "$VERIFICATION_ARTIFACT"
      git commit -q -m "Record the failed nightly attempt for ${RUN_DATE}" && git push -q origin main \
        && say "miss recorded and pushed"
    fi
    alert "Twin nightly FAILED on ${RUN_DATE} at stage '${STAGE}'${CURRENT_TARGET:+ for estate ${CURRENT_TARGET}} (exit ${code}). No history line was written; reports/LATEST.md will name the gap."
  fi
  rm -rf "$TMP_DIR"
}
trap on_exit EXIT

say "starting nightly read for: ${TARGETS}"

# The stack is expected to be up already. Bring up anything that is not, rather than
# assuming: a container restarted by the host would otherwise fail the read at 03:17 with
# nobody watching.
docker compose up -d --wait warehouse datahub-gms datahub-frontend

# The test suite is a property of the repository, not of an estate, so it runs once.
TEST_LOG="$TMP_DIR/pytest.txt"
STAGE="test suite"
say "running the test suite"
# pytest is already quiet via pyproject.toml. Passing -q again becomes -qq and suppresses the
# final "N passed" line that is recorded with the evidence below.
docker compose run --rm -T twin python -m pytest 2>&1 | tee "$TEST_LOG"
TESTS_PASSED="$(sed -nE 's/^([0-9]+) passed.*/\1/p' "$TEST_LOG" | tail -1)"
if [ -z "$TESTS_PASSED" ]; then
  say "could not find the pytest passed count"
  exit 1
fi

run_target() {
  local target="$1"
  CURRENT_TARGET="$target"
  VERIFICATION_WRITTEN=0
  VERIFICATION_ARTIFACT="examples/verification/nightly-${target}-${RUN_DATE}.txt"

  local history="examples/history/${target}/nightly.jsonl"
  local scores="examples/history/${target}/fragility.jsonl"
  local history_lines_before=0 score_lines_before=0
  [ -f "$history" ] && history_lines_before="$(wc -l <"$history")"
  [ -f "$scores" ] && score_lines_before="$(wc -l <"$scores")"
  local scenario
  scenario="$(docker compose run --rm -T twin python -c \
    "from twin.target import load_target; print(load_target('${target}').nightly_scenario)" | tr -d '\r')"
  if [ -z "$scenario" ] || [ "$scenario" = "None" ]; then
    say "target ${target} declares no nightly_scenario; skipping it"
    return 0
  fi

  STAGE="estate verification"
  say "verifying the ${target} estate"
  docker compose run --rm -T twin python -m twin.target verify --target "$target"

  STAGE="source-column verification"
  say "running the ${target} source-column verification"
  local tmp="$TMP_DIR/verification-${target}.txt"
  {
    printf '# nightly source-column verification\n# generated by ops/nightly-read.sh at %s from commit %s\n# source: make run TARGET=%s SCENARIO=%s\n\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse --short HEAD)" "$target" "$scenario"
    docker compose run --rm -T twin python -m twin.run --target "$target" "$scenario"
  } 2>&1 | tee "$tmp"
  mv "$tmp" "$VERIFICATION_ARTIFACT"
  VERIFICATION_WRITTEN=1

  local precision recall
  read -r precision recall < <(
    sed -nE 's/.*predicted [0-9]+ .* precision ([0-9.]+)   recall ([0-9.]+).*/\1 \2/p' \
      "$VERIFICATION_ARTIFACT" | tail -1
  )
  if [ -z "${precision:-}" ] || [ -z "${recall:-}" ]; then
    say "could not find precision and recall in the actual ${target} verification result"
    exit 1
  fi

  STAGE="MCP read"
  say "reading the ${target} estate over MCP"
  docker compose run --rm -T twin python -m twin.read --target "$target"

  STAGE="fragility scoring"
  say "scoring ${target} fragility"
  docker compose run --rm -T twin python -m twin.score --target "$target" --append-history "$scores"

  STAGE="DataHub write-back"
  say "writing ${target} fragility scores back to DataHub"
  docker compose run --rm -T twin python -m twin.write --target "$target"

  STAGE="history append"
  say "recording the successful ${target} run"
  docker compose run --rm -T twin python -m twin.read --target "$target" --cached \
    --append-history "$history" \
    --tests-passed "$TESTS_PASSED" \
    --verification-precision "$precision" \
    --verification-recall "$recall" \
    --pipeline-status succeeded \
    --verification-artifact "$VERIFICATION_ARTIFACT"

  local history_lines_after score_lines_after
  history_lines_after="$(wc -l <"$history")"
  score_lines_after="$(wc -l <"$scores")"
  if [ "$history_lines_after" -ne "$((history_lines_before + 1))" ] || \
     [ "$score_lines_after" -ne "$((score_lines_before + 1))" ]; then
    say "${target} did not append exactly one read and score history line"
    exit 1
  fi

  STAGE="complete"
  record_attempt succeeded "read, scored, wrote back, and appended history"
  COMMITTABLE="$COMMITTABLE $history $scores $VERIFICATION_ARTIFACT"
}

COMMITTABLE=""
for target in $TARGETS; do
  run_target "$target"
done

CURRENT_TARGET=""
STAGE="report render"
say "rendering the judge-facing report"
make --no-print-directory report

ATTEMPT_RECORDED=1

if [ "$NIGHTLY_AUTOCOMMIT" = "0" ]; then
  say "evidence generated; commit reports/, examples/history/ and examples/verification/ when reviewed"
  exit 0
fi

git config user.name jennycruzy
git config user.email jennycruzy@users.noreply.github.com
# shellcheck disable=SC2086
git add $COMMITTABLE "$ATTEMPTS" reports
git commit -q -m "Record the nightly estate read for $(date -u +%Y-%m-%d)"
git push -q origin main

say "recorded and pushed"
