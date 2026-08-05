"""What produced a record, stamped onto the record itself.

Twin's history files are append-only and are never regenerated, which is what makes the
fragility trend evidence rather than decoration. But append-only cuts both ways: a line
written in the morning describes the code that was running in the morning, and if the model
changes at lunchtime the file now disagrees with the README and nothing in it explains why.

That happened. Two reads on 2026-08-05 recorded 251 column edges and fingerprint
``1ab1aaacad9403ce``; a change to how column edges are resolved landed an hour later and the
next read produced 322 and ``2b0ff33cd937f51f``. Both numbers were correct when written. A
reader had no way to establish that, because the records said nothing about the code that
produced them, and the README had to explain the discrepancy in prose instead.

So every record carries the commit it was produced by, and whether the tree was dirty at the
time. A dirty tree means the commit does not fully describe the code that ran, which is worth
knowing and worth being unable to hide. Scores additionally carry a digest of the weights
file, because the weights *are* the model: a ranking that moves is either the estate changing
or the weights changing, and those are the two things a trend has to be able to tell apart.

Nothing here fabricates a value. Where git cannot answer, the field is written as null rather
than omitted — an absent field is ambiguous between "not recorded" and "nothing to record",
and a null says plainly that Twin asked and did not get an answer.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str | None:
    """Run a git command in the repository, or return ``None`` if it cannot be run.

    Twin runs inside a container where the repository is bind-mounted, so git is reachable
    but need not be — an image built without it, or a source tree exported without ``.git``,
    are both ordinary situations that must degrade to an honest null rather than an error.
    """
    try:
        finished = subprocess.run(
            ("git", *args),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.strip()


def commit() -> str | None:
    """The commit this run was produced by, short form."""
    return _git("rev-parse", "--short", "HEAD")


def is_dirty() -> bool | None:
    """Whether the working tree carried uncommitted changes when this ran.

    ``None`` means the question could not be answered, which is different from ``False``.
    """
    status = _git("status", "--porcelain")
    if status is None:
        return None
    return bool(status)


def digest_of(path: Path) -> str | None:
    """A short content digest of a config file, or ``None`` if it is not readable.

    Content-addressed rather than a version anyone has to remember to bump. A hand-maintained
    version number is only correct while someone maintains it, and the failure is silent: the
    weights change, the number does not, and two incomparable scores end up labelled the same.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def stamp() -> dict[str, object]:
    """The provenance fields every history record carries."""
    return {"commit": commit(), "dirty": is_dirty()}
