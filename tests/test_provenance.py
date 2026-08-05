"""Tests for the provenance stamped onto history records.

These records are the project's evidence trail, and the trail is only worth anything if a
reader can establish what produced each line. The tests below are therefore about honesty
under failure as much as correctness: a missing answer must come out as null rather than as
a plausible-looking value, because a fabricated provenance field is worse than none.
"""

from __future__ import annotations

from pathlib import Path

from twin import provenance


def test_the_stamp_carries_both_fields_whatever_git_says():
    """A record must never be missing these keys, even where git cannot answer."""
    stamp = provenance.stamp()
    assert set(stamp) == {"commit", "dirty"}


def test_a_digest_is_content_addressed(tmp_path: Path):
    """Same bytes, same digest; different bytes, different digest.

    This is what lets a shifted ranking be attributed to the weights changing rather than
    the estate changing, so it has to hold without anyone remembering to bump a version.
    """
    first = tmp_path / "scoring.yml"
    first.write_text("blast: 0.25\n")
    same = tmp_path / "copy.yml"
    same.write_text("blast: 0.25\n")
    changed = tmp_path / "changed.yml"
    changed.write_text("blast: 0.30\n")

    assert provenance.digest_of(first) == provenance.digest_of(same)
    assert provenance.digest_of(first) != provenance.digest_of(changed)


def test_an_unreadable_file_digests_to_none_rather_than_to_something(tmp_path: Path):
    assert provenance.digest_of(tmp_path / "absent.yml") is None


def test_git_failure_degrades_to_null_rather_than_raising(monkeypatch):
    """An image without git, or a tree exported without .git, must still write a record.

    The failure mode being excluded is a run that dies at the final step, after the estate
    was read, because provenance could not be established. The record is worth writing
    without it; it just has to say so.
    """
    monkeypatch.setattr(provenance, "_git", lambda *args: None)
    assert provenance.commit() is None
    assert provenance.is_dirty() is None
    assert provenance.stamp() == {"commit": None, "dirty": None}


def test_dirty_is_false_not_none_when_the_tree_is_clean(monkeypatch):
    """`None` means unanswerable and `False` means answered. They must not be conflated."""
    monkeypatch.setattr(provenance, "_git", lambda *args: "")
    assert provenance.is_dirty() is False
