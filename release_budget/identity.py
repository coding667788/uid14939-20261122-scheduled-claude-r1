"""Identity interpretations over immutable source-member records.

A :class:`~release_budget.casefile.Linkage` says, for every source member, which
participants the input admits.  An *identity interpretation* (a **choice**)
picks exactly one admitted participant for every choice point, globally, so two
releases that mention the same source member can never disagree about who that
member is.

A ``merge_group`` forces its members to share one participant: it is one
individual recorded under several old numbers.  Such a group is a single choice
point, which is why a merged or split identity must be chosen jointly rather
than per release.

Choice points are the connected components of the "must share" relation.  They
are independent of one another, so a choice is any function from choice points
to their admissible participants, and *every* such function is feasible.

The module never mutates a member record or a snapshot; it only builds views.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Iterable, Mapping

from .casefile import Case, Linkage
from .errors import CaseError


class _UnionFind:
    def __init__(self, nodes: Iterable[str]):
        self.parent = {node: node for node in nodes}
        self.rank = {node: 0 for node in nodes}

    def find(self, node: str) -> str:
        parent = self.parent
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


@dataclass(frozen=True)
class Components:
    """The choice points of one linkage table.

    ``admissible`` maps each choice point to the participants it may take; for
    a merge group that is the intersection of its members' candidates, so a
    joint choice exists exactly when the intersection is non-empty.
    """

    roots: tuple[str, ...]
    component_of_member: Mapping[str, str]
    members_by_root: Mapping[str, tuple[str, ...]]
    admissible: Mapping[str, tuple[str, ...]]

    # -- queries ---------------------------------------------------------

    def admits(self, member_id: str, participant_id: str) -> bool:
        root = self.component_of_member.get(member_id)
        return root is not None and participant_id in self.admissible[root]

    def roots_of(self, member_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    self.component_of_member[member_id]
                    for member_id in member_ids
                    if member_id in self.component_of_member
                }
            )
        )

    @property
    def interpretation_count(self) -> int:
        return prod(len(options) for options in self.admissible.values())

    # -- choices ---------------------------------------------------------

    def default_choice(self) -> dict[str, str]:
        return {root: options[0] for root, options in self.admissible.items()}

    def witness(self, target: str | None = None) -> dict[str, str]:
        """One complete, feasible interpretation, member by member.

        With ``target`` set, every choice point admitting it is assigned to it,
        so this is the interpretation that maximises ``target``'s exposure.
        Remaining choice points take their lexicographically smallest
        admissible participant, which keeps the witness deterministic.  The
        mapping covers every member the linkage interprets, and it is global,
        so two releases that mention the same source member never disagree.
        """

        choice = self.choice_witness(target)
        return {
            member_id: choice[root]
            for member_id, root in sorted(self.component_of_member.items())
        }

    def choice_witness(self, target: str | None = None) -> dict[str, str]:
        choice: dict[str, str] = {}
        for root in self.roots:
            options = self.admissible[root]
            if target is not None and target in options:
                choice[root] = target
            else:
                choice[root] = options[0]
        return choice


def build_components(linkage: Linkage) -> Components:
    members = tuple(sorted(linkage.assigned_members()))
    union = _UnionFind(members)
    for group in linkage.merge_groups:
        for other in group[1:]:
            union.union(group[0], other)

    grouped: dict[str, list[str]] = {}
    component_of_member: dict[str, str] = {}
    for member_id in members:
        root = union.find(member_id)
        component_of_member[member_id] = root
        grouped.setdefault(root, []).append(member_id)

    roots = tuple(sorted(grouped))
    admissible: dict[str, tuple[str, ...]] = {}
    members_by_root: dict[str, tuple[str, ...]] = {}
    for root in roots:
        group = tuple(sorted(grouped[root]))
        options = set(linkage.candidates(group[0]))
        for member_id in group[1:]:
            options &= set(linkage.candidates(member_id))
        if not options:
            # The loader rejects this, but re-check so no silent empty choice
            # point can reach the accounting layer.
            raise CaseError(
                "infeasible-merge-group",
                "merge group has no participant admitted by every member",
                "linkages",
                linkage.linkage_id,
            )
        members_by_root[root] = group
        admissible[root] = tuple(sorted(options))

    return Components(roots, component_of_member, members_by_root, admissible)


def require_covered(
    case: Case,
    components: Components,
    snapshot_id: str,
    *,
    path: str,
    record: str,
    linkage_id: str,
) -> None:
    """Reject a snapshot whose source members the linkage does not interpret.

    Dropping an unlinked member would silently discount its exposure, so the
    audit refuses instead and points at the snapshot that failed.
    """

    snapshot = case.snapshot(snapshot_id)
    for member_id in snapshot.unique_members():
        if member_id not in components.component_of_member:
            raise CaseError(
                "unlinked-member",
                f"snapshot {snapshot_id!r} uses member {member_id!r}, but linkage "
                f"{linkage_id!r} does not interpret it",
                path,
                record,
            )


__all__ = ["Components", "build_components", "require_covered"]
