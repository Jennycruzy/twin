"""Grade the prediction against what actually happened.

This is the module the project stands on, and its only real job is to resist the temptation
to flatter Twin. Three decisions do that work.

**Scope is declared before scoring.** Only assets the experiment could actually observe are
graded — the models dbt attempted to rebuild. A prediction about a dashboard is not scored
here, because a dbt build cannot observe a dashboard; those are reported separately and
counted nowhere. Silently scoring against a set that happens to contain only easy cases is
how a scorecard becomes theatre.

**Misses are named, not counted.** A false negative is printed with the error the warehouse
returned, so a reader can see precisely what Twin failed to anticipate. An honest 0.85 with
three named misses is stronger evidence than a perfect score, which mostly reads as staged.

**A perfect score is a warning.** Twin's rules and the estate come from the same repository,
so precision and recall of 1.00 on every scenario means the scenarios are too easy or the
model has been fitted to them, and the report says so rather than celebrating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence



@dataclass(frozen=True)
class Scorecard:
    """Predicted against observed, over a declared scope."""

    scope: tuple[str, ...]
    predicted: tuple[str, ...]
    observed: tuple[str, ...]
    hits: tuple[str, ...]
    false_alarms: tuple[str, ...]
    misses: tuple[str, ...]
    ungraded_predictions: tuple[str, ...]

    @property
    def precision(self) -> float | None:
        """Of what Twin predicted would break, how much did. ``None`` if it predicted nothing."""
        claimed = len(self.hits) + len(self.false_alarms)
        return len(self.hits) / claimed if claimed else None

    @property
    def recall(self) -> float | None:
        """Of what broke, how much Twin predicted. ``None`` if nothing broke."""
        actual = len(self.hits) + len(self.misses)
        return len(self.hits) / actual if actual else None

    @property
    def is_suspiciously_perfect(self) -> bool:
        return self.precision == 1.0 and self.recall == 1.0 and len(self.observed) > 0


def grade(predicted: Iterable[str], observed: Sequence[str], scope: Iterable[str]) -> Scorecard:
    """Compare a prediction with observed reality inside ``scope``."""
    in_scope = set(scope)
    predicted = set(predicted)
    observed_set = set(observed)

    predicted_in_scope = predicted & in_scope
    # Observations outside the declared scope would be a bug in the experiment rather than a
    # finding, so they are intersected in rather than quietly counted as misses.
    observed_in_scope = observed_set & in_scope

    return Scorecard(
        scope=tuple(sorted(in_scope)),
        predicted=tuple(sorted(predicted_in_scope)),
        observed=tuple(sorted(observed_in_scope)),
        hits=tuple(sorted(predicted_in_scope & observed_in_scope)),
        false_alarms=tuple(sorted(predicted_in_scope - observed_in_scope)),
        misses=tuple(sorted(observed_in_scope - predicted_in_scope)),
        ungraded_predictions=tuple(sorted(predicted - in_scope)),
    )
