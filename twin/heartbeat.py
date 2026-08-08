"""Alert on silence, not only on failure: ``python -m twin.heartbeat``.

A failed nightly now records and pushes its own miss, and alerts while doing it. That covers
every way a run can fail *while running*. It covers none of the ways a run can fail to happen:
cron not firing, the box rebooting, docker being down, the script being renamed. In each of
those the trail looks exactly like the hole this whole mechanism exists to close — a missing
date and nothing else — because the code that would have reported the problem never ran.

Nothing that runs inside the nightly can detect the nightly not running. So this check runs on
its own schedule and asserts the one thing a healthy system always produces: a recent attempt.
If the newest record in ``attempts.jsonl`` is older than the tolerated age, it exits non-zero
and says so, whether the cause was a failure, a crash, or a job that never started.

It deliberately reads only the attempts file. Reading the estate, or the catalog, would make
this depend on the same stack whose health it is supposed to report on independently.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Iterable

from twin import evidence

# A nightly runs daily, so anything under a day is normal and a little over a day is a late
# or slow run rather than a missing one. Beyond that, a night has been skipped.
DEFAULT_MAX_AGE_HOURS = 25.0


def newest_attempt(history: Path) -> dict | None:
    """The most recent attempt record, or ``None`` if there are none to read."""
    if not history.exists():
        return None
    newest: dict | None = None
    newest_at: dt.datetime | None = None
    for line in history.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        try:
            at = dt.datetime.fromisoformat(str(record.get("attempted_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if newest_at is None or at > newest_at:
            newest, newest_at = record, at
    return newest


def check(
    history: Path,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    now: dt.datetime | None = None,
) -> tuple[bool, str]:
    """Return whether the nightly is alive, and a line explaining the verdict."""
    now = now or dt.datetime.now(dt.timezone.utc)
    record = newest_attempt(history)
    if record is None:
        return False, f"no nightly attempt has ever been recorded in {history}"

    attempted_at = dt.datetime.fromisoformat(str(record["attempted_at"]).replace("Z", "+00:00"))
    age_hours = (now - attempted_at).total_seconds() / 3600
    where = f"{record.get('stage') or 'unknown stage'}"
    if record.get("target"):
        where += f" for {record['target']}"

    if age_hours > max_age_hours:
        return False, (
            f"the last nightly attempt was {age_hours:.1f}h ago at {record['attempted_at']} "
            f"({record.get('status', 'unknown')} at {where}); "
            f"nothing has run in the last {max_age_hours:g}h"
        )
    if record.get("status") != "succeeded":
        return False, (
            f"the last nightly attempt {age_hours:.1f}h ago failed at {where} "
            f"({record.get('detail') or 'no detail recorded'})"
        )
    return True, (
        f"the last nightly attempt was {age_hours:.1f}h ago and succeeded at {where}"
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=evidence.attempts_history())
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help="how old the newest attempt may be before this is a problem",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    alive, message = check(args.history, args.max_age_hours)
    print(("ok: " if alive else "STALE: ") + message)
    return 0 if alive else 1


if __name__ == "__main__":
    raise SystemExit(main())
