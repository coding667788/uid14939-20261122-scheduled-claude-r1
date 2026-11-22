"""Audit and recheck.

``audit`` answers one question: is every budget window within its cap under
*every* feasible identity choice?  Because a choice point can be assigned to
any participant it admits, independently of the others, the charge to a
participant can only grow when the choice is steered toward that participant.
So the exact worst case for participant ``p`` is obtained by the single choice
that assigns every choice point admitting ``p`` to ``p`` -- no enumeration is
needed, and no approximation is used.

When that worst case exceeds a cap the report is a *proof*: the participant,
the abstract sources (choice points) that carry the exposure, the exact release
set, the exact rational charge and excess, and one complete feasible choice
that realises it.  An ``audit`` with no overrun certifies compliance for all
choices.

``recheck`` replays a named historical audit view and then the newly entered
linkage table, and reports exactly which conclusions changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Sequence

from .casefile import Case, Linkage, Release, Window
from .errors import CaseError
from .identity import Components, build_components, require_covered
from .ledger import (
    Exposure,
    release_exposure,
    releases_charged,
    worst_case_charges,
)
from .rational import format_rational


@dataclass(frozen=True)
class Violation:
    window: Window
    participant_id: str
    source_id: str
    sources: tuple[dict, ...]
    release_ids: tuple[str, ...]
    charge: Fraction
    excess: Fraction
    witness: tuple[tuple[str, str], ...]

    def sort_key(self):
        # Contract order: time window, then purpose, then abstract source.
        return (
            self.window.start,
            self.window.end,
            self.window.purpose_key,
            self.window.window_id,
            self.source_id,
            self.participant_id,
        )

    def to_json(self) -> dict:
        return {
            "window_id": self.window.window_id,
            "start": self.window.start,
            "end": self.window.end,
            "purpose_ids": list(self.window.purpose_ids),
            "cap": format_rational(self.window.cap),
            "participant_id": self.participant_id,
            "source_id": self.source_id,
            "sources": [dict(source) for source in self.sources],
            "release_ids": list(self.release_ids),
            "charge": format_rational(self.charge),
            "excess": format_rational(self.excess),
            "witness": dict(self.witness),
        }


@dataclass(frozen=True)
class AuditResult:
    linkage_id: str
    components: Components
    exposures: tuple[Exposure, ...]
    violations: tuple[Violation, ...]
    accounted_release_ids: tuple[str, ...]
    refunded_release_ids: tuple[str, ...]

    @property
    def compliant(self) -> bool:
        return not self.violations

    def violation_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (violation.window.window_id, violation.participant_id)
            for violation in self.violations
        )

    def to_json(self, *, view_id: str | None = None) -> dict:
        body = {
            "ok": True,
            "command": "audit",
            "case_id": None,
            "view": {"view_id": view_id, "linkage_id": self.linkage_id},
            "compliant": self.compliant,
            "interpretations": self.components.interpretation_count,
            "accounted_releases": list(self.accounted_release_ids),
            "refunded_releases": list(self.refunded_release_ids),
            "windows_with_violations": len(
                {violation.window.window_id for violation in self.violations}
            ),
            "violations": [violation.to_json() for violation in self.violations],
        }
        return body


def _sources_for(
    case: Case,
    components: Components,
    participant_id: str,
    exposures: Sequence[Exposure],
) -> tuple[str, tuple[dict, ...]]:
    """Abstract sources that carry ``participant_id`` in a window."""

    roots: dict[str, list[str]] = {}
    for exposure in exposures:
        for root in exposure.roots:
            if participant_id in components.admissible[root]:
                roots.setdefault(root, []).append(exposure.release_id)
    sources = []
    for root in sorted(roots):
        member_ids = list(components.members_by_root[root])
        cohort_ids = sorted({case.members[mid].cohort_id for mid in member_ids})
        sources.append(
            {
                "source_id": root,
                "member_ids": member_ids,
                "cohort_ids": cohort_ids,
                "release_ids": sorted(set(roots[root])),
            }
        )
    source_id = sources[0]["source_id"] if sources else participant_id
    return source_id, tuple(sources)


def audit_with_linkage(
    case: Case,
    linkage: Linkage,
    *,
    view_id: str | None = None,
    releases: Sequence[Release] | None = None,
    windows: Sequence[Window] | None = None,
) -> AuditResult:
    components = build_components(linkage)
    source_releases = tuple(releases) if releases is not None else case.accounted_releases()

    # Every accounted release must have a fully interpreted snapshot; refusing
    # here prevents an unlinked member from silently discounting exposure.
    index_of = {release.release_id: index for index, release in enumerate(case.releases)}
    for release in source_releases:
        require_covered(
            case,
            components,
            release.snapshot_id,
            path=f"releases[{index_of.get(release.release_id, 0)}].snapshot_id",
            record=release.release_id,
            linkage_id=linkage.linkage_id,
        )

    exposures = tuple(
        release_exposure(case, components, release) for release in source_releases
    )
    violations: list[Violation] = []
    for window in windows if windows is not None else case.windows:
        matching = [
            exposure
            for exposure in exposures
            if window.matches(exposure.purpose_id, exposure.occasion)
        ]
        # Exact worst-case charge for every participant at once (single engine
        # shared with the ledger, so the audit and the cross-check cannot drift).
        charges = worst_case_charges(matching, window, components)
        for participant_id, charge in charges.items():
            if charge <= window.cap:
                continue
            source_id, sources = _sources_for(
                case, components, participant_id, matching
            )
            witness = components.witness(participant_id)
            release_ids = releases_charged(
                matching, window, participant_id, components
            )
            violations.append(
                Violation(
                    window=window,
                    participant_id=participant_id,
                    source_id=source_id,
                    sources=sources,
                    release_ids=release_ids,
                    charge=charge,
                    excess=charge - window.cap,
                    witness=tuple(sorted(witness.items())),
                )
            )
    violations.sort(key=lambda violation: violation.sort_key())
    return AuditResult(
        linkage_id=linkage.linkage_id,
        components=components,
        exposures=exposures,
        violations=tuple(violations),
        accounted_release_ids=tuple(r.release_id for r in source_releases),
        refunded_release_ids=tuple(r.release_id for r in case.refunded_releases()),
    )


def assess_worst_case(
    exposures: Iterable[Exposure],
    window: Window,
    components: Components,
) -> dict[str, Fraction]:
    """Exact worst-case charge per participant for one window."""

    matching = [
        exposure
        for exposure in exposures
        if window.matches(exposure.purpose_id, exposure.occasion)
    ]
    return worst_case_charges(matching, window, components)


def resolve_view(case: Case, view_id: str | None) -> tuple[str | None, Linkage]:
    if view_id is None:
        return None, case.linkage(case.active_linkage_id)
    if view_id not in case.audit_views:
        raise CaseError(
            "unknown-view", f"unknown audit view {view_id!r}", "view_id", view_id
        )
    view = case.audit_views[view_id]
    return view.view_id, case.linkage(view.linkage_id)


def run_audit(case: Case, *, view_id: str | None = None) -> AuditResult:
    resolved_view, linkage = resolve_view(case, view_id)
    result = audit_with_linkage(case, linkage, view_id=resolved_view)
    return result


# --------------------------------------------------------------------------
# recheck
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecheckResult:
    view_id: str
    old_linkage_id: str
    new_linkage_id: str
    old: AuditResult
    new: AuditResult

    def to_json(self) -> dict:
        changes = _compare(self.old, self.new)
        old_json = self.old.to_json(view_id=self.view_id)
        new_json = self.new.to_json(view_id=self.view_id)
        return {
            "ok": True,
            "command": "recheck",
            "case_id": None,
            "view": {"view_id": self.view_id, "linkage_id": self.old_linkage_id},
            "new_linkage": self.new_linkage_id,
            "old": {
                "linkage_id": self.old_linkage_id,
                "compliant": self.old.compliant,
                "interpretations": self.old.components.interpretation_count,
                "violations": [v.to_json() for v in self.old.violations],
            },
            "new": {
                "linkage_id": self.new_linkage_id,
                "compliant": self.new.compliant,
                "interpretations": self.new.components.interpretation_count,
                "violations": [v.to_json() for v in self.new.violations],
            },
            "changed": [change for change in changes],
        }


def _violation_index(result: AuditResult) -> dict[tuple[str, str], Violation]:
    return {
        (violation.window.window_id, violation.participant_id): violation
        for violation in result.violations
    }


def _compare(old: AuditResult, new: AuditResult) -> list[dict]:
    old_index = _violation_index(old)
    new_index = _violation_index(new)
    old_keys = set(old_index)
    new_keys = set(new_index)
    changes: list[dict] = []
    for key in sorted(old_keys - new_keys):
        violation = old_index[key]
        changes.append(
            {
                "change": "resolved",
                "window_id": violation.window.window_id,
                "participant_id": violation.participant_id,
                "source_id": violation.source_id,
                "old_charge": format_rational(violation.charge),
                "old_excess": format_rational(violation.excess),
            }
        )
    for key in sorted(new_keys - old_keys):
        violation = new_index[key]
        changes.append(
            {
                "change": "introduced",
                "window_id": violation.window.window_id,
                "participant_id": violation.participant_id,
                "source_id": violation.source_id,
                "new_charge": format_rational(violation.charge),
                "new_excess": format_rational(violation.excess),
            }
        )
    for key in sorted(old_keys & new_keys):
        before = old_index[key]
        after = new_index[key]
        if before.charge != after.charge or before.release_ids != after.release_ids:
            changes.append(
                {
                    "change": "revised",
                    "window_id": after.window.window_id,
                    "participant_id": after.participant_id,
                    "source_id": after.source_id,
                    "old_charge": format_rational(before.charge),
                    "new_charge": format_rational(after.charge),
                    "old_release_ids": list(before.release_ids),
                    "new_release_ids": list(after.release_ids),
                }
            )
    changes.sort(key=lambda change: (change["window_id"], change["participant_id"]))
    return changes


def run_recheck(
    case: Case, *, view_id: str | None = None, new_linkage_id: str | None = None
) -> RecheckResult:
    config_view = view_id
    config_new = new_linkage_id
    if case.recheck is not None:
        if config_view is None:
            config_view = case.recheck.view_id
        if config_new is None:
            config_new = case.recheck.new_linkage_id
    if config_view is None:
        raise CaseError(
            "missing-field", "recheck needs a view_id", "recheck.view_id", None
        )
    # Resolve the historical view first: an unknown view is the more specific
    # diagnostic, and the old side must be replayable before the new table is
    # compared against it.
    resolved_view, old_linkage = resolve_view(case, config_view)
    if config_new is None:
        raise CaseError(
            "missing-field", "recheck needs a new linkage", "recheck.new_linkage", None
        )
    if config_new not in case.linkages:
        raise CaseError(
            "unknown-linkage", f"unknown linkage {config_new!r}", "recheck.new_linkage", config_new
        )
    new_linkage = case.linkages[config_new]
    return RecheckResult(
        view_id=resolved_view,
        old_linkage_id=old_linkage.linkage_id,
        new_linkage_id=new_linkage.linkage_id,
        old=audit_with_linkage(case, old_linkage, view_id=resolved_view),
        new=audit_with_linkage(case, new_linkage, view_id=resolved_view),
    )


__all__ = [
    "AuditResult",
    "RecheckResult",
    "Violation",
    "assess_worst_case",
    "audit_with_linkage",
    "resolve_view",
    "run_audit",
    "run_recheck",
]
