"""Case-file loading and validation.

The case file is the frozen contract: immutable source-member records,
versioned cohort snapshots, publications bound to exactly one snapshot,
rational per-window costs, purpose groups, versioned identity-linkage tables,
and the pending candidates used by ``choose``.

Validation is strict.  Every rejection carries a machine path such as
``releases[3].cost`` and, where one exists, the offending record id, so a bad
input can be traced to the exact field of the exact record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from .errors import (
    CaseError,
    require_field,
    require_int,
    require_list,
    require_mapping,
    require_str,
)
from .rational import parse_rational

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_RETRACTED = "retracted"
STATUSES = (STATUS_PUBLISHED, STATUS_RETRACTED, STATUS_DRAFT)
#: A retraction does not refund: only a never-submitted draft is free.
ACCOUNTED_STATUSES = frozenset({STATUS_PUBLISHED, STATUS_RETRACTED})

SUPPORTED_CASE_VERSION = 1

#: Sentinel used in a complete interpretation for a member the linkage does
#: not mention.  Such a member is never part of an exposure set the audit
#: charges, but the interpretation stays total.
UNASSIGNED = None


@dataclass(frozen=True)
class Participant:
    participant_id: str


@dataclass(frozen=True)
class Cohort:
    cohort_id: str


@dataclass(frozen=True)
class Member:
    """An immutable source-member record.

    The record never changes.  Re-attribution (split, merge, corrected
    linkage) is expressed by a *different* linkage table pointing the same
    record at other participants; it never rewrites the record or the snapshot
    that used it.
    """

    member_id: str
    cohort_id: str


@dataclass(frozen=True)
class Snapshot:
    """A versioned multiset of source-member records for one cohort."""

    snapshot_id: str
    cohort_id: str
    version: int
    members: tuple[str, ...]

    def unique_members(self) -> tuple[str, ...]:
        # The same person appearing twice in one snapshot is still one exposure.
        return tuple(sorted(set(self.members)))


@dataclass(frozen=True)
class Window:
    """Half-open accounting window ``[start, end)`` for a purpose group."""

    window_id: str
    start: int
    end: int
    purpose_ids: tuple[str, ...]
    cap: Fraction

    def matches(self, purpose_id: str, occasion: int) -> bool:
        return purpose_id in self.purpose_ids and self.start <= occasion < self.end

    @property
    def purpose_key(self) -> str:
        return "|".join(sorted(self.purpose_ids))


@dataclass(frozen=True)
class Release:
    release_id: str
    batch_id: str
    snapshot_id: str
    cohort_id: str
    purpose_id: str
    occasion: int
    cost: Fraction
    status: str
    mandatory: bool = False

    @property
    def accounted(self) -> bool:
        return self.status in ACCOUNTED_STATUSES

    @property
    def refunded(self) -> bool:
        return self.status == STATUS_DRAFT


@dataclass(frozen=True)
class Candidate:
    """A pending, not-yet-released item considered by ``choose``."""

    release_id: str
    snapshot_id: str
    cohort_id: str
    purpose_id: str
    occasion: int
    cost: Fraction
    research_value: Fraction
    mandatory: bool = False


@dataclass(frozen=True)
class Linkage:
    """One version of the identity-linkage table.

    ``member_candidates`` lists, per source member, the participants the input
    admits.  A member named once is definite; a member named with several
    alternatives is *ambiguous*, and the audit quantifies over those
    alternatives.  A ``merge_group`` forces every listed source member to
    receive the same participant (a joint choice), which is how a correction
    that merges several old ids into one identity is expressed.
    """

    linkage_id: str
    effective_from: int | None
    effective_to: int | None
    member_candidates: tuple[tuple[str, tuple[str, ...]], ...]
    merge_groups: tuple[tuple[str, ...], ...]

    def candidates(self, member_id: str) -> tuple[str, ...]:
        for mid, options in self.member_candidates:
            if mid == member_id:
                return options
        return ()

    def assigned_members(self) -> tuple[str, ...]:
        return tuple(mid for mid, _ in self.member_candidates)


@dataclass(frozen=True)
class AuditView:
    """A named historical audit view: the identity table it was run under."""

    view_id: str
    linkage_id: str


@dataclass(frozen=True)
class RecheckConfig:
    view_id: str
    new_linkage_id: str


@dataclass(frozen=True)
class Case:
    case_id: str
    participants: dict[str, Participant]
    cohorts: dict[str, Cohort]
    purposes: dict[str, str]
    members: dict[str, Member]
    snapshots: dict[str, Snapshot]
    windows: tuple[Window, ...]
    releases: tuple[Release, ...]
    candidates: tuple[Candidate, ...]
    linkages: dict[str, Linkage]
    active_linkage_id: str | None
    audit_views: dict[str, AuditView]
    recheck: RecheckConfig | None

    def linkage(self, linkage_id: str | None = None) -> Linkage:
        chosen = linkage_id if linkage_id is not None else self.active_linkage_id
        if chosen is None or chosen not in self.linkages:
            raise CaseError(
                "unknown-linkage",
                f"unknown linkage {chosen!r}",
                "active_linkage",
                chosen,
            )
        return self.linkages[chosen]

    def snapshot(self, snapshot_id: str) -> Snapshot:
        return self.snapshots[snapshot_id]

    def accounted_releases(self) -> tuple[Release, ...]:
        return tuple(r for r in self.releases if r.accounted)

    def refunded_releases(self) -> tuple[Release, ...]:
        return tuple(r for r in self.releases if r.refunded)


# --------------------------------------------------------------------------
# JSON plumbing
# --------------------------------------------------------------------------


def load_case(path: str) -> Case:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise CaseError(
            "io-error",
            f"cannot read case file: {exc.strerror or exc}",
            "file",
            None,
        ) from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CaseError(
            "encoding-error",
            f"case file must be UTF-8: {exc.reason}",
            "file",
            None,
        ) from None
    try:
        # parse_float keeps float literals exact instead of binary-approximate.
        data = json.loads(text, parse_float=_decimal_literal)
    except json.JSONDecodeError as exc:
        raise CaseError(
            "invalid-json",
            f"malformed JSON: {exc.msg}",
            f"file:line {exc.lineno} column {exc.colno}",
            None,
        ) from None
    return build_case(data)


def _decimal_literal(text: str) -> Fraction:
    """Turn a JSON float literal into its exact decimal rational.

    ``json.loads(..., parse_float=...)`` hands over the *literal text*, so
    ``0.6666666666666666`` becomes the exact rational
    ``3333333333333333/5000000000000000`` and never a binary float.  A strict
    overrun therefore cannot be rounded into compliance.
    """

    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        raise CaseError(
            "invalid-rational",
            f"{text!r} is not a finite rational literal",
            "file",
            None,
        ) from None


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def _rat(value, *, path, record, what, minimum=None):
    return parse_rational(value, path=path, record=record, what=what, minimum=minimum)


def _check_unique(ids: list[str], *, path: str, what: str) -> None:
    seen: set[str] = set()
    for index, ident in enumerate(ids):
        if ident in seen:
            raise CaseError(
                "duplicate-id",
                f"{what} {ident!r} appears more than once; IDs are non-reusable",
                f"{path}[{index}]",
                ident,
            )
        seen.add(ident)


def _build_participants(raw) -> dict[str, Participant]:
    rows = require_list(raw, path="participants", what="participants", nonempty=True)
    built = []
    for index, item in enumerate(rows):
        path = f"participants[{index}]"
        obj = require_mapping(item, path=path, what="participant")
        pid = require_str(
            require_field(obj, "participant_id", path=path),
            path=f"{path}.participant_id",
            what="participant_id",
        )
        built.append(Participant(pid))
    _check_unique([p.participant_id for p in built], path="participants", what="participant")
    return {p.participant_id: p for p in built}


def _build_cohorts(raw) -> dict[str, Cohort]:
    rows = require_list(raw, path="cohorts", what="cohorts", nonempty=True)
    built = []
    for index, item in enumerate(rows):
        path = f"cohorts[{index}]"
        obj = require_mapping(item, path=path, what="cohort")
        cid = require_str(
            require_field(obj, "cohort_id", path=path),
            path=f"{path}.cohort_id",
            what="cohort_id",
        )
        built.append(Cohort(cid))
    _check_unique([c.cohort_id for c in built], path="cohorts", what="cohort")
    return {c.cohort_id: c for c in built}


def _build_members(raw, cohorts) -> dict[str, Member]:
    rows = require_list(raw, path="members", what="members", nonempty=True)
    built = []
    for index, item in enumerate(rows):
        path = f"members[{index}]"
        obj = require_mapping(item, path=path, what="member")
        mid = require_str(
            require_field(obj, "member_id", path=path),
            path=f"{path}.member_id",
            what="member_id",
        )
        cid = require_str(
            require_field(obj, "cohort_id", path=path),
            path=f"{path}.cohort_id",
            what="cohort_id",
        )
        if cid not in cohorts:
            raise CaseError(
                "unknown-cohort",
                f"member {mid!r} names unknown cohort {cid!r}",
                f"{path}.cohort_id",
                mid,
            )
        built.append(Member(mid, cid))
    _check_unique([m.member_id for m in built], path="members", what="member")
    return {m.member_id: m for m in built}


def _build_snapshots(raw, cohorts, members) -> dict[str, Snapshot]:
    rows = require_list(raw, path="snapshots", what="snapshots", nonempty=True)
    built = []
    versions: dict[tuple[str, int], str] = {}
    for index, item in enumerate(rows):
        path = f"snapshots[{index}]"
        obj = require_mapping(item, path=path, what="snapshot")
        sid = require_str(
            require_field(obj, "snapshot_id", path=path),
            path=f"{path}.snapshot_id",
            what="snapshot_id",
        )
        cid = require_str(
            require_field(obj, "cohort_id", path=path),
            path=f"{path}.cohort_id",
            what="cohort_id",
        )
        if cid not in cohorts:
            raise CaseError(
                "unknown-cohort",
                f"snapshot {sid!r} names unknown cohort {cid!r}",
                f"{path}.cohort_id",
                sid,
            )
        version = require_int(
            require_field(obj, "version", path=path),
            path=f"{path}.version",
            what="version",
        )
        if version < 1:
            raise CaseError("out-of-range", "version must be >= 1", f"{path}.version", sid)
        member_rows = require_list(
            require_field(obj, "members", path=path),
            path=f"{path}.members",
            record=sid,
            what="members",
        )
        member_ids = []
        for m_index, member_value in enumerate(member_rows):
            m_path = f"{path}.members[{m_index}]"
            mid = require_str(member_value, path=m_path, what="member reference")
            if mid not in members:
                raise CaseError(
                    "unknown-member",
                    f"snapshot {sid!r} references unknown member {mid!r}",
                    m_path,
                    sid,
                )
            if members[mid].cohort_id != cid:
                raise CaseError(
                    "cross-cohort-member",
                    f"member {mid!r} belongs to cohort {members[mid].cohort_id!r}, "
                    f"not {cid!r}",
                    m_path,
                    sid,
                )
            member_ids.append(mid)
        key = (cid, version)
        if key in versions:
            raise CaseError(
                "duplicate-snapshot-version",
                f"cohort {cid!r} already has a snapshot at version {version} "
                f"({versions[key]!r})",
                f"{path}.version",
                sid,
            )
        versions[key] = sid
        built.append(Snapshot(sid, cid, version, tuple(member_ids)))
    _check_unique([s.snapshot_id for s in built], path="snapshots", what="snapshot")
    return {s.snapshot_id: s for s in built}


def _build_purposes(raw) -> dict[str, str]:
    rows = require_list(raw, path="purposes", what="purposes", nonempty=True)
    ids = []
    for index, item in enumerate(rows):
        path = f"purposes[{index}]"
        obj = require_mapping(item, path=path, what="purpose")
        pid = require_str(
            require_field(obj, "purpose_id", path=path),
            path=f"{path}.purpose_id",
            what="purpose_id",
        )
        ids.append(pid)
    _check_unique(ids, path="purposes", what="purpose")
    return {pid: pid for pid in sorted(ids)}


def _build_windows(raw, purposes) -> tuple[Window, ...]:
    rows = require_list(raw, path="windows", what="windows", nonempty=True)
    built = []
    for index, item in enumerate(rows):
        path = f"windows[{index}]"
        obj = require_mapping(item, path=path, what="window")
        wid = require_str(
            require_field(obj, "window_id", path=path),
            path=f"{path}.window_id",
            what="window_id",
        )
        start = require_int(
            require_field(obj, "start", path=path), path=f"{path}.start", what="start"
        )
        end = require_int(
            require_field(obj, "end", path=path), path=f"{path}.end", what="end"
        )
        if end <= start:
            raise CaseError(
                "invalid-window",
                f"window is half-open [start, end); end ({end}) must exceed start ({start})",
                f"{path}.end",
                wid,
            )
        purpose_rows = require_list(
            require_field(obj, "purpose_ids", path=path),
            path=f"{path}.purpose_ids",
            record=wid,
            what="purpose_ids",
            nonempty=True,
        )
        purpose_ids = []
        for p_index, value in enumerate(purpose_rows):
            pid = require_str(
                value, path=f"{path}.purpose_ids[{p_index}]", what="purpose reference"
            )
            if pid not in purposes:
                raise CaseError(
                    "unknown-purpose",
                    f"window {wid!r} names unknown purpose {pid!r}",
                    f"{path}.purpose_ids[{p_index}]",
                    wid,
                )
            purpose_ids.append(pid)
        if len(set(purpose_ids)) != len(purpose_ids):
            raise CaseError(
                "duplicate-purpose",
                f"window {wid!r} lists a purpose more than once",
                f"{path}.purpose_ids",
                wid,
            )
        cap = _rat(
            require_field(obj, "cap", path=path),
            path=f"{path}.cap",
            record=wid,
            what="budget cap",
            minimum=0,
        )
        built.append(Window(wid, start, end, tuple(sorted(purpose_ids)), cap))
    _check_unique([w.window_id for w in built], path="windows", what="window")
    return tuple(built)


def _common_release_fields(obj, *, path, ident, snapshots, purposes):
    sid = require_str(
        require_field(obj, "snapshot_id", path=path, record=ident),
        path=f"{path}.snapshot_id",
        what="snapshot_id",
    )
    if sid not in snapshots:
        raise CaseError(
            "unknown-snapshot",
            f"{ident!r} names unknown snapshot {sid!r}",
            f"{path}.snapshot_id",
            ident,
        )
    pid = require_str(
        require_field(obj, "purpose_id", path=path, record=ident),
        path=f"{path}.purpose_id",
        what="purpose_id",
    )
    if pid not in purposes:
        raise CaseError(
            "unknown-purpose",
            f"{ident!r} names unknown purpose {pid!r}",
            f"{path}.purpose_id",
            ident,
        )
    occasion = require_int(
        require_field(obj, "occasion", path=path, record=ident),
        path=f"{path}.occasion",
        what="occasion",
    )
    cost = _rat(
        require_field(obj, "cost", path=path, record=ident),
        path=f"{path}.cost",
        record=ident,
        what="exposure cost",
        minimum=0,
    )
    return sid, snapshots[sid].cohort_id, pid, occasion, cost


def _build_releases(raw, snapshots, purposes) -> tuple[Release, ...]:
    rows = require_list(raw, path="releases", what="releases")
    built = []
    for index, item in enumerate(rows):
        path = f"releases[{index}]"
        obj = require_mapping(item, path=path, what="release")
        rid = require_str(
            require_field(obj, "release_id", path=path),
            path=f"{path}.release_id",
            what="release_id",
        )
        batch = require_str(
            require_field(obj, "batch_id", path=path, record=rid),
            path=f"{path}.batch_id",
            what="batch_id",
        )
        sid, cid, pid, occasion, cost = _common_release_fields(
            obj, path=path, ident=rid, snapshots=snapshots, purposes=purposes
        )
        status = require_str(
            require_field(obj, "status", path=path, record=rid),
            path=f"{path}.status",
            what="status",
        )
        if status not in STATUSES:
            raise CaseError(
                "invalid-status",
                f"status must be one of {', '.join(STATUSES)}; got {status!r}",
                f"{path}.status",
                rid,
            )
        mandatory = obj.get("mandatory", False)
        if not isinstance(mandatory, bool):
            raise CaseError(
                "expected-boolean", "mandatory must be true or false",
                f"{path}.mandatory", rid,
            )
        built.append(Release(rid, batch, sid, cid, pid, occasion, cost, status, mandatory))
    _check_unique([r.release_id for r in built], path="releases", what="release")
    return tuple(built)


def _build_candidates(raw, snapshots, purposes) -> tuple[Candidate, ...]:
    rows = require_list(raw, path="candidates", what="candidates")
    built = []
    for index, item in enumerate(rows):
        path = f"candidates[{index}]"
        obj = require_mapping(item, path=path, what="candidate")
        rid = require_str(
            require_field(obj, "release_id", path=path),
            path=f"{path}.release_id",
            what="release_id",
        )
        sid, cid, pid, occasion, cost = _common_release_fields(
            obj, path=path, ident=rid, snapshots=snapshots, purposes=purposes
        )
        value = _rat(
            require_field(obj, "research_value", path=path, record=rid),
            path=f"{path}.research_value",
            record=rid,
            what="research value",
            minimum=0,
        )
        mandatory = obj.get("mandatory", False)
        if not isinstance(mandatory, bool):
            raise CaseError(
                "expected-boolean", "mandatory must be true or false",
                f"{path}.mandatory", rid,
            )
        built.append(Candidate(rid, sid, cid, pid, occasion, cost, value, mandatory))
    _check_unique([c.release_id for c in built], path="candidates", what="candidate")
    return tuple(built)


def _build_linkages(raw, members, participants) -> dict[str, Linkage]:
    rows = require_list(raw, path="linkages", what="linkages", nonempty=True)
    built: dict[str, Linkage] = {}
    for index, item in enumerate(rows):
        path = f"linkages[{index}]"
        obj = require_mapping(item, path=path, what="linkage")
        lid = require_str(
            require_field(obj, "linkage_id", path=path),
            path=f"{path}.linkage_id",
            what="linkage_id",
        )
        effective_from = obj.get("effective_from")
        if effective_from is not None:
            effective_from = require_int(
                effective_from, path=f"{path}.effective_from", what="effective_from"
            )
        effective_to = obj.get("effective_to")
        if effective_to is not None:
            effective_to = require_int(
                effective_to, path=f"{path}.effective_to", what="effective_to"
            )
        if (
            effective_from is not None
            and effective_to is not None
            and effective_to <= effective_from
        ):
            raise CaseError(
                "invalid-validity",
                f"effective_to ({effective_to}) must exceed effective_from "
                f"({effective_from})",
                f"{path}.effective_to",
                lid,
            )
        assignment_rows = require_list(
            require_field(obj, "assignments", path=path),
            path=f"{path}.assignments",
            record=lid,
            what="assignments",
            nonempty=True,
        )
        collected: dict[str, list[str]] = {}
        seen_entries: dict[str, int] = {}
        for a_index, entry in enumerate(assignment_rows):
            a_path = f"{path}.assignments[{a_index}]"
            item_obj = require_mapping(entry, path=a_path, what="assignment")
            mid = require_str(
                require_field(item_obj, "member_id", path=a_path, record=lid),
                path=f"{a_path}.member_id",
                what="member_id",
            )
            if mid not in members:
                raise CaseError(
                    "unknown-member",
                    f"linkage {lid!r} names unknown member {mid!r}",
                    f"{a_path}.member_id",
                    lid,
                )
            if mid in seen_entries:
                raise CaseError(
                    "duplicate-assignment",
                    f"member {mid!r} already has an assignment at index "
                    f"{seen_entries[mid]}; list every admitted participant under "
                    "that single assignment",
                    a_path,
                    lid,
                )
            seen_entries[mid] = a_index
            options: list[str] = []
            if "participant_id" in item_obj:
                options.append(
                    require_str(
                        item_obj["participant_id"],
                        path=f"{a_path}.participant_id",
                        what="participant_id",
                    )
                )
            if "participant_ids" in item_obj:
                option_rows = require_list(
                    item_obj["participant_ids"],
                    path=f"{a_path}.participant_ids",
                    record=lid,
                    what="participant_ids",
                    nonempty=True,
                )
                for p_index, value in enumerate(option_rows):
                    options.append(
                        require_str(
                            value,
                            path=f"{a_path}.participant_ids[{p_index}]",
                            what="participant reference",
                        )
                    )
            if not options:
                raise CaseError(
                    "missing-field",
                    "an assignment needs 'participant_id' or 'participant_ids'",
                    a_path,
                    lid,
                )
            bucket = collected.setdefault(mid, [])
            for pid in options:
                if pid not in participants:
                    raise CaseError(
                        "unknown-participant",
                        f"linkage {lid!r} names unknown participant {pid!r}",
                        f"{a_path}.participant_id",
                        lid,
                    )
                if pid in bucket:
                    raise CaseError(
                        "duplicate-assignment",
                        f"member {mid!r} lists participant {pid!r} twice in "
                        f"linkage {lid!r}",
                        a_path,
                        lid,
                    )
                bucket.append(pid)
        merge_rows = obj.get("merge_groups", [])
        merge_list = require_list(
            merge_rows, path=f"{path}.merge_groups", record=lid, what="merge_groups"
        )
        merge_groups = []
        seen_in_merge: dict[str, int] = {}
        for g_index, group in enumerate(merge_list):
            g_path = f"{path}.merge_groups[{g_index}]"
            group_rows = require_list(
                group, path=g_path, record=lid, what="merge group", nonempty=True
            )
            if len(group_rows) < 2:
                raise CaseError(
                    "invalid-merge-group",
                    "a merge group needs at least two source member ids",
                    g_path,
                    lid,
                )
            group_members = []
            for m_index, value in enumerate(group_rows):
                ref_path = f"{g_path}[{m_index}]"
                mid = require_str(value, path=ref_path, what="member reference")
                if mid not in members:
                    raise CaseError(
                        "unknown-member",
                        f"linkage {lid!r} names unknown member {mid!r}",
                        ref_path,
                        lid,
                    )
                if mid in seen_in_merge:
                    raise CaseError(
                        "overlapping-merge-groups",
                        f"member {mid!r} appears in merge groups "
                        f"{seen_in_merge[mid]} and {g_index}",
                        ref_path,
                        lid,
                    )
                seen_in_merge[mid] = g_index
                group_members.append(mid)
            if len(set(group_members)) != len(group_members):
                raise CaseError(
                    "duplicate-id",
                    "a merge group lists a member more than once",
                    g_path,
                    lid,
                )
            missing = [m for m in group_members if m not in collected]
            if missing:
                raise CaseError(
                    "unlinked-member",
                    f"merge group member {missing[0]!r} has no assignment in "
                    f"linkage {lid!r}",
                    g_path,
                    lid,
                )
            shared = set(collected[group_members[0]])
            for mid in group_members[1:]:
                shared &= set(collected[mid])
            if not shared:
                raise CaseError(
                    "infeasible-merge-group",
                    "merge group has no participant admitted by every member; "
                    "no feasible identity interpretation exists",
                    g_path,
                    lid,
                )
            merge_groups.append(tuple(group_members))
        if lid in built:
            raise CaseError(
                "duplicate-id",
                f"linkage {lid!r} appears more than once",
                f"{path}.linkage_id",
                lid,
            )
        built[lid] = Linkage(
            lid,
            effective_from,
            effective_to,
            tuple((mid, tuple(sorted(options))) for mid, options in collected.items()),
            tuple(merge_groups),
        )
    return built


def _build_audit_views(raw, linkages) -> dict[str, AuditView]:
    rows = require_list(raw, path="audit_views", what="audit_views")
    built = {}
    for index, item in enumerate(rows):
        path = f"audit_views[{index}]"
        obj = require_mapping(item, path=path, what="audit view")
        vid = require_str(
            require_field(obj, "view_id", path=path),
            path=f"{path}.view_id",
            what="view_id",
        )
        lid = require_str(
            require_field(obj, "linkage_id", path=path),
            path=f"{path}.linkage_id",
            what="linkage_id",
        )
        if lid not in linkages:
            raise CaseError(
                "unknown-linkage",
                f"audit view {vid!r} names unknown linkage {lid!r}",
                f"{path}.linkage_id",
                vid,
            )
        if vid in built:
            raise CaseError(
                "duplicate-id", f"audit view {vid!r} appears more than once",
                f"{path}.view_id", vid,
            )
        built[vid] = AuditView(vid, lid)
    return built


def _build_recheck(raw, linkages, views) -> RecheckConfig | None:
    if raw is None:
        return None
    obj = require_mapping(raw, path="recheck", what="recheck")
    vid = require_str(
        require_field(obj, "view_id", path="recheck"),
        path="recheck.view_id",
        what="view_id",
    )
    lid = require_str(
        require_field(obj, "new_linkage", path="recheck"),
        path="recheck.new_linkage",
        what="new_linkage",
    )
    if vid not in views:
        raise CaseError(
            "unknown-view", f"unknown audit view {vid!r}", "recheck.view_id", vid
        )
    if lid not in linkages:
        raise CaseError(
            "unknown-linkage", f"unknown linkage {lid!r}", "recheck.new_linkage", lid
        )
    return RecheckConfig(vid, lid)


def _default_active_linkage(linkages: dict[str, Linkage]) -> str | None:
    if not linkages:
        return None

    def key(item):
        lid, linkage = item
        start = linkage.effective_from
        return (0, start, lid) if start is not None else (1, 0, lid)

    # Latest effective_from wins; tables without a start date come last.
    return sorted(linkages.items(), key=key)[-1][0]


def build_case(data) -> Case:
    root = require_mapping(data, path="case", what="case file")
    version = require_int(
        require_field(root, "case_version", path="case"),
        path="case.case_version",
        what="case_version",
    )
    if version != SUPPORTED_CASE_VERSION:
        raise CaseError(
            "unsupported-version",
            f"case_version must be {SUPPORTED_CASE_VERSION}, got {version}",
            "case.case_version",
            None,
        )
    case_id = require_str(
        require_field(root, "case_id", path="case"),
        path="case.case_id",
        what="case_id",
    )
    participants = _build_participants(require_field(root, "participants", path="case"))
    cohorts = _build_cohorts(require_field(root, "cohorts", path="case"))
    purposes = _build_purposes(require_field(root, "purposes", path="case"))
    members = _build_members(require_field(root, "members", path="case"), cohorts)
    snapshots = _build_snapshots(
        require_field(root, "snapshots", path="case"), cohorts, members
    )
    windows = _build_windows(require_field(root, "windows", path="case"), purposes)
    releases = _build_releases(
        require_field(root, "releases", path="case"), snapshots, purposes
    )
    candidates = _build_candidates(
        require_field(root, "candidates", path="case"), snapshots, purposes
    )
    bound_ids = {r.release_id for r in releases}
    overlap = bound_ids & {c.release_id for c in candidates}
    if overlap:
        first = sorted(overlap)[0]
        raise CaseError(
            "duplicate-id",
            f"a candidate must not reuse a bound release ID: {first!r}",
            "candidates",
            first,
        )
    linkages = _build_linkages(
        require_field(root, "linkages", path="case"), members, participants
    )
    views = _build_audit_views(root.get("audit_views", []), linkages)
    recheck = _build_recheck(root.get("recheck"), linkages, views)

    active = root.get("active_linkage")
    if active is None:
        active = _default_active_linkage(linkages)
    else:
        active = require_str(active, path="active_linkage", what="active_linkage")
        if active not in linkages:
            raise CaseError(
                "unknown-linkage",
                f"active_linkage names unknown linkage {active!r}",
                "active_linkage",
                active,
            )

    return Case(
        case_id=case_id,
        participants=participants,
        cohorts=cohorts,
        purposes=purposes,
        members=members,
        snapshots=snapshots,
        windows=windows,
        releases=releases,
        candidates=candidates,
        linkages=linkages,
        active_linkage_id=active,
        audit_views=views,
        recheck=recheck,
    )


__all__ = [
    "ACCOUNTED_STATUSES",
    "AuditView",
    "Case",
    "Candidate",
    "Cohort",
    "Linkage",
    "Member",
    "Participant",
    "RecheckConfig",
    "Release",
    "Snapshot",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "STATUS_RETRACTED",
    "STATUSES",
    "SUPPORTED_CASE_VERSION",
    "UNASSIGNED",
    "Window",
    "build_case",
    "load_case",
]
