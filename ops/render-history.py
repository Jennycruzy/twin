"""Render examples/history/*.jsonl as a table a reader will actually look at.

The JSONL files are the record: append-only, one line per run that genuinely happened. They
are also unreadable, which means the nightly trend — the one claim Twin makes that cannot be
manufactured after the fact — is invisible to anyone who does not open a JSON viewer.

This renders them and nothing else. It reads the same files, computes no new numbers, and
carries no thresholds or verdicts: every value below appears in the JSONL as written. If a
line is malformed it is reported rather than skipped, because a history with a silent hole in
it is worse than one that says where the hole is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twin import evidence


def _load(path: Path) -> tuple[list[dict], list[str]]:
    """Every parseable record, and a complaint for every line that was not."""
    records: list[dict] = []
    problems: list[str] = []
    if not path.exists():
        return records, [f"{path} does not exist"]
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name} line {number} is not valid JSON: {exc}")
    return records, problems


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.3f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _table(headings: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headings) + " |",
        "|" + "|".join("---" for _ in headings) + "|",
        *["| " + " | ".join(row) + " |" for row in rows],
    ]


def _reads(records: list[dict]) -> list[str]:
    if not records:
        return ["No reads recorded yet."]
    rows = [
        [
            _cell(r.get("read_at")),
            _cell(r.get("fingerprint")),
            _cell(r.get("assets")),
            _cell(r.get("datasets")),
            _cell(r.get("edges")),
            _cell(r.get("column_edges")),
            _cell(r.get("unowned_datasets")),
            _cell(r.get("commit")),
        ]
        for r in records
    ]
    return _table(
        ["read at (UTC)", "fingerprint", "assets", "datasets", "edges", "column edges",
         "unowned", "commit"],
        rows,
    )


def _scores(records: list[dict]) -> list[str]:
    if not records:
        return ["No scoring runs recorded yet."]
    rows = []
    for r in records:
        top = r.get("top") or []
        first = top[0] if top else {}
        rows.append(
            [
                _cell(r.get("scored_at")),
                _cell(r.get("fingerprint")),
                _cell(r.get("assets_scored")),
                _cell(r.get("mean_score")),
                f"{first.get('key', '—')} ({_cell(first.get('score'))})",
            ]
        )
    return _table(
        ["scored at (UTC)", "fingerprint", "assets scored", "mean score", "most fragile"],
        rows,
    )


def _attempts(records: list[dict]) -> list[str]:
    if not records:
        return ["No attempts recorded yet."]
    rows = []
    for r in records:
        detail = _cell(r.get("detail"))
        if r.get("reconstructed_from"):
            detail += f" (reconstructed from `{r['reconstructed_from']}`)"
        rows.append(
            [
                _cell(r.get("attempted_at")),
                _cell(r.get("status")),
                _cell(r.get("stage")),
                _cell(r.get("commit")),
                detail,
            ]
        )
    return _table(["attempted (UTC)", "status", "stage reached", "commit", "detail"], rows)


def main() -> int:
    lines = [
        "# Nightly history",
        "",
        "Rendered from each estate's `nightly.jsonl` and `fragility.jsonl`, plus the shared",
        "`attempts.jsonl`, by `ops/render-history.py`. Those files are the record; this is a",
        "view of them. Regenerate with `make examples`.",
        "",
        "Every read and score below was written by a run that actually happened — the nightly",
        "appends only after a read succeeds, so a failed night contributes no numbers rather",
        "than asserted ones. It does contribute a row to the attempts table: a success-only",
        "history cannot tell a failed night from a night nobody ran, and the difference",
        "matters, because a failure means the newest verified numbers are older than the",
        "newest attempt. A changed fingerprint means the estate's structure changed; an",
        "unchanged one across nights is the evidence that scoring is deterministic.",
        "",
    ]

    problems: list[str] = []
    for target in evidence.known_targets():
        reads, read_problems = _load(evidence.nightly_history(target))
        scores, score_problems = _load(evidence.fragility_history(target))
        problems.extend(read_problems + score_problems)
        lines.extend(
            [
                f"## {target}",
                "",
                "### Estate reads",
                "",
                *_reads(reads),
                "",
                "### Fragility scoring",
                "",
                *_scores(scores),
                "",
            ]
        )

    attempts, attempt_problems = _load(evidence.attempts_history())
    lines.extend([
        "## Every attempt, including failures",
        "",
        *_attempts(attempts),
    ])

    problems.extend(attempt_problems)
    if problems:
        lines += ["", "## Lines that could not be read", ""]
        lines += [f"- {p}" for p in problems]

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
