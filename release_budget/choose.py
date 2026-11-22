"""Exact subset selection for pending releases.

``choose`` picks one fixed subset of the pending candidates.  The subset must

1. keep every mandatory candidate,
2. stay within every budget window under **every** feasible identity choice,
3. maximise total research value,
4. then minimise the added worst-case personal consumption, and
5. then take the lexicographically smallest sorted release-ID list.

The feasibility test is the same monotonicity the audit uses: a participant is
charged by a release exactly when some choice point of that release admits the
participant, and steering every admitting choice point to that participant is a
feasible choice.  So a subset is safe under all choices iff, for every
(window, participant) cell, base charge plus selected cost is at most the cap.
That is a multi-dimensional 0/1 knapsack; with at most 20 candidates an exact
branch-and-bound is small and is what this module runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Sequence

from .casefile import Candidate, Case, Release, Window
from .identity import Components, build_components, require_covered
from .ledger import release_exposure
from .rational import format_rational

Cell = tuple[str, str]  # (window_id, participant_id)


@dataclass(frozen=True)
class Selection:
    feasible: bool
    selected: tuple[str, ...]
    mandatory: tuple[str, ...]
    optional: tuple[str, ...]
    research_value: Fraction
    added_worst_case: Fraction
    baseline_compliant: bool
    blocked: tuple[dict, ...]
    candidate_count: int

    def to_json(self, case_id: str) -> dict:
        body = {
            "ok": True,
            "command": "choose",
            "case_id": case_id,
            "feasible": self.feasible,
            "mandatory_release_ids": list(self.mandatory),
            "selected_release_ids": list(self.selected),
            "optional_release_ids": list(self.optional),
            "research_value": format_rational(self.research_value),
            "added_worst_case_consumption": format_rational(self.added_worst_case),
            "baseline_compliant": self.baseline_compliant,
            "candidates_considered": self.candidate_count,
        }
        if not self.feasible:
            body["reason"] = "no-subset-within-budget"
            body["blocked"] = [dict(cell) for cell in self.blocked]
        return body


def release_cells(
    case: Case,
    components: Components,
    release: Release | Candidate,
    windows: Sequence[Window],
) -> dict[Cell, Fraction]:
    """Cells this release charges in the worst case, with its cost."""

    exposure = release_exposure(case, components, release)
    participants: set[str] = set()
    for root in exposure.roots:
        participants.update(components.admissible[root])
    cells: dict[Cell, Fraction] = {}
    for window in windows:
        if not window.matches(release.purpose_id, release.occasion):
            continue
        for participant_id in participants:
            cells[(window.window_id, participant_id)] = release.cost
    return cells


def _cell_cap(case: Case) -> dict[str, Fraction]:
    return {window.window_id: window.cap for window in case.windows}


def _blocked_cells(
    base: dict[Cell, Fraction],
    caps: dict[str, Fraction],
    added: dict[Cell, Fraction] | None = None,
) -> tuple[dict, ...]:
    extra = added or {}
    blocked = []
    for cell in sorted(set(base) | set(extra)):
        used = base.get(cell, Fraction(0)) + extra.get(cell, Fraction(0))
        cap = caps[cell[0]]
        if used > cap:
            blocked.append(
                {
                    "window_id": cell[0],
                    "participant_id": cell[1],
                    "charge": format_rational(used),
                    "cap": format_rational(cap),
                    "excess": format_rational(used - cap),
                }
            )
    return tuple(blocked)


def _covered(case: Case, components: Components, linkage_id: str) -> None:
    """Refuse an uninterpreted snapshot rather than silently dropping members."""

    for index, release in enumerate(case.accounted_releases()):
        require_covered(
            case,
            components,
            release.snapshot_id,
            path=f"releases[{index}].snapshot_id",
            record=release.release_id,
            linkage_id=linkage_id,
        )
    for index, candidate in enumerate(case.candidates):
        require_covered(
            case,
            components,
            candidate.snapshot_id,
            path=f"candidates[{index}].snapshot_id",
            record=candidate.release_id,
            linkage_id=linkage_id,
        )


def run_choose(case: Case, components: Components | None = None) -> Selection:
    if components is None:
        linkage_id = case.active_linkage_id
        if linkage_id is None:
            raise CaseError(
                "unknown-linkage", "choose needs an active linkage", "active_linkage", None
            )
        components = build_components(case.linkage(linkage_id))
    else:
        linkage_id = "explicit"

    _covered(case, components, linkage_id)

    windows = case.windows
    caps = _cell_cap(case)

    base: dict[Cell, Fraction] = {}
    for release in case.accounted_releases():
        for cell, cost in release_cells(case, components, release, windows).items():
            base[cell] = base.get(cell, Fraction(0)) + cost

    baseline_blocked = _blocked_cells(base, caps)
    baseline_compliant = not baseline_blocked

    mandatory = [candidate for candidate in case.candidates if candidate.mandatory]
    optional = [candidate for candidate in case.candidates if not candidate.mandatory]
    mandatory_ids = tuple(sorted(candidate.release_id for candidate in mandatory))

    mandatory_cells: dict[Cell, Fraction] = {}
    for candidate in mandatory:
        for cell, cost in release_cells(case, components, candidate, windows).items():
            mandatory_cells[cell] = mandatory_cells.get(cell, Fraction(0)) + cost

    if baseline_blocked or _blocked_cells(base, caps, mandatory_cells):
        blocker = baseline_blocked or _blocked_cells(base, caps, mandatory_cells)
        return Selection(
            feasible=False,
            selected=(),
            mandatory=mandatory_ids,
            optional=(),
            research_value=Fraction(0),
            added_worst_case=Fraction(0),
            baseline_compliant=baseline_compliant,
            blocked=blocker,
            candidate_count=len(case.candidates),
        )

    vectors: list[dict[Cell, Fraction]] = [
        release_cells(case, components, candidate, windows) for candidate in optional
    ]
    values = [candidate.research_value for candidate in optional]
    ids = [candidate.release_id for candidate in optional]

    # Mandatory value always counts; mandatory charge is folded into residual
    # capacity by ``_best_subset`` and into the worst-case objective there.
    mandatory_value = sum(
        (candidate.research_value for candidate in mandatory), Fraction(0)
    )

    best = _best_subset(
        ids, values, vectors, caps, base, mandatory_cells, mandatory_ids
    )

    return Selection(
        feasible=True,
        selected=tuple(sorted((*mandatory_ids, *best["chosen"]))),
        mandatory=mandatory_ids,
        optional=tuple(best["chosen"]),
        research_value=mandatory_value + best["value"],
        added_worst_case=best["worst"],
        baseline_compliant=baseline_compliant,
        blocked=(),
        candidate_count=len(case.candidates),
    )


def _best_subset(
    ids: list[str],
    values: list[Fraction],
    vectors: list[dict[Cell, Fraction]],
    caps: dict[str, Fraction],
    base: dict[Cell, Fraction],
    mandatory_cells: dict[Cell, Fraction],
    mandatory_ids: tuple[str, ...],
) -> dict:
    """Exact branch and bound.

    Residual capacity per cell is ``cap - base - mandatory``.  Items are tried
    in descending value order so a good incumbent appears early; the bound is
    the remaining total value, which is admissible and prunes hard on
    20-candidate cases.
    """

    order = sorted(range(len(ids)), key=lambda index: (-values[index], ids[index]))
    ordered_ids = [ids[index] for index in order]
    ordered_values = [values[index] for index in order]
    ordered_vectors = [vectors[index] for index in order]

    residual: dict[Cell, Fraction] = {}
    for cell in set(base) | set(mandatory_cells):
        residual[cell] = caps[cell[0]] - base.get(cell, Fraction(0)) - mandatory_cells.get(
            cell, Fraction(0)
        )
    for vector in ordered_vectors:
        for cell in vector:
            residual.setdefault(cell, caps[cell[0]])

    suffix_value = [Fraction(0)] * (len(ordered_ids) + 1)
    for position in range(len(ordered_ids) - 1, -1, -1):
        suffix_value[position] = suffix_value[position + 1] + ordered_values[position]

    best: dict | None = None

    def consider(value: Fraction, added: dict[Cell, Fraction], chosen: list[str]) -> None:
        nonlocal best
        # Added worst-case consumption per participant = mandatory + optional
        # charges, maximised over cells.  It is exactly the increase over the
        # baseline worst case, because both are additive per cell.
        worst = max(
            (
                mandatory_cells.get(cell, Fraction(0)) + added.get(cell, Fraction(0))
                for cell in set(mandatory_cells) | set(added)
            ),
            default=Fraction(0),
        )
        # The final tie-break compares the full selected list, mandatory items
        # included, because that is what the caller publishes.
        key = (value, worst, tuple(sorted((*mandatory_ids, *chosen))))
        if best is None or _better(key, best["key"]):
            best = {
                "key": key,
                "value": value,
                "worst": worst,
                "chosen": tuple(sorted(chosen)),
            }

    def dfs(
        position: int,
        value: Fraction,
        added: dict[Cell, Fraction],
        chosen: list[str],
    ) -> None:
        if best is not None and value + suffix_value[position] < best["value"]:
            return
        if position == len(ordered_ids):
            consider(value, added, chosen)
            return

        vector = ordered_vectors[position]
        feasible = True
        for cell, cost in vector.items():
            if added.get(cell, Fraction(0)) + cost > residual[cell]:
                feasible = False
                break
        if feasible:
            for cell, cost in vector.items():
                added[cell] = added.get(cell, Fraction(0)) + cost
            chosen.append(ordered_ids[position])
            dfs(position + 1, value + ordered_values[position], added, chosen)
            chosen.pop()
            for cell, cost in vector.items():
                remaining = added[cell] - cost
                if remaining:
                    added[cell] = remaining
                else:
                    del added[cell]

        dfs(position + 1, value, added, chosen)

    dfs(0, Fraction(0), {}, [])
    assert best is not None
    return best


def _better(left: tuple, right: tuple) -> bool:
    """Higher value, then lower worst charge, then smaller ID list."""

    left_value, left_worst, left_ids = left
    right_value, right_worst, right_ids = right
    if left_value != right_value:
        return left_value > right_value
    if left_worst != right_worst:
        return left_worst < right_worst
    return left_ids < right_ids


__all__ = ["Selection", "release_cells", "run_choose"]
