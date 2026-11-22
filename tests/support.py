"""Shared fixtures for the release-budget test suite.

Cases are built in Python rather than loaded from disk so each test can tweak
exactly one contract rule and pin the consequence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from release_budget.audit import run_audit, run_recheck
from release_budget.bruteforce import brute_force_audit
from release_budget.casefile import build_case
from release_budget.identity import build_components


def participant(pid):
    return {"participant_id": pid}


def cohort(cid):
    return {"cohort_id": cid}


def member(mid, cid):
    return {"member_id": mid, "cohort_id": cid}


def snapshot(sid, cid, version, members):
    return {"snapshot_id": sid, "cohort_id": cid, "version": version, "members": list(members)}


def purpose(pid):
    return {"purpose_id": pid}


def window(wid, start, end, purposes, cap):
    return {
        "window_id": wid,
        "start": start,
        "end": end,
        "purpose_ids": list(purposes),
        "cap": cap,
    }


def release(
    rid,
    sid,
    pid,
    occasion,
    cost,
    status="published",
    batch="B-1",
    mandatory=False,
):
    body = {
        "release_id": rid,
        "batch_id": batch,
        "snapshot_id": sid,
        "purpose_id": pid,
        "occasion": occasion,
        "cost": cost,
        "status": status,
    }
    if mandatory:
        body["mandatory"] = True
    return body


def candidate(rid, sid, pid, occasion, cost, value, mandatory=False):
    return {
        "release_id": rid,
        "snapshot_id": sid,
        "purpose_id": pid,
        "occasion": occasion,
        "cost": cost,
        "research_value": value,
        "mandatory": mandatory,
    }


def linkage(lid, assignments, merge_groups=None, effective_from=None, effective_to=None):
    body = {"linkage_id": lid, "assignments": list(assignments)}
    if merge_groups:
        body["merge_groups"] = [list(group) for group in merge_groups]
    if effective_from is not None:
        body["effective_from"] = effective_from
    if effective_to is not None:
        body["effective_to"] = effective_to
    return body


def simple_case(**overrides):
    """A minimal two-cohort case with one shared participant.

    P-ADA is recorded in both cohorts as M-A1 and M-B1; each release costs 3/5
    and the window cap is 1, so co-publishing overruns by 1/5.
    """

    data = {
        "case_version": 1,
        "case_id": "test-case",
        "active_linkage": "L1",
        "participants": [participant("P-ADA"), participant("P-BOR")],
        "cohorts": [cohort("C-A"), cohort("C-B")],
        "purposes": [purpose("U-1")],
        "members": [
            member("M-A1", "C-A"),
            member("M-A2", "C-A"),
            member("M-B1", "C-B"),
        ],
        "snapshots": [
            snapshot("S-A", "C-A", 1, ["M-A1", "M-A2"]),
            snapshot("S-B", "C-B", 1, ["M-B1"]),
        ],
        "windows": [window("W-1", 0, 10, ["U-1"], "1")],
        "releases": [
            release("R-A", "S-A", "U-1", 1, "3/5"),
            release("R-B", "S-B", "U-1", 2, "3/5"),
        ],
        "candidates": [],
        "linkages": [
            linkage(
                "L1",
                [
                    {"member_id": "M-A1", "participant_id": "P-ADA"},
                    {"member_id": "M-A2", "participant_id": "P-BOR"},
                    {"member_id": "M-B1", "participant_id": "P-ADA"},
                ],
            )
        ],
        "audit_views": [{"view_id": "V1", "linkage_id": "L1"}],
    }
    data.update(overrides)
    return data


def case_from(data):
    return build_case(data)


def write_case(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def run_module(args, *, cwd=None):
    """Run ``python -m release_budget`` in a subprocess and capture the object."""

    env = dict(os.environ)
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "release_budget", *args],
        cwd=cwd or package_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return completed


class CaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def audit(self, data, **kwargs):
        case = case_from(data)
        return case, run_audit(case, **kwargs)

    def brute(self, data):
        case = case_from(data)
        components = build_components(case.linkage(case.active_linkage_id))
        return brute_force_audit(case, components)

    def assertExact(self, value, expected):
        from fractions import Fraction

        self.assertEqual(value, Fraction(expected))
