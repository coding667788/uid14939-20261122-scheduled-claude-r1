"""Exact accounting: deduplication, retraction, window boundaries, exactness."""

from __future__ import annotations

import unittest
from fractions import Fraction

from release_budget.audit import run_audit
from release_budget.identity import build_components
from release_budget.ledger import (
    accounted_exposures,
    charge_all,
    charge_participant,
    release_exposure,
)

from .support import (
    CaseTestCase,
    case_from,
    linkage,
    member,
    release,
    simple_case,
    snapshot,
    window,
)


class RepeatedMemberTest(CaseTestCase):
    def test_repeated_member_in_one_release_is_charged_once(self):
        data = simple_case(
            snapshots=[
                snapshot("S-A", "C-A", 1, ["M-A1", "M-A1", "M-A1"]),
                snapshot("S-B", "C-B", 1, ["M-B1"]),
            ],
            releases=[release("R-A", "S-A", "U-1", 1, "3/5")],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        exposures = accounted_exposures(case, components)
        choice = components.choice_witness()
        charges = charge_all(exposures, case.windows[0], choice)
        self.assertEqual(charges, {"P-ADA": Fraction("3/5")})

    def test_multiset_duplicates_do_not_inflate_worst_case(self):
        data = simple_case(
            snapshots=[
                snapshot("S-A", "C-A", 1, ["M-A1", "M-A1", "M-A2"]),
                snapshot("S-B", "C-B", 1, ["M-B1"]),
            ],
            releases=[release("R-A", "S-A", "U-1", 1, "3/5")],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
        )
        _, result = self.audit(data)
        self.assertTrue(result.compliant)


class SharedParticipantTwoCohortsTest(CaseTestCase):
    def test_two_compliant_reports_overrun_when_published_together(self):
        _, result = self.audit(simple_case())
        self.assertFalse(result.compliant)
        self.assertEqual(len(result.violations), 1)
        violation = result.violations[0]
        self.assertEqual(violation.participant_id, "P-ADA")
        self.assertEqual(violation.charge, Fraction("6/5"))
        self.assertEqual(violation.excess, Fraction("1/5"))
        self.assertEqual(violation.release_ids, ("R-A", "R-B"))
        self.assertEqual(
            set(dict(violation.witness).items()),
            {("M-A1", "P-ADA"), ("M-B1", "P-ADA"), ("M-A2", "P-BOR")},
        )

    def test_each_report_alone_is_compliant(self):
        for only in ("R-A", "R-B"):
            data = simple_case(
                releases=[release(only, "S-A" if only == "R-A" else "S-B", "U-1", 1, "3/5")]
            )
            _, result = self.audit(data)
            self.assertTrue(result.compliant, only)


class StatusTest(CaseTestCase):
    def test_draft_is_refunded(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-DRAFT", "S-B", "U-1", 2, "3/5", status="draft"),
            ]
        )
        _, result = self.audit(data)
        self.assertTrue(result.compliant)
        self.assertEqual(result.refunded_release_ids, ("R-DRAFT",))
        self.assertNotIn("R-DRAFT", result.accounted_release_ids)

    def test_retracted_release_still_costs(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-B", "S-B", "U-1", 2, "3/5", status="retracted"),
            ]
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertIn("R-B", result.accounted_release_ids)
        self.assertEqual(result.violations[0].charge, Fraction("6/5"))

    def test_retracted_release_keeps_its_original_snapshot(self):
        # The cohort later shrank (V2 drops M-A1).  Re-publishing a corrected
        # snapshot must not retroactively discount the published release.
        data = simple_case(
            snapshots=[
                snapshot("S-A", "C-A", 1, ["M-A1", "M-A2"]),
                snapshot("S-A2", "C-A", 2, ["M-A2"]),
                snapshot("S-B", "C-B", 1, ["M-B1"]),
            ],
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-B", "S-B", "U-1", 2, "3/5"),
                release("R-NEW", "S-A2", "U-1", 3, "1/10"),
            ],
        )
        _, result = self.audit(data)
        violation = result.violations[0]
        self.assertIn("R-A", violation.release_ids)
        self.assertNotIn("R-NEW", violation.release_ids)


class WindowBoundaryTest(CaseTestCase):
    def test_occasion_at_window_end_is_outside(self):
        data = simple_case(
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
            releases=[
                release("R-EARLY", "S-A", "U-1", 0, "3/5"),
                release("R-EDGE", "S-B", "U-1", 10, "3/5"),
            ],
        )
        _, result = self.audit(data)
        self.assertTrue(result.compliant)
        self.assertEqual(result.violations, ())

    def test_occasion_at_window_start_is_inside(self):
        data = simple_case(
            windows=[window("W-1", 5, 10, ["U-1"], "1")],
            releases=[
                release("R-A", "S-A", "U-1", 5, "3/5"),
                release("R-B", "S-B", "U-1", 9, "3/5"),
            ],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)

    def test_purpose_outside_group_is_not_charged(self):
        data = simple_case(
            purposes=[{"purpose_id": "U-1"}, {"purpose_id": "U-2"}],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
            releases=[
                release("R-A", "S-A", "U-1", 1, "3/5"),
                release("R-B", "S-B", "U-2", 2, "3/5"),
            ],
        )
        _, result = self.audit(data)
        self.assertTrue(result.compliant)


class ExactnessTest(CaseTestCase):
    """The float trap: exact overrun must not be rounded into compliance."""

    def test_strict_overrun_hidden_by_float_is_reported(self):
        # 1/3 + 1/3 = 2/3 exactly, strictly above the decimal cap
        # 0.6666666666666666.  In binary floats 1/3+1/3 == 0.6666666666666666,
        # so an approximate audit would call this compliant.
        cost = "1/3"
        cap = "0.6666666666666666"
        self.assertGreater(Fraction(cost) + Fraction(cost), Fraction(cap))
        self.assertLessEqual(float(Fraction(cost)) + float(Fraction(cost)), float(Fraction(cap)))

        data = simple_case(
            windows=[window("W-1", 0, 10, ["U-1"], cap)],
            releases=[
                release("R-A", "S-A", "U-1", 1, cost),
                release("R-B", "S-B", "U-1", 2, cost),
            ],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertEqual(result.violations[0].charge, Fraction(2, 3))
        self.assertEqual(result.violations[0].excess, Fraction(2, 3) - Fraction(cap))

    def test_no_float_leaks_into_charge_values(self):
        data = simple_case()
        _, result = self.audit(data)
        for violation in result.violations:
            self.assertIsInstance(violation.charge, Fraction)
            self.assertIsInstance(violation.excess, Fraction)


class RetractionRefundTest(CaseTestCase):
    def test_only_draft_refunded_not_retracted(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "1/5"),
                release("R-RETRACTED", "S-A", "U-1", 2, "1/5", status="retracted"),
                release("R-DRAFT", "S-A", "U-1", 3, "1/5", status="draft"),
            ]
        )
        _, result = self.audit(data)
        self.assertEqual(result.accounted_release_ids, ("R-A", "R-RETRACTED"))
        self.assertEqual(result.refunded_release_ids, ("R-DRAFT",))


if __name__ == "__main__":
    unittest.main()
