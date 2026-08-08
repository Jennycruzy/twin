import json

import pytest

from twin.attempt import record


def test_an_attempt_record_carries_how_far_the_run_got(tmp_path):
    history = tmp_path / "attempts.jsonl"

    entry = record(history, status="failed", stage="test suite", detail="2 failed")

    assert entry["status"] == "failed"
    assert entry["stage"] == "test suite"
    assert "commit" in entry and "dirty" in entry
    assert json.loads(history.read_text().splitlines()[0])["detail"] == "2 failed"


def test_attempts_append_rather_than_replace(tmp_path):
    """The value of the file is that a failure cannot be edited out of it later."""
    history = tmp_path / "attempts.jsonl"

    record(history, status="failed", stage="MCP read")
    record(history, status="succeeded", stage="complete")

    statuses = [json.loads(line)["status"] for line in history.read_text().splitlines()]
    assert statuses == ["failed", "succeeded"]


def test_an_attempt_carries_no_numbers_from_a_run_that_failed(tmp_path):
    """A failed run has no evidence to contribute; the record must not imply otherwise."""
    history = tmp_path / "attempts.jsonl"

    entry = record(history, status="failed", stage="test suite")

    for field in ("fingerprint", "assets", "tests_passed", "verification_precision"):
        assert field not in entry


def test_a_reconstructed_record_says_where_it_came_from(tmp_path):
    history = tmp_path / "attempts.jsonl"

    entry = record(
        history, status="failed", stage="test suite", reconstructed_from="/var/log/twin-nightly.log"
    )

    assert entry["reconstructed_from"] == "/var/log/twin-nightly.log"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "maybe", "stage": "test suite"},
        {"status": "failed", "stage": "  "},
        {"status": "failed", "stage": "test suite", "attempted_at": "last tuesday"},
    ],
)
def test_an_unusable_attempt_record_is_refused(tmp_path, kwargs):
    history = tmp_path / "attempts.jsonl"

    with pytest.raises(ValueError):
        record(history, **kwargs)

    assert not history.exists()
