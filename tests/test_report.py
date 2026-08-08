import json

from twin.report import render


def test_report_is_generated_only_from_available_artifacts(tmp_path):
    history = tmp_path / "examples" / "history"
    examples_reports = tmp_path / "examples" / "reports"
    verification = tmp_path / "examples" / "verification"
    history.mkdir(parents=True)
    examples_reports.mkdir(parents=True)
    verification.mkdir(parents=True)
    (history / "nightly.jsonl").write_text(
        json.dumps(
            {
                "read_at": "2026-08-08T03:17:00+00:00",
                "fingerprint": "abc",
                "tests_passed": 163,
                "pipeline_status": "succeeded",
            }
        )
        + "\n"
    )
    (history / "fragility.jsonl").write_text("")
    (examples_reports / "fragility-scorecard.txt").write_text("score 61.517\n")
    (examples_reports / "estate-graph.txt").write_text("fingerprint abc\n")
    (verification / "merchant_id_nulled_at_source.txt").write_text("precision 0.69\n")

    latest = render(tmp_path)

    assert latest == tmp_path / "reports" / "LATEST.md"
    text = latest.read_text()
    assert "Tests passed: `163`" in text
    assert "Verification precision" not in text
    assert "nightly/2026-08-08/verification.md" in text
    assert "score 61.517" in (
        tmp_path / "reports" / "nightly" / "2026-08-08" / "scorecard.md"
    ).read_text()


def _artifacts(tmp_path, *, read_at="2026-08-08T03:17:00+00:00"):
    history = tmp_path / "examples" / "history"
    examples_reports = tmp_path / "examples" / "reports"
    verification = tmp_path / "examples" / "verification"
    for directory in (history, examples_reports, verification):
        directory.mkdir(parents=True, exist_ok=True)
    (history / "nightly.jsonl").write_text(
        json.dumps({"read_at": read_at, "fingerprint": "abc", "pipeline_status": "succeeded"})
        + "\n"
    )
    (history / "fragility.jsonl").write_text("")
    (examples_reports / "fragility-scorecard.txt").write_text("score 61.517\n")
    (examples_reports / "estate-graph.txt").write_text("fingerprint abc\n")
    (verification / "merchant_id_nulled_at_source.txt").write_text("precision 0.69\n")
    return history


def test_a_failed_attempt_after_the_verified_run_is_named_in_the_report(tmp_path):
    """A success-only history hides a failed night. The report must not."""
    history = _artifacts(tmp_path)
    (history / "attempts.jsonl").write_text(
        json.dumps(
            {
                "attempted_at": "2026-08-09T03:17:01+00:00",
                "status": "failed",
                "stage": "test suite",
                "commit": "5ef4c81",
                "detail": "2 failed",
            }
        )
        + "\n"
    )

    text = render(tmp_path).read_text()

    assert "Runs that did not complete" in text
    assert "1 later nightly run(s) did not complete" in text
    assert "test suite" in text
    assert "5ef4c81" in text


def test_attempts_up_to_the_verified_run_are_not_reported_as_missed(tmp_path):
    """The verified run accounts for itself and for everything before it."""
    history = _artifacts(tmp_path)
    (history / "attempts.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"attempted_at": "2026-08-07T03:17:01+00:00", "status": "failed", "stage": "MCP read"},
                {"attempted_at": "2026-08-08T03:17:00+00:00", "status": "succeeded", "stage": "complete"},
            )
        )
        + "\n"
    )

    text = render(tmp_path).read_text()

    assert "Runs that did not complete" not in text


def test_the_report_renders_without_an_attempts_file(tmp_path):
    _artifacts(tmp_path)

    text = render(tmp_path).read_text()

    assert "Runs that did not complete" not in text
