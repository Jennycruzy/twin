import json

from twin.report import render


def test_report_is_generated_only_from_available_artifacts(tmp_path):
    history = tmp_path / "examples" / "history" / "commerce"
    examples_reports = tmp_path / "examples" / "reports" / "commerce"
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
                "target": "commerce",
            }
        )
        + "\n"
    )
    (history / "fragility.jsonl").write_text("")
    (examples_reports / "fragility-scorecard.txt").write_text("score 61.517\n")
    (examples_reports / "estate-graph.txt").write_text("fingerprint abc\n")
    (verification / "nightly-commerce-2026-08-08.txt").write_text("precision 0.69\n")

    latest = render(tmp_path)

    assert latest == tmp_path / "reports" / "LATEST.md"
    text = latest.read_text()
    assert "Tests passed: `163`" in text
    assert "Verification precision" not in text
    assert "nightly/2026-08-08/commerce/verification.md" in text
    assert "score 61.517" in (
        tmp_path / "reports" / "nightly" / "2026-08-08" / "commerce" / "scorecard.md"
    ).read_text()


def _artifacts(tmp_path, *, target="commerce", read_at="2026-08-08T03:17:00+00:00"):
    history = tmp_path / "examples" / "history" / target
    examples_reports = tmp_path / "examples" / "reports" / target
    verification = tmp_path / "examples" / "verification"
    for directory in (history, examples_reports, verification):
        directory.mkdir(parents=True, exist_ok=True)
    (history / "nightly.jsonl").write_text(
        json.dumps(
            {
                "read_at": read_at,
                "fingerprint": "abc",
                "pipeline_status": "succeeded",
                "target": target,
            }
        )
        + "\n"
    )
    (history / "fragility.jsonl").write_text("")
    (examples_reports / "fragility-scorecard.txt").write_text("score 61.517\n")
    (examples_reports / "estate-graph.txt").write_text("fingerprint abc\n")
    date = read_at[:10]
    (verification / f"nightly-{target}-{date}.txt").write_text("precision 0.69\n")
    return history


def test_a_failed_attempt_after_the_verified_run_is_named_in_the_report(tmp_path):
    """A success-only history hides a failed night. The report must not."""
    history = _artifacts(tmp_path)
    (history.parent / "attempts.jsonl").write_text(
        json.dumps(
            {
                "attempted_at": "2026-08-09T03:17:01+00:00",
                "status": "failed",
                "stage": "test suite",
                "commit": "5ef4c81",
                "detail": "2 failed",
                "target": "commerce",
            }
        )
        + "\n"
    )

    text = render(tmp_path).read_text()

    assert "Runs that did not complete" in text
    assert "1 later commerce run(s) did not complete" in text
    assert "test suite" in text
    assert "5ef4c81" in text


def test_attempts_up_to_the_verified_run_are_not_reported_as_missed(tmp_path):
    """The verified run accounts for itself and for everything before it."""
    history = _artifacts(tmp_path)
    (history.parent / "attempts.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                    {"attempted_at": "2026-08-07T03:17:01+00:00", "status": "failed", "stage": "MCP read", "target": "commerce"},
                    {"attempted_at": "2026-08-08T03:17:00+00:00", "status": "succeeded", "stage": "complete", "target": "commerce"},
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


def test_two_estates_render_separately_and_failures_stay_with_their_estate(tmp_path):
    commerce = _artifacts(tmp_path, target="commerce")
    _artifacts(tmp_path, target="operations")
    (commerce.parent / "attempts.jsonl").write_text(
        json.dumps(
            {
                "attempted_at": "2026-08-09T03:17:01+00:00",
                "status": "failed",
                "stage": "MCP read",
                "target": "operations",
            }
        )
        + "\n"
    )

    text = render(tmp_path).read_text()

    assert "## commerce" in text
    assert "## operations" in text
    assert "later commerce run(s) did not complete" not in text
    assert "1 later operations run(s) did not complete" in text
    assert "| 2026-08-09T03:17:01+00:00 | operations | MCP read |" in text


def test_two_runs_on_one_date_keep_separate_artifacts(tmp_path):
    """A second run must not be able to overwrite the first run's evidence.

    Naming a capture after its date alone meant a re-run silently replaced an artifact that a
    committed history line already pointed at, leaving that line asserting a precision the
    file it referenced no longer showed.
    """
    _artifacts(tmp_path, read_at="2026-08-08T03:17:00+00:00")
    verification = tmp_path / "examples" / "verification"

    morning = verification / "nightly-commerce-2026-08-08T03-17-00.txt"
    morning.write_text("# generated by ops/nightly-read.sh at 2026-08-08T03:17:00Z\nprecision 0.69\n")
    afternoon = verification / "nightly-commerce-2026-08-08T14-31-52.txt"
    afternoon.write_text("# generated by ops/nightly-read.sh at 2026-08-08T14:31:52Z\nprecision 1.00\n")

    render(tmp_path)

    assert morning.read_text().endswith("precision 0.69\n")
    assert afternoon.read_text().endswith("precision 1.00\n")
    body = (tmp_path / "reports" / "nightly" / "2026-08-08" / "commerce" / "verification.md").read_text()
    assert "precision 1.00" in body, "the newest capture on the date should be reported"


def test_a_date_only_artifact_written_before_the_rename_is_still_found(tmp_path):
    """Evidence that was valid when captured stays valid after the naming rule changes."""
    _artifacts(tmp_path, read_at="2026-08-08T03:17:00+00:00")

    render(tmp_path)

    body = (tmp_path / "reports" / "nightly" / "2026-08-08" / "commerce" / "verification.md").read_text()
    assert "precision 0.69" in body
