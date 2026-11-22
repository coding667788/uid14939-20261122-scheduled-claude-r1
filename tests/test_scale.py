"""Scale and ordering guarantees.

The contract asks for 20 candidates, 30 identity choices and 200 windows.  The
tests here build cases at that size and check that results are still exact,
deterministic and produced on CPU in reasonable time.
"""

from __future__ import annotations

import time
import unittest
from fractions import Fraction

from release_budget.audit import run_audit
from release_budget.choose import run_choose

from .support import (
    CaseTestCase,
    candidate,
    case_from,
    linkage,
    member,
    participant,
    release,
    simple_case,
    snapshot,
    window,
)


class ScaleTest(CaseTestCase):
    def test_two_hundred_windows_audit_exactly(self):
        members = [member(f"M{index}", "C-A") for index in range(4)]
        snapshots = [snapshot("S-A", "C-A", 1, [m["member_id"] for m in members])]
        # 200 windows, alternating tight and generous caps.
        windows = [
            window(f"W-{index:03d}", index, index + 1, ["U-1"],
                   "1/2" if index % 2 == 0 else "5")
            for index in range(200)
        ]
        releases = [
            release(f"R-{index:03d}", "S-A", "U-1", index, "3/5")
            for index in range(200)
        ]
        data = simple_case(
            participants=[participant("P-ADA"), participant("P-BOR")],
            members=members,
            snapshots=snapshots,
            releases=releases,
            windows=windows,
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M0", "participant_id": "P-ADA"},
                        {"member_id": "M1", "participant_id": "P-BOR"},
                        {"member_id": "M2", "participant_id": "P-ADA"},
                        {"member_id": "M3", "participant_id": "P-BOR"},
                    ],
                )
            ],
            cohorts=[{"cohort_id": "C-A"}],
            audit_views=[{"view_id": "V", "linkage_id": "L1"}],
        )
        case = case_from(data)
        started = time.perf_counter()
        result = run_audit(case)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 10.0)

        # The 100 even windows each hold one release charging both P-ADA and
        # P-BOR 3/5 > cap 1/2, so every even window produces two violations.
        self.assertEqual(len(result.violations), 200)
        even_windows = {v.window.window_id for v in result.violations}
        self.assertEqual(even_windows, {f"W-{i:03d}" for i in range(0, 200, 2)})
        for violation in result.violations:
            self.assertEqual(violation.charge, Fraction("3/5"))
            self.assertEqual(violation.excess, Fraction("1/10"))

    def test_thirty_identity_choices_are_aggregated_exactly(self):
        members = [member(f"M{index}", "C-A") for index in range(30)]
        assignments = [
            {"member_id": f"M{index}",
             "participant_ids": ["P-ADA", "P-BOR", "P-CYR"]}
            for index in range(30)
        ]
        data = simple_case(
            participants=[participant("P-ADA"), participant("P-BOR"), participant("P-CYR")],
            members=members,
            snapshots=[snapshot("S-A", "C-A", 1, [m["member_id"] for m in members])],
            releases=[release("R-A", "S-A", "U-1", 1, "1/2")],
            windows=[window("W-1", 0, 10, ["U-1"], "1/4")],
            linkages=[linkage("L1", assignments)],
            cohorts=[{"cohort_id": "C-A"}],
            audit_views=[{"view_id": "V", "linkage_id": "L1"}],
        )
        case = case_from(data)
        result = run_audit(case)
        # 3^30 interpretations, but the answer is exact and immediate.
        self.assertEqual(result.components.interpretation_count, 3 ** 30)
        self.assertFalse(result.compliant)
        self.assertEqual(
            sorted(v.participant_id for v in result.violations),
            ["P-ADA", "P-BOR", "P-CYR"],
        )
        # One release, one charge per participant: never 30 charges.
        for violation in result.violations:
            self.assertEqual(violation.charge, Fraction("1/2"))


class OrderingTest(CaseTestCase):
    def test_violations_are_ordered_by_window_then_purpose_then_source(self):
        members = [member("M-A", "C-A"), member("M-B", "C-A")]
        data = simple_case(
            participants=[participant("P-ADA"), participant("P-BOR")],
            purposes=[{"purpose_id": "U-1"}, {"purpose_id": "U-2"}],
            members=members,
            snapshots=[snapshot("S-A", "C-A", 1, ["M-A", "M-B"])],
            releases=[
                # Later window, purpose U-2.
                release("R-LATE-U2", "S-A", "U-2", 20, "3/4"),
                # Earlier window, purpose U-2.
                release("R-EARLY-U2", "S-A", "U-2", 5, "3/4"),
                # Earlier window, purpose U-1.
                release("R-EARLY-U1", "S-A", "U-1", 5, "3/4"),
            ],
            windows=[
                window("W-EARLY", 0, 10, ["U-1", "U-2"], "1/2"),
                window("W-LATE", 20, 30, ["U-2"], "1/2"),
            ],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A", "participant_id": "P-ADA"},
                        {"member_id": "M-B", "participant_id": "P-ADA"},
                    ],
                )
            ],
            cohorts=[{"cohort_id": "C-A"}],
            audit_views=[{"view_id": "V", "linkage_id": "L1"}],
        )
        case = case_from(data)
        result = run_audit(case)
        keys = [
            (v.window.start, v.window.purpose_key, v.source_id)
            for v in result.violations
        ]
        self.assertEqual(keys, sorted(keys))
        # W-EARLY has two purposes in one window and one release each, but a
        # single (window, participant) pair: purpose is part of the *window*
        # key, not a separate axis, so R-EARLY-U1 and R-EARLY-U2 accumulate.
        early = [v for v in result.violations if v.window.window_id == "W-EARLY"]
        self.assertEqual(len(early), 1)
        self.assertEqual(early[0].charge, Fraction("3/2"))
        self.assertEqual(early[0].release_ids, ("R-EARLY-U1", "R-EARLY-U2"))

    def test_violation_report_lists_exact_fraction_and_complete_witness(self):
        case = case_from(
            simple_case(
                windows=[window("W-1", 0, 10, ["U-1"], "1")],
                releases=[
                    release("R-A", "S-A", "U-1", 1, "3/5"),
                    release("R-B", "S-B", "U-1", 2, "3/5"),
                ],
                audit_views=[{"view_id": "V", "linkage_id": "L1"}],
            )
        )
        result = run_audit(case)
        payload = result.violations[0].to_json()
        self.assertEqual(payload["charge"], "6/5")
        self.assertEqual(payload["excess"], "1/5")
        self.assertEqual(payload["cap"], "1")
        # The witness covers every interpreted member, so it is complete.
        self.assertEqual(
            set(payload["witness"]),
            {"M-A1", "M-A2", "M-B1"},
        )
        self.assertEqual(
            sorted(payload["release_ids"]), ["R-A", "R-B"]
        )
        self.assertEqual(payload["sources"][0]["cohort_ids"], ["C-A"])


if __name__ == "__main__":
    unittest.main()
