"""Published context confidence must reflect the campaign ledger.

The write path computed context confidence without reading the ledger, so every asset in
every estate published `verification=0.00` no matter how many experiments had actually run.
The campaign ranking read the same ledger correctly, which is what hid it: selection behaved
as designed while the catalog reported that nothing had ever been verified.

The live round trip is covered by `make writeback && make prove-writeback`. What is pinned
here is the derivation those commands publish.
"""

from __future__ import annotations

import json

from twin.context import confidence, evidence_path, verified_assets

FINGERPRINT = "2b0ff33cd937f51f"


def write_ledger(cache_dir, fingerprint=FINGERPRINT, asset="staging.stg_fx_rates"):
    path = evidence_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "fault_asset": asset,
                "scenario": "fx_rate_column_drop",
                "target": "commerce",
            }
        )
        + "\n"
    )
    return path


def test_a_recorded_experiment_marks_its_asset_verified(tmp_path):
    path = write_ledger(tmp_path)

    assert verified_assets(path, FINGERPRINT) == {"staging.stg_fx_rates"}


def test_evidence_from_another_estate_read_does_not_count(tmp_path):
    """A ledger line is evidence about the graph it ran against, not about any later one."""
    path = write_ledger(tmp_path, fingerprint="0000000000000000")

    assert verified_assets(path, FINGERPRINT) == set()


def test_a_missing_ledger_is_absence_of_evidence_not_an_error(tmp_path):
    assert verified_assets(evidence_path(tmp_path), FINGERPRINT) == set()


def test_confidence_separates_a_verified_asset_from_an_inferred_one():
    """The published component must tell a broken-for-real asset from an inferred one."""
    from tests.test_context import graph as build_graph

    graph = build_graph()
    key = graph.assets[0].key

    inferred = confidence(graph, key, None, ())
    verified = confidence(graph, key, None, {key})

    assert inferred.verification == 0.0
    assert verified.verification == 1.0
    assert verified.score > inferred.score
