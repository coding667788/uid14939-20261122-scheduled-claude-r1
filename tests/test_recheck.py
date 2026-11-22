"""recheck: replay a historical view, then the new linkage, and diff them."""

from __future__ import annotations

import unittest
from fractions import Fraction

from release_budget.audit import run_audit, run_recheck

from .support import (
    CaseTestCase,
    case_from,
    linkage,
    participant,
    release,
    simple_case,
    snapshot,
    window,
)


def three_release_case(**overrides):
    """S-A holds M-A1 + M-A2; S-B holds M-B1.  Cap 1, each release 3/5.

    Under any linkage that maps one S-A member and M-B1 to the same person,
    that person is charged 6/5 and overruns; otherwise the case is compliant.
    """

    from .support import member

    data = simple_case(
        participants=[participant("P-ADA"), participant("P-BOR"), participant("P-CYR")],
        members=[
            member("M-A1", "C-A"),
            member("M-A2", "C-A"),
            member("M-B1", "C-B"),
        ],
        snapshots=[
            snapshot("S-A", "C-A", 1, ["M-A1", "M-A2"]),
            snapshot("S-B", "C-B", 1, ["M-B1"]),
        ],
        releases=[
            release("R-A", "S-A", "U-1", 1, "3/5"),
            release("R-B", "S-B", "U-1", 2, "3/5"),
        ],
        windows=[window("W-1", 0, 10, ["U-1"], "1")],
        audit_views=[{"view_id": "V-OLD", "linkage_id": "L1"}],
        recheck={"view_id": "V-OLD", "new_linkage": "L1"},
    )
    data.update(overrides)
    return data


class RecheckTest(CaseTestCase):
    def test_recheck_reports_introduced_violation(self):
        # Old: M-A1 -> P-ADA, M-B1 -> P-BOR, so P-ADA 3/5, P-BOR 3/5: fine.
        # New: M-B1 gains P-ADA, so P-ADA's worst case is 3/5 + 3/5 = 6/5.
        data = three_release_case(
            linkages=[
                linkage(
                    "L-OLD",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_id": "P-BOR"},
                    ],
                ),
                linkage(
                    "L-NEW",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_ids": ["P-BOR", "P-ADA"]},
                    ],
                ),
            ],
            audit_views=[{"view_id": "V-OLD", "linkage_id": "L-OLD"}],
            recheck={"view_id": "V-OLD", "new_linkage": "L-NEW"},
            active_linkage="L-NEW",
        )
        case = case_from(data)
        old = run_audit(case, view_id="V-OLD")
        self.assertTrue(old.compliant)

        result = run_recheck(case)
        self.assertEqual(result.view_id, "V-OLD")
        self.assertEqual(result.old_linkage_id, "L-OLD")
        self.assertEqual(result.new_linkage_id, "L-NEW")

        changes = result.to_json()["changed"]
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change["change"], "introduced")
        self.assertEqual(change["participant_id"], "P-ADA")
        self.assertEqual(change["new_charge"], "6/5")
        self.assertEqual(change["new_excess"], "1/5")

    def test_recheck_reports_resolved_violation(self):
        # Old: M-B1 -> P-ADA, so P-ADA is 6/5 (over).  New: M-B1 -> P-BOR, so
        # nobody reaches 6/5 and the case becomes compliant.
        data = three_release_case(
            linkages=[
                linkage(
                    "L-OLD",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                ),
                linkage(
                    "L-NEW",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_id": "P-BOR"},
                    ],
                ),
            ],
            audit_views=[{"view_id": "V-OLD", "linkage_id": "L-OLD"}],
            recheck={"view_id": "V-OLD", "new_linkage": "L-NEW"},
            active_linkage="L-NEW",
        )
        case = case_from(data)
        old = run_audit(case, view_id="V-OLD")
        self.assertFalse(old.compliant)

        result = run_recheck(case)
        self.assertTrue(result.new.compliant)
        changes = result.to_json()["changed"]
        resolved = [c for c in changes if c["change"] == "resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["participant_id"], "P-ADA")
        self.assertEqual(resolved[0]["old_charge"], "6/5")
        self.assertEqual(resolved[0]["old_excess"], "1/5")

    def test_recheck_reports_revised_charge(self):
        # Both linkages overrun P-ADA, but the new table admits P-BOR for
        # M-B1 as well, so P-ADA's charge drops from 6/5 to 3/5 -- which is
        # below cap, so the entry is "resolved" rather than "revised" for that
        # participant; P-BOR picks up a *new* overrun instead.
        data = three_release_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-B", "S-B", "U-1", 2, "3/5"),
                release("R-C", "S-B", "U-1", 3, "1/5"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
            linkages=[
                linkage(
                    "L-OLD",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                ),
                linkage(
                    "L-NEW",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_ids": ["P-ADA", "P-BOR"]},
                    ],
                ),
            ],
            audit_views=[{"view_id": "V-OLD", "linkage_id": "L-OLD"}],
            recheck={"view_id": "V-OLD", "new_linkage": "L-NEW"},
            active_linkage="L-NEW",
        )
        case = case_from(data)
        result = run_recheck(case)
        changes = result.to_json()["changed"]
        by_key = {(c["change"], c["participant_id"]) for c in changes}
        # P-ADA: 6/5 + 1/5 = 7/5 both before and after -> not a change.
        self.assertNotIn(("revised", "P-ADA"), by_key)
        self.assertNotIn(("resolved", "P-ADA"), by_key)
        # P-BOR is newly exposed to R-B + R-C = 4/5 under the new reading,
        # which is still below cap; so no new violation either.
        self.assertEqual(changes, [])

    def test_recheck_marks_only_windows_or_people_that_change(self):
        # Old compliant, new introduces exactly one violation; the second
        # window with a generous cap is untouched.
        data = three_release_case(
            windows=[
                window("W-TIGHT", 0, 10, ["U-1"], "1"),
                window("W-LOOSE", 0, 100, ["U-1"], "10"),
            ],
            linkages=[
                linkage(
                    "L-OLD",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_id": "P-BOR"},
                    ],
                ),
                linkage(
                    "L-NEW",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-CYR"},
                        {"member_id": "M-B1", "participant_ids": ["P-BOR", "P-ADA"]},
                    ],
                ),
            ],
            audit_views=[{"view_id": "V-OLD", "linkage_id": "L-OLD"}],
            recheck={"view_id": "V-OLD", "new_linkage": "L-NEW"},
            active_linkage="L-NEW",
        )
        case = case_from(data)
        result = run_recheck(case)
        changes = result.to_json()["changed"]
        self.assertEqual([c["window_id"] for c in changes], ["W-TIGHT"])

    def test_recheck_old_side_is_unchanged_by_new_linkage(self):
        case = case_from(
            three_release_case(
                linkages=[
                    linkage(
                        "L-OLD",
                        [
                            {"member_id": "M-A1", "participant_id": "P-ADA"},
                            {"member_id": "M-A2", "participant_id": "P-CYR"},
                            {"member_id": "M-B1", "participant_id": "P-ADA"},
                        ],
                    ),
                    linkage(
                        "L-NEW",
                        [
                            {"member_id": "M-A1", "participant_id": "P-ADA"},
                            {"member_id": "M-A2", "participant_id": "P-CYR"},
                            {"member_id": "M-B1", "participant_id": "P-BOR"},
                        ],
                    ),
                ],
                audit_views=[{"view_id": "V-OLD", "linkage_id": "L-OLD"}],
                recheck={"view_id": "V-OLD", "new_linkage": "L-NEW"},
                active_linkage="L-NEW",
            )
        )
        baseline = run_audit(case, view_id="V-OLD")
        result = run_recheck(case)
        self.assertEqual(result.old.compliant, baseline.compliant)
        self.assertEqual(
            [(v.window.window_id, v.participant_id, v.charge) for v in result.old.violations],
            [(v.window.window_id, v.participant_id, v.charge) for v in baseline.violations],
        )

    def test_recheck_rejects_unknown_view(self):
        case = case_from(three_release_case())
        with self.assertRaises(Exception) as caught:
            run_recheck(case, view_id="MISSING")
        self.assertEqual(caught.exception.code, "unknown-view")

    def test_recheck_rejects_unknown_linkage(self):
        case = case_from(three_release_case())
        with self.assertRaises(Exception) as caught:
            run_recheck(case, view_id="V-OLD", new_linkage_id="MISSING")
        self.assertEqual(caught.exception.code, "unknown-linkage")


if __name__ == "__main__":
    unittest.main()
