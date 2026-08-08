"""Every nightly attempt, including the ones that failed.

``nightly.jsonl`` is append-only and success-only: a line is written after a read genuinely
succeeded, so a broken estate leaves no row rather than a row asserting something that was
never checked. That is the right rule for an evidence file, and it is not enough on its own.

A success-only record cannot distinguish "no run was attempted" from "a run was attempted and
failed". Both look identical — a missing date — and the second is the one a reader needs to
know about, because it means the newest verified numbers are older than they appear. On
2026-08-08 the nightly failed at the test suite and left exactly that hole: the trail showed
2026-08-07 as the latest run and said nothing about the ten hours since.

So this file records the attempt itself. It never carries estate numbers, scores, or a
verdict — a failed run has no numbers worth recording, and the whole point is that it does not
get to contribute any. It carries when the run started, how far it got, and the commit it ran
from. ``reports/LATEST.md`` reads it and names any failure newer than the latest verified
read, so the gap is stated on the page rather than inferred from a date that is missing.

Records marked ``reconstructed_from`` were written after the fact from an operator log rather
than by the run itself. That is weaker evidence than a line a run wrote about itself, and it
says so in the record instead of being indistinguishable from one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

from twin import evidence, provenance

STATUSES = ("succeeded", "failed")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def record(
    history: Path,
    *,
    status: str,
    stage: str,
    target: str | None = None,
    detail: str | None = None,
    attempted_at: str | None = None,
    verification_artifact: str | None = None,
    reconstructed_from: str | None = None,
) -> dict[str, Any]:
    """Append one attempt record and return it."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if not stage or not stage.strip():
        raise ValueError("stage is required: an attempt record must say how far the run got")
    if attempted_at is not None:
        try:
            dt.datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"attempted_at is not an ISO timestamp: {attempted_at}") from exc

    entry: dict[str, Any] = {
        "attempted_at": attempted_at or _now(),
        "status": status,
        "stage": stage.strip(),
        "target": target,
        "detail": detail,
        "verification_artifact": verification_artifact,
        **provenance.stamp(),
    }
    if reconstructed_from is not None:
        entry["reconstructed_from"] = reconstructed_from

    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append a nightly attempt record.")
    parser.add_argument("--history", type=Path, default=evidence.attempts_history())
    parser.add_argument("--status", required=True, choices=STATUSES)
    parser.add_argument("--stage", required=True, help="how far the run got")
    parser.add_argument("--target", help="estate this attempt was for, if it had chosen one")
    parser.add_argument("--detail", help="what happened, in one line")
    parser.add_argument("--attempted-at", help="ISO timestamp; defaults to now")
    parser.add_argument("--verification-artifact", help="path to output this attempt produced")
    parser.add_argument("--reconstructed-from", help="log this record was recovered from")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        entry = record(
            args.history,
            status=args.status,
            stage=args.stage,
            target=args.target or None,
            detail=args.detail,
            attempted_at=args.attempted_at,
            verification_artifact=args.verification_artifact,
            reconstructed_from=args.reconstructed_from,
        )
    except (OSError, ValueError) as exc:
        print(f"cannot record attempt: {exc}")
        return 1
    print(f"{entry['status']} at stage {entry['stage']} -> {args.history}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
