"""Selection: hard mandatory items, ordering objective, tie-breaks."""

from __future__ import annotations

import unittest
from fractions import Fraction

from release_budget.choose import run_choose
from release_budget.identity import build_components

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


def choose_case(**overrides):
    data = simple_case(
        releases=[release("R-BASE", "S-A", "U-1", 1, "1/4")],
        candidates=[
            candidate("CAND-1", "S-A", "U-1", 2, "1/4", "5"),
            candidate("CAND-2", "S-A", "U-1", 3, "1/4", "3"),
            candidate("CAND-3", "S-A", "U-1", 4, "1/4", "2"),
        ],
        windows=[window("W-1", 0, 10, ["U-1"], "1")],
    )
    data.update(overrides)
    return data


class ChooseTest(CaseTestCase):
    def choose(self, data):
        case = case_from(data)
        return case, run_choose(case)

    def test_fits_as_many_valueful_items_as_budget_allows(self):
        case, selection = self.choose(choose_case())
        self.assertTrue(selection.feasible)
        # Base 1/4 leaves 3/4: at most three of 1/4, and all three fit.
        self.assertEqual(selection.selected, ("CAND-1", "CAND-2", "CAND-3"))
        self.assertEqual(selection.research_value, Fraction("10"))

    def test_budget_binds_and_value_ordering_decides(self):
        case, selection = self.choose(
            choose_case(
                candidates=[
                    candidate("CAND-LOW", "S-A", "U-1", 2, "1/2", "1"),
                    candidate("CAND-HIGH", "S-A", "U-1", 3, "1/2", "9"),
                ],
                windows=[window("W-1", 0, 10, ["U-1"], "3/4")],
            )
        )
        # Base 1/4 leaves 1/2: exactly one 1/2 item fits; take the higher value.
        self.assertEqual(selection.optional, ("CAND-HIGH",))
        self.assertEqual(selection.research_value, Fraction("9"))

    def test_mandatory_candidate_is_always_kept(self):
        case, selection = self.choose(
            choose_case(
                candidates=[
                    candidate("CAND-MUST", "S-A", "U-1", 2, "1/2", "0", mandatory=True),
                    candidate("CAND-OPT", "S-A", "U-1", 3, "1/4", "9"),
                ],
            )
        )
        self.assertTrue(selection.feasible)
        self.assertIn("CAND-MUST", selection.selected)
        self.assertEqual(selection.mandatory, ("CAND-MUST",))
        # Base 1/4 + mandatory 1/2 leaves 1/4, so the optional 1/4 also fits.
        self.assertEqual(selection.selected, ("CAND-MUST", "CAND-OPT"))

    def test_infeasible_when_mandatory_breaks_budget(self):
        case, selection = self.choose(
            choose_case(
                releases=[release("R-BASE", "S-A", "U-1", 1, "9/10")],
                candidates=[
                    candidate("CAND-MUST", "S-A", "U-1", 2, "1/2", "0", mandatory=True)
                ],
                windows=[window("W-1", 0, 10, ["U-1"], "1")],
            )
        )
        self.assertFalse(selection.feasible)
        self.assertEqual(selection.selected, ())
        self.assertIn("CAND-MUST", selection.mandatory)
        self.assertEqual(selection.blocked[0]["window_id"], "W-1")

    def test_infeasible_when_baseline_alone_overruns(self):
        case, selection = self.choose(
            choose_case(
                releases=[
                    release("R-1", "S-A", "U-1", 1, "3/5"),
                    release("R-2", "S-B", "U-1", 2, "3/5"),
                ],
                candidates=[],
                windows=[window("W-1", 0, 10, ["U-1"], "1")],
            )
        )
        self.assertFalse(selection.feasible)
        self.assertFalse(selection.baseline_compliant)

    def test_safety_holds_under_every_identity_interpretation(self):
        # The candidate's snapshot holds a member that is ambiguous between
        # two participants; the chosen subset must be safe for both.
        case, selection = self.choose(
            simple_case(
                participants=[participant("P-ADA"), participant("P-BOR")],
                members=[member("M-A1", "C-A")],
                snapshots=[snapshot("S-A", "C-A", 1, ["M-A1"])],
                releases=[release("R-BASE", "S-A", "U-1", 1, "1/10")],
                candidates=[
                    candidate("CAND-1", "S-A", "U-1", 2, "2/5", "5"),
                    candidate("CAND-2", "S-A", "U-1", 3, "2/5", "4"),
                ],
                windows=[window("W-1", 0, 10, ["U-1"], "9/10")],
                linkages=[
                    linkage(
                        "L1",
                        [{"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR"]}],
                    )
                ],
            )
        )
        self.assertTrue(selection.feasible)
        # Base 1/10 + both candidates = 9/10, exactly at cap: both fit.
        self.assertEqual(selection.selected, ("CAND-1", "CAND-2"))
        self.assertEqual(selection.research_value, Fraction("9"))

    def test_second_objective_minimises_added_worst_case(self):
        # Two equal-value candidates are mutually exclusive.  Candidate A adds
        # 1/5 to a low-baseline cell (total 1/5); candidate B adds 2/5 to a
        # cell that is otherwise empty (total 2/5).
        #
        # The contract says "minimise the *added* worst personal consumption",
        # so A wins (1/5 < 2/5).  A "minimise the resulting worst consumption"
        # reading would also pick... let's see: A's final worst is 1/2 (the
        # baseline P-ADA cell in WX), B's is 2/5, so that reading would pick B.
        # This pins the added-over-baseline reading.
        case, selection = self.choose(
            simple_case(
                members=[member("M-A1", "C-A"), member("M-Z", "C-A")],
                snapshots=[
                    snapshot("S-X", "C-A", 1, ["M-A1"]),
                    snapshot("S-Z", "C-A", 2, ["M-Z"]),
                    snapshot("S-AX", "C-A", 3, ["M-A1", "M-Z"]),
                ],
                releases=[release("R-X", "S-X", "U-1", 5, "1/2")],
                candidates=[
                    candidate("CAND-A", "S-AX", "U-1", 1, "1/5", "5"),
                    candidate("CAND-B", "S-Z", "U-1", 1, "2/5", "5"),
                ],
                windows=[
                    window("WX", 5, 6, ["U-1"], "1"),
                    window("WZ", 0, 2, ["U-1"], "1/2"),
                ],
                participants=[participant("P-ADA"), participant("P-BOR")],
                linkages=[
                    linkage(
                        "L1",
                        [
                            {"member_id": "M-A1", "participant_id": "P-ADA"},
                            {"member_id": "M-Z", "participant_id": "P-BOR"},
                        ],
                    )
                ],
                cohorts=[{"cohort_id": "C-A"}],
            )
        )
        self.assertTrue(selection.feasible)
        self.assertEqual(selection.optional, ("CAND-A",))
        self.assertEqual(selection.added_worst_case, Fraction("1/5"))

    def test_third_objective_prefers_lexicographically_smallest_ids(self):
        # CAND-A and CAND-B are interchangeable except for ID.
        case, selection = self.choose(
            simple_case(
                releases=[],
                candidates=[
                    candidate("CAND-B", "S-A", "U-1", 2, "1/2", "5"),
                    candidate("CAND-A", "S-A", "U-1", 3, "1/2", "5"),
                ],
                windows=[window("W-1", 0, 10, ["U-1"], "1/2")],
            )
        )
        self.assertTrue(selection.feasible)
        self.assertEqual(selection.selected, ("CAND-A",))

    def test_mandatory_value_is_always_counted(self):
        case, selection = self.choose(
            choose_case(
                candidates=[
                    candidate("CAND-MUST", "S-A", "U-1", 2, "1/4", "7", mandatory=True),
                ],
            )
        )
        self.assertEqual(selection.research_value, Fraction("7"))

    def test_twenty_candidates_solve_exactly_and_quickly(self):
        candidates = [
            candidate(f"CAND-{index:02d}", "S-A", "U-1", index, "1/10", str(index % 7 + 1))
            for index in range(20)
        ]
        case, selection = self.choose(
            simple_case(
                releases=[],
                candidates=candidates,
                windows=[window("W-1", 0, 100, ["U-1"], "1")],
            )
        )
        self.assertTrue(selection.feasible)
        # Capacity is 10 items of 1/10; the optimum takes the ten most valuable.
        self.assertEqual(len(selection.optional), 10)
        self.assertEqual(
            selection.research_value,
            sum(
                sorted((Fraction(str(index % 7 + 1)) for index in range(20)),
                       reverse=True)[:10]
            ),
        )

    def test_thirty_identity_choices_do_not_change_selection_correctness(self):
        members = [member(f"M{index}", "C-A") for index in range(30)]
        assignments = [
            {
                "member_id": f"M{index}",
                "participant_ids": ["P-ADA", "P-BOR", "P-CYR"],
            }
            for index in range(30)
        ]
        participants = [participant("P-ADA"), participant("P-BOR"), participant("P-CYR")]
        candidates = [
            candidate(f"CAND-{index:02d}", "S-A", "U-1", index, "1/10", "1")
            for index in range(20)
        ]
        case, selection = self.choose(
            simple_case(
                participants=participants,
                members=members,
                snapshots=[snapshot("S-A", "C-A", 1, [m["member_id"] for m in members])],
                releases=[],
                candidates=candidates,
                windows=[window("W-1", 0, 100, ["U-1"], "1")],
                linkages=[linkage("L1", assignments)],
                cohorts=[{"cohort_id": "C-A"}],
            )
        )
        self.assertTrue(selection.feasible)
        self.assertEqual(len(selection.optional), 10)


if __name__ == "__main__":
    unittest.main()
