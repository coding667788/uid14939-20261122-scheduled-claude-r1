"""The accounting engine.

For one identity choice the engine answers, exactly and by rational arithmetic:

* which participants a release exposes (deduplicated inside the release, and
  deduplicated across the several old ids a merge group may bundle);
* the exact consumption of each participant in each purpose group / window;
* whether every window stays within its cap for *every* feasible choice, and,
  when it does not, which participants, which releases, and which choices
  prove the overrun.

The structural fact that makes the audit cheap: a release's exposed set only
*grows* when a choice point is assigned to the participant being maximised.
Choice points are independent, so the worst case for participant ``p`` in a
window is reached by the single choice that assigns every choice point
admitting ``p`` to ``p``.  The main audit therefore never enumerates choices;
exhaustive enumeration is reserved for the independent cross-check in
:mod:`release_budget.bruteforce`.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Sequence

from .casefile import Case, Release, Snapshot, Window
from .identity import Components


@dataclass(frozen=True)
class Exposure:
    """One accounted release reduced to the identity choices it depends on.

    ``roots`` are the choice points of its *unique* source members.  A source
    member repeated in one snapshot, or two old ids bundled by a merge group,
    collapse to one entry here -- which is exactly why such a release charges a
    shared participant once.
    """

    release_id: str
    purpose_id: str
    occasion: int
    cost: Fraction
    roots: tuple[str, ...]

    def participants(self, choice: Mapping[str, str]) -> frozenset[str]:
        return frozenset(choice[root] for root in self.roots)


def release_exposure(case: Case, components: Components, release: Release) -> Exposure:
    snapshot: Snapshot = case.snapshot(release.snapshot_id)
    return Exposure(
        release.release_id,
        release.purpose_id,
        release.occasion,
        release.cost,
        components.roots_of(snapshot.unique_members()),
    )


def accounted_exposures(
    case: Case,
    components: Components,
    *,
    releases: Sequence[Release] | None = None,
) -> list[Exposure]:
    """Exposures of every release that still costs budget.

    A published or retracted release is accounted; a never-submitted draft is
    refunded.  History is never rewritten, so a later snapshot or linkage does
    not change which releases are accounted.
    """

    source = case.releases if releases is None else releases
    return [
        release_exposure(case, components, release)
        for release in source
        if release.accounted
    ]


def charge_participant(
    exposures: Iterable[Exposure],
    window: Window,
    participant_id: str,
    choice: Mapping[str, str],
) -> Fraction:
    """Exact charge to one participant in one window under one choice."""

    total = Fraction(0)
    for exposure in exposures:
        if not window.matches(exposure.purpose_id, exposure.occasion):
            continue
        if participant_id in exposure.participants(choice):
            total += exposure.cost
    return total


def charge_all(
    exposures: Iterable[Exposure], window: Window, choice: Mapping[str, str]
) -> dict[str, Fraction]:
    """Exact per-participant charge in one window under one choice."""

    charges: dict[str, Fraction] = {}
    for exposure in exposures:
        if not window.matches(exposure.purpose_id, exposure.occasion):
            continue
        for participant_id in exposure.participants(choice):
            charges[participant_id] = charges.get(participant_id, Fraction(0)) + exposure.cost
    return charges


def worst_case_charges(
    exposures: Iterable[Exposure], window: Window, components: Components
) -> dict[str, Fraction]:
    """Exact worst-case charge per participant over all choices.

    For ``p`` the worst choice assigns every choice point that *can* take ``p``
    to ``p``; it is feasible because choice points are independent, and it
    dominates every other choice.  So one pass yields every participant's exact
    worst case at once.
    """

    charges: dict[str, Fraction] = {}
    for exposure in exposures:
        if not window.matches(exposure.purpose_id, exposure.occasion):
            continue
        # This release charges ``p`` in the worst case iff at least one of its
        # choice points admits ``p`` -- and then exactly once.
        admitting: set[str] = set()
        for root in exposure.roots:
            admitting.update(components.admissible[root])
        for participant_id in admitting:
            charges[participant_id] = charges.get(participant_id, Fraction(0)) + exposure.cost
    return charges


def releases_charged(
    exposures: Iterable[Exposure],
    window: Window,
    participant_id: str,
    components: Components,
) -> tuple[str, ...]:
    """IDs of the releases that charge ``participant_id`` in this window.

    ``p`` is charged by a release in the worst case exactly when one of the
    release's choice points admits ``p``; the set is therefore independent of
    which particular choice realises the worst case.
    """

    hits = []
    for exposure in exposures:
        if not window.matches(exposure.purpose_id, exposure.occasion):
            continue
        if any(participant_id in components.admissible[root] for root in exposure.roots):
            hits.append(exposure.release_id)
    return tuple(sorted(hits))


__all__ = [
    "Exposure",
    "accounted_exposures",
    "charge_all",
    "charge_participant",
    "release_exposure",
    "releases_charged",
    "worst_case_charges",
]
