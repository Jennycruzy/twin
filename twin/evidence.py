"""Where each estate's evidence lives.

Twin scores more than one estate, and the two are not comparable: a fragility score is a
position within an estate, so 61.517 in commerce and 43.789 in operations describe different
scales. History files inherited from the single-estate version of Twin carried no target
field and lived at one path, which meant a second estate could only be added by interleaving
its lines with the first. A trend file whose ``datasets`` column alternates 66, 25, 66, 25 is
not a trend, and the determinism argument — an unchanged fingerprint across nights — becomes
unreadable in exactly the same move.

So evidence is partitioned by target, one directory per estate, and every record additionally
names the target it came from. The directory is what a reader navigates; the field is what
survives a file being moved or two files being concatenated. Neither is load-bearing alone.

Attempt records are the exception: they describe the nightly job rather than an estate, and a
run that dies before it selects a target still has to be recordable. They stay in one file and
name their target where they have one.
"""

from __future__ import annotations

from pathlib import Path

HISTORY = Path("examples/history")
CAPTURED = Path("examples/reports")
VERIFICATION = Path("examples/verification")


def history_dir(target: str, root: Path = Path(".")) -> Path:
    """The directory holding one estate's append-only history."""
    return root / HISTORY / target


def nightly_history(target: str, root: Path = Path(".")) -> Path:
    return history_dir(target, root) / "nightly.jsonl"


def fragility_history(target: str, root: Path = Path(".")) -> Path:
    return history_dir(target, root) / "fragility.jsonl"


def attempts_history(root: Path = Path(".")) -> Path:
    """Every nightly attempt across every target, in one file."""
    return root / HISTORY / "attempts.jsonl"


def captured_dir(target: str, root: Path = Path(".")) -> Path:
    """The directory holding one estate's captured command output."""
    return root / CAPTURED / target


def verification_artifact(target: str, run_stamp: str, root: Path = Path(".")) -> Path:
    """The nightly verification capture for one run of one estate.

    Keyed on the run, not the date. A date-only name means a second run on the same day
    silently rewrites the first one's output — including an artifact that a committed history
    line already points at, which would leave that line asserting a precision the file it
    references no longer shows. Naming the file after the run that produced it makes the
    collision impossible rather than merely unlikely.
    """
    return root / VERIFICATION / f"nightly-{target}-{run_stamp}.txt"


def verification_artifacts_on(target: str, date: str, root: Path = Path(".")) -> list[Path]:
    """Every nightly capture for one estate on one date, oldest name first.

    Matches both the run-stamped names and the date-only names written before this was keyed
    on the run: an artifact that was valid evidence when it was captured stays valid evidence
    after the naming rule changes.
    """
    directory = root / VERIFICATION
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"nightly-{target}-{date}*.txt"))


def known_targets(root: Path = Path(".")) -> list[str]:
    """Every target with a history directory, in a stable order.

    Discovered from what is on disk rather than from ``targets/*.yml``: the report describes
    runs that happened, and a target that has been configured but never run has nothing to
    report. A configured target with no history is not an error here — it is a target whose
    first nightly has not landed yet.
    """
    directory = root / HISTORY
    if not directory.is_dir():
        return []
    return sorted(
        child.name
        for child in directory.iterdir()
        if child.is_dir() and (child / "nightly.jsonl").exists()
    )
