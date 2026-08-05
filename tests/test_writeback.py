"""Tests for the fragility dimension Twin writes into DataHub.

These are unit tests over the derivation — what value each property gets, given a score. The
round trip itself (define, write, read back over MCP) needs a live DataHub and is exercised
by `make writeback && make prove-writeback`, which is what the acceptance criterion names.

The rule these tests protect is the one the whole project rests on: every published number is
derived from something measured. A property that could be computed without the knockout sweep
having run would be a number Twin made up, and it would be indistinguishable in the catalog
from one it measured.
"""

from __future__ import annotations

import datetime as dt

import pytest

from twin.score.fragility import COMPONENTS, Score
from twin.score.knockout import Knockout
from twin.write.properties import BY_ID, DEFINITIONS, PREFIX, bus_factor, is_spof, values_for


def make_score(
    key: str = "raw_pg.fx_rates",
    score: float = 61.517,
    datasets_lost: tuple[str, ...] = ("a", "b"),
    consumers_lost: tuple[str, ...] = ("dash",),
    owners_paged: tuple[str, ...] = ("priya", "sam"),
) -> Score:
    shot = Knockout(
        key=key,
        timeline=None,
        datasets_lost=datasets_lost,
        consumers_lost=consumers_lost,
        owners_paged=owners_paged,
        unowned_in_radius=(),
        first_consumer_at=dt.timedelta(hours=2),
    )
    return Score(
        key=key,
        score=score,
        components={name: 0.5 for name in COMPONENTS},
        raw={name: 1.0 for name in COMPONENTS},
        knockout=shot,
    )


def test_every_definition_has_a_unique_prefixed_id():
    """The prefix is what makes the write-back reversible, so nothing may sit outside it."""
    ids = [d.id for d in DEFINITIONS]
    assert len(ids) == len(set(ids))
    assert all(i.startswith(PREFIX) for i in ids)
    assert len(BY_ID) == len(DEFINITIONS)


def test_every_definition_carries_a_description():
    """A score in a catalog with no stated rule is a number nobody can argue with."""
    assert all(len(d.description) > 40 for d in DEFINITIONS)


def test_resilience_is_the_complement_of_fragility():
    values = values_for(make_score(score=61.517), rank=1, scored_at="t", provenance="p")
    assert values[f"{PREFIX}fragility_score"] == 61.517
    assert values[f"{PREFIX}resilience_score"] == pytest.approx(38.483)


def test_blast_radius_counts_datasets_and_consumers():
    values = values_for(
        make_score(datasets_lost=("a", "b", "c"), consumers_lost=("d1", "d2")),
        rank=4,
        scored_at="t",
        provenance="p",
    )
    assert values[f"{PREFIX}blast_radius"] == 5


def test_bus_factor_counts_distinct_owners_not_pages():
    """One owner paged three times is still one person who can fix it."""
    assert bus_factor(make_score(owners_paged=("priya", "priya", "priya"))) == 1
    assert bus_factor(make_score(owners_paged=("priya", "sam"))) == 2


def test_an_unowned_blast_radius_has_a_bus_factor_of_zero():
    assert bus_factor(make_score(owners_paged=())) == 0


def test_spof_needs_both_a_consumer_and_a_thin_bus_factor():
    """The stated rule, tested at each of its edges rather than at its middle."""
    assert is_spof(make_score(consumers_lost=("dash",), owners_paged=("priya",)))
    assert is_spof(make_score(consumers_lost=("dash",), owners_paged=()))
    # Something people read goes down, but three people can fix it.
    assert not is_spof(make_score(consumers_lost=("dash",), owners_paged=("a", "b", "c")))
    # One owner, but nothing anybody reads is lost.
    assert not is_spof(make_score(consumers_lost=(), owners_paged=("priya",)))


def test_spof_is_written_as_a_word_because_datahub_has_no_boolean():
    values = values_for(
        make_score(consumers_lost=("dash",), owners_paged=("priya",)),
        rank=1,
        scored_at="t",
        provenance="p",
    )
    assert values[f"{PREFIX}is_spof"] == "yes"


def test_every_component_is_published_beside_the_total():
    """A score nobody can take apart is a score nobody should act on."""
    values = values_for(make_score(), rank=1, scored_at="t", provenance="p")
    for name in COMPONENTS:
        assert f"{PREFIX}component_{name}" in values


def test_every_written_value_has_a_definition_behind_it():
    """Nothing may be written that the catalog has no description for."""
    values = values_for(make_score(), rank=1, scored_at="t", provenance="p")
    assert set(values) <= set(BY_ID)
    assert set(values) == set(BY_ID)
