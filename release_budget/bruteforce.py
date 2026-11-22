"""Independent small-scale exhaustive audit.

This module deliberately does **not** reuse the accounting engine.  It works
directly from the raw case file: for each feasible identity interpretation it
maps every source member of every accounted release's snapshot to a
participant, deduplicates inside the release, accumulates the exact rational
cost per window and participant, and asks whether any cap is exceeded.

It is exponential in the number of ambiguous identity choices and is meant for
small cases and for cross-checking the fast audit.  ``python -m release_budget
verify CASE.json`` runs both and reports whether they agree.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterator, Mapping, Sequence

from .casefile import Case, Release, Window
from .identity import Components, build_components
from .rational import format_rational


def enumerate_choices(
    components: Components, *, limit: int | None = None
) -> Iterator[dict[str, str]]:
    """Yield complete feasible interpretations, deterministically.

    A choice assigns one admitted participant to every choice point.  A member
    outside any choice point is never charged, and the interpretation stays
    total for the members the linkage covers.
    """

    option_lists = [components.admissible[root] for root in components.roots]
    for index, combination in enumerate(itertools.product(*option_lists)):
        if limit is not None and index >= limit:
            return
        yield dict(zip(components.roots, combination))


def _exposed_members(case: Case, release: Release) -> tuple[str, ...]:
    # The raw multiset, with duplicates preserved, so the deduplication below
    # is exercised rather than assumed.
    return case.snapshot(release.snapshot_id).members


def _participants_of(
    case: Case,
    components: Components,
    release: Release,
    choice: Mapping[str, str],
) -> frozenset[str]:
    """Participants a release exposes under ``choice``.

    Walks raw members, maps each through its choice point, and deduplicates --
    this is the independent check that repeated members and merged old ids are
    charged once inside one release.
    """

    exposed: set[str] = set()
    for member_id in _exposed_members(case, release):
        root = components.component_of_member.get(member_id)
        if root is not None:
            exposed.add(choice[root])
    return frozenset(exposed)


def _window_charges(
    case: Case,
    components: Components,
    releases: Sequence[Release],
    window: Window,
    choice: Mapping[str, str],
) -> dict[str, Fraction]:
    charges: dict[str, Fraction] = {}
    for release in releases:
        if release.purpose_id not in window.purpose_ids:
            continue
        if not (window.start <= release.occasion < window.end):
            continue
        for participant_id in _participants_of(case, components, release, choice):
            charges[participant_id] = charges.get(participant_id, Fraction(0)) + release.cost
    return charges


@dataclass(frozen=True)
class BruteForceResult:
    compliant: bool
    choices_enumerated: int
    worst: dict[tuple[str, str], Fraction]
    witness: dict[str, str] | None

    def to_json(self) -> dict:
        return {
            "compliant": self.compliant,
            "choices_enumerated": self.choices_enumerated,
            "worst": {
                f"{window_id}|{participant_id}": format_rational(charge)
                for (window_id, participant_id), charge in sorted(self.worst.items())
            },
            "witness": dict(sorted(self.witness.items())) if self.witness else None,
        }


def brute_force_audit(
    case: Case,
    components: Components | None = None,
    *,
    releases: Sequence[Release] | None = None,
    limit: int | None = None,
) -> BruteForceResult:
    if components is None:
        components = build_components(case.linkage(case.active_linkage_id))
    source = tuple(releases) if releases is not None else case.accounted_releases()

    worst: dict[tuple[str, str], Fraction] = {}
    failing: dict[str, str] | None = None
    enumerated = 0

    for choice in enumerate_choices(components, limit=limit):
        enumerated += 1
        exceeded = False
        for window in case.windows:
            for participant_id, charge in _window_charges(
                case, components, source, window, choice
            ).items():
                key = (window.window_id, participant_id)
                if key not in worst or charge > worst[key]:
                    worst[key] = charge
                if charge > window.cap:
                    exceeded = True
        if exceeded and failing is None:
            failing = choice

    compliant = True
    caps = {window.window_id: window.cap for window in case.windows}
    for (window_id, _participant_id), charge in worst.items():
        if charge > caps[window_id]:
            compliant = False
            break

    return BruteForceResult(
        compliant=compliant,
        choices_enumerated=enumerated,
        worst=worst,
        witness=failing,
    )


def fast_worst_by_cell(
    case: Case, components: Components, releases: Sequence[Release] | None = None
) -> dict[tuple[str, str], Fraction]:
    """The fast audit's exact worst case per (window, participant) cell."""

    from .audit import assess_worst_case
    from .ledger import accounted_exposures

    exposures = accounted_exposures(case, components, releases=releases)
    cells: dict[tuple[str, str], Fraction] = {}
    for window in case.windows:
        for participant_id, charge in assess_worst_case(
            exposures, window, components
        ).items():
            cells[(window.window_id, participant_id)] = charge
    return cells


def compare_with_fast(
    case: Case, components: Components | None = None
) -> dict:
    """Cross-check the fast audit against exhaustive enumeration."""

    from .audit import audit_with_linkage

    if components is None:
        components = build_components(case.linkage(case.active_linkage_id))
    linkage = case.linkage(case.active_linkage_id)
    fast = audit_with_linkage(case, linkage, releases=case.accounted_releases())
    slow = brute_force_audit(case, components)
    fast_cells = fast_worst_by_cell(case, components, case.accounted_releases())

    mismatches = []
    for key in sorted(set(fast_cells) | set(slow.worst)):
        left = fast_cells.get(key, Fraction(0))
        right = slow.worst.get(key, Fraction(0))
        if left != right:
            mismatches.append(
                {
                    "window_id": key[0],
                    "participant_id": key[1],
                    "fast": format_rational(left),
                    "bruteforce": format_rational(right),
                }
            )
    return {
        "ok": True,
        "command": "verify",
        "case_id": case.case_id,
        "linkage_id": linkage.linkage_id,
        "choices_enumerated": slow.choices_enumerated,
        "fast_compliant": fast.compliant,
        "bruteforce_compliant": slow.compliant,
        "agree": not mismatches and fast.compliant == slow.compliant,
        "mismatches": mismatches,
    }


__all__ = [
    "BruteForceResult",
    "brute_force_audit",
    "compare_with_fast",
    "enumerate_choices",
    "fast_worst_by_cell",
]
