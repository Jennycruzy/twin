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
    dated_reports = examples_reports / "nightly" / "2026-08-08"
    dated_reports.mkdir(parents=True)
    (dated_reports / "fragility-scorecard.txt").write_text("nightly score 61.517\n")
    (dated_reports / "estate-graph.txt").write_text("nightly fingerprint abc\n")
    (verification / "merchant_id_nulled_at_source.txt").write_text("precision 0.69\n")

    latest = render(tmp_path)

    assert latest == tmp_path / "reports" / "LATEST.md"
    text = latest.read_text()
    assert "Tests passed: `163`" in text
    assert "Verification precision" not in text
    assert "nightly/2026-08-08/verification.md" in text
    assert "nightly score 61.517" in (
        tmp_path / "reports" / "nightly" / "2026-08-08" / "scorecard.md"
    ).read_text()
    assert "nightly fingerprint abc" in (
        tmp_path / "reports" / "nightly" / "2026-08-08" / "mcp-readback.md"
    ).read_text()
