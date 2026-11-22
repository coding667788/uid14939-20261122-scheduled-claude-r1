"""Regression cases the contract calls out explicitly.

Each test names one clause of the contract and pins it with a small, fully
enumerated case:

* merge: old accounts charged together, but one shared charge inside a single
  release;
* split: the same source member must take the same participant in every
  release, so choices are made jointly;
* exactly at the window end: the window is half-open;
* a retracted report is not refunded;
* exact rational arithmetic catches an overrun that floats would round away.
"""

from __future__ import annotations

import itertools
import unittest
from fractions import Fraction

from release_budget.audit import audit_with_linkage
from release_budget.bruteforce import brute_force_audit, enumerate_choices
from release_budget.identity import build_components

from .support import (
    CaseTestCase,
    case_from,
    linkage,
    member,
    participant,
    release,
    simple_case,
    snapshot,
    window,
)


class MergeRegressionTest(CaseTestCase):
    def test_merged_history_is_combined_but_deduplicated_once_per_release(self):
        # Two old numbers, each used by its own published release, plus one
        # release that mentions both.  The merged person is charged
        # 1/3 + 1/3 + 1/3 (one charge for the joint release), i.e. 1.
        data = simple_case(
            members=[member("M-OLD1", "C-A"), member("M-OLD2", "C-A")],
            snapshots=[
                snapshot("S-1", "C-A", 1, ["M-OLD1"]),
                snapshot("S-2", "C-A", 2, ["M-OLD2"]),
                snapshot("S-3", "C-A", 3, ["M-OLD1", "M-OLD2"]),
            ],
            releases=[
                release("R-1", "S-1", "U-1", 1, "1/3"),
                release("R-2", "S-2", "U-1", 2, "1/3"),
                release("R-3", "S-3", "U-1", 3, "1/3"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "9/10")],
            linkages=[
                linkage(
                    "L-MERGED",
                    [
                        {"member_id": "M-OLD1", "participant_id": "P-ADA"},
                        {"member_id": "M-OLD2", "participant_id": "P-ADA"},
                    ],
                    merge_groups=[["M-OLD1", "M-OLD2"]],
                )
            ],
            active_linkage="L-MERGED",
            audit_views=[{"view_id": "V", "linkage_id": "L-MERGED"}],
            cohorts=[{"cohort_id": "C-A"}],
        )
        case = case_from(data)
        result = audit_with_linkage(case, case.linkage("L-MERGED"))
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].charge, Fraction("1"))
        self.assertEqual(
            result.violations[0].release_ids, ("R-1", "R-2", "R-3")
        )
        # The proof is that 3 x 1/3 = 1 > 9/10 exactly.
        self.assertEqual(result.violations[0].excess, Fraction("1/10"))

        components = build_components(case.linkage("L-MERGED"))
        slow = brute_force_audit(case, components)
        self.assertEqual(slow.worst[("W-1", "P-ADA")], Fraction("1"))
        self.assertFalse(slow.compliant)

    def test_merge_without_group_double_charges(self):
        # Same numbers, but *without* the merge group the two old ids are two
        # independent people: the joint release charges both, and nobody
        # exceeds 2/3.  This pins that the dedup above really comes from the
        # merge group, not from the snapshot.
        data = simple_case(
            participants=[participant("P-ADA"), participant("P-BOR")],
            members=[member("M-O1", "C-A"), member("M-O2", "C-A")],
            snapshots=[snapshot("S-1", "C-A", 1, ["M-O1", "M-O2"])],
            releases=[release("R-1", "S-1", "U-1", 1, "2/3")],
            windows=[window("W-1", 0, 10, ["U-1"], "2/3")],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-O1", "participant_id": "P-ADA"},
                        {"member_id": "M-O2", "participant_id": "P-BOR"},
                    ],
                )
            ],
            cohorts=[{"cohort_id": "C-A"}],
        )
        _, result = self.audit(data)
        self.assertTrue(result.compliant)


class SplitRegressionTest(CaseTestCase):
    def test_split_member_is_chosen_once_across_all_releases(self):
        # A single source member split between two candidates, used by two
        # releases.  There are exactly two interpretations -- never four --
        # because both releases must agree about the member.
        data = simple_case(
            members=[member("M-S", "C-A")],
            snapshots=[
                snapshot("S-1", "C-A", 1, ["M-S"]),
                snapshot("S-2", "C-A", 2, ["M-S"]),
            ],
            releases=[
                release("R-1", "S-1", "U-1", 1, "1/2"),
                release("R-2", "S-2", "U-1", 2, "1/2"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "3/4")],
            linkages=[
                linkage(
                    "L1",
                    [{"member_id": "M-S", "participant_ids": ["P-ADA", "P-BOR"]}],
                )
            ],
            cohorts=[{"cohort_id": "C-A"}],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        self.assertEqual(components.interpretation_count, 2)
        self.assertEqual(len(list(enumerate_choices(components))), 2)

        # Both releases charge whoever owns the member: 1/2 + 1/2 = 1 > 3/4.
        result = audit_with_linkage(case, case.linkage("L1"))
        self.assertEqual(
            sorted(v.participant_id for v in result.violations),
            ["P-ADA", "P-BOR"],
        )
        for violation in result.violations:
            self.assertEqual(violation.charge, Fraction("1"))

        slow = brute_force_audit(case, components)
        self.assertEqual(slow.choices_enumerated, 2)
        self.assertFalse(slow.compliant)
        self.assertEqual(slow.worst[("W-1", "P-ADA")], Fraction("1"))
        self.assertEqual(slow.worst[("W-1", "P-BOR")], Fraction("1"))

    def test_independent_choices_would_have_understated_the_overrun(self):
        # If the two releases could disagree (the bug this rule forbids), each
        # participant would be charged 1/2 and the case would look compliant.
        # The joint choice makes each participant 1, hence over cap.
        data = simple_case(
            members=[member("M-S", "C-A")],
            snapshots=[
                snapshot("S-1", "C-A", 1, ["M-S"]),
                snapshot("S-2", "C-A", 2, ["M-S"]),
            ],
            releases=[
                release("R-1", "S-1", "U-1", 1, "1/2"),
                release("R-2", "S-2", "U-1", 2, "1/2"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "3/4")],
            linkages=[
                linkage(
                    "L1",
                    [{"member_id": "M-S", "participant_ids": ["P-ADA", "P-BOR"]}],
                )
            ],
            cohorts=[{"cohort_id": "C-A"}],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        per_interpretation_independent = Fraction("1/2")
        self.assertLessEqual(per_interpretation_independent, Fraction("3/4"))


class WindowEndRegressionTest(CaseTestCase):
    def test_occasion_exactly_at_end_is_excluded(self):
        data = simple_case(
            releases=[
                release("R-IN", "S-A", "U-1", 0, "1/2"),
                release("R-END", "S-B", "U-1", 10, "1/2"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "3/4")],
        )
        case = case_from(data)
        result = audit_with_linkage(case, case.linkage("L1"))
        self.assertTrue(result.compliant)
        # Under a closed-interval reading the shared person would reach 1 and
        # overrun; the half-open rule is what keeps this compliant.
        self.assertEqual(Fraction("1/2") + Fraction("1/2"), Fraction("1"))
        slow = brute_force_audit(case, build_components(case.linkage("L1")))
        # The shared person is charged 1/2 once (R-IN only); the other S-A
        # member is a separate participant, also at 1/2.  Both are inside cap.
        self.assertEqual(slow.worst[("W-1", "P-ADA")], Fraction("1/2"))
        self.assertNotIn(("W-1", "P-ADA"), {key for key, v in slow.worst.items() if v > Fraction("1/2")})

    def test_last_inside_occasion_is_exactly_end_minus_one(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 9, "3/5"),
                release("R-B", "S-B", "U-1", 9, "3/5"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertEqual(result.violations[0].charge, Fraction("6/5"))


class RetractionRegressionTest(CaseTestCase):
    def test_retracted_report_does_not_refund(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-RETRACT", "S-B", "U-1", 2, "3/5", status="retracted"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
        )
        case = case_from(data)
        result = audit_with_linkage(case, case.linkage("L1"))
        self.assertFalse(result.compliant)
        self.assertIn("R-RETRACT", result.accounted_release_ids)
        self.assertEqual(result.violations[0].charge, Fraction("6/5"))

    def test_unsubmitted_draft_is_refunded(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-DRAFT", "S-B", "U-1", 2, "3/5", status="draft"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
        )
        case = case_from(data)
        result = audit_with_linkage(case, case.linkage("L1"))
        self.assertTrue(result.compliant)
        self.assertEqual(result.refunded_release_ids, ("R-DRAFT",))

    def test_retraction_then_reissue_costs_both(self):
        data = simple_case(
            releases=[
                release("R-OLD", "S-A", "U-1", 1, "3/5", status="retracted"),
                release("R-NEW", "S-B", "U-1", 2, "3/5", status="published"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertEqual(result.violations[0].release_ids, ("R-NEW", "R-OLD"))


class FloatTrapRegressionTest(CaseTestCase):
    def test_float_would_call_strict_overrun_compliant(self):
        # 0.1 + 0.2 + 0.3 in binary floats is 0.6000000000000001; the exact
        # sum of the decimal literals is 3/5.  A cap of 0.6 is *exactly* met,
        # so this is compliant either way -- but a cap just below is not.
        cap = "59999999999999999/100000000000000000"
        self.assertGreater(Fraction("1/10") + Fraction("1/5") + Fraction("3/10"),
                           Fraction(cap))
        # Exact: 3/5 > cap, so non-compliant.  Float: (0.1+0.2+0.3) rounds to
        # 0.6000000000000001, which would *not* be <= 0.6 -- so use a cap that
        # floats also miss, to show the audit does not rely on either side.

        # The sharper trap: two releases of 1/3 each, cap 0.6666666666666666.
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "1/3"),
                release("R-B", "S-B", "U-1", 2, "1/3"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "0.6666666666666666")],
        )
        case = case_from(data)
        result = audit_with_linkage(case, case.linkage("L1"))
        self.assertFalse(result.compliant)
        self.assertEqual(result.violations[0].charge, Fraction(2, 3))
        self.assertEqual(
            result.violations[0].excess,
            Fraction(2, 3) - Fraction("0.6666666666666666"),
        )
        # And the float comparison really does hide it.
        self.assertLessEqual(
            float(Fraction("1/3")) + float(Fraction("1/3")),
            float(Fraction("0.6666666666666666")),
        )

    def test_exact_sum_equal_to_cap_is_compliant(self):
        # 3 x 1/9 = 1/3 exactly; cap 1/3.  Equality is *within* budget.
        data = simple_case(
            members=[member("M-A1", "C-A")],
            snapshots=[snapshot("S-A", "C-A", 1, ["M-A1"])],
            releases=[
                release("R-1", "S-A", "U-1", 1, "1/9"),
                release("R-2", "S-A", "U-1", 2, "1/9"),
                release("R-3", "S-A", "U-1", 3, "1/9"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "1/3")],
            linkages=[
                linkage(
                    "L1",
                    [{"member_id": "M-A1", "participant_id": "P-ADA"}],
                )
            ],
            cohorts=[{"cohort_id": "C-A"}],
        )
        _, result = self.audit(data)
        self.assertTrue(result.compliant)

    def test_one_ulps_worth_over_is_reported(self):
        # Cap is one tenth-of-a-quadrillion below the exact sum.
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "1/3"),
                release("R-B", "S-B", "U-1", 2, "1/3"),
            ],
            windows=[
                window("W-1", 0, 10, ["U-1"], "3333333333333333/5000000000000000")
            ],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertEqual(
            result.violations[0].excess,
            Fraction(2, 3) - Fraction(3333333333333333, 5000000000000000),
        )


if __name__ == "__main__":
    unittest.main()
