"""Identity interpretations: merges, splits, ambiguity and global consistency."""

from __future__ import annotations

import unittest
from fractions import Fraction

from release_budget.identity import build_components
from release_budget.ledger import accounted_exposures, charge_all

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


class MergeTest(CaseTestCase):
    def test_merge_conflicts_are_jointly_chosen(self):
        # Two linked participants, each admissible on both old ids, but the
        # merge forces one shared choice.  Treating the ids independently would
        # invent four interpretations; only two are feasible.
        data = simple_case(
            participants=[participant("P-ADA"), participant("P-BOR")],
            members=[
                member("M-OLD1", "C-A"),
                member("M-OLD2", "C-A"),
                member("M-B1", "C-B"),
            ],
            snapshots=[
                snapshot("S-A", "C-A", 1, ["M-OLD1", "M-OLD2"]),
                snapshot("S-B", "C-B", 1, ["M-B1"]),
            ],
            releases=[release("R-A", "S-A", "U-1", 1, "3/5")],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-OLD1", "participant_ids": ["P-ADA", "P-BOR"]},
                        {"member_id": "M-OLD2", "participant_ids": ["P-ADA", "P-BOR"]},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                    merge_groups=[["M-OLD1", "M-OLD2"]],
                )
            ],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        self.assertEqual(components.interpretation_count, 2)
        self.assertEqual(components.admissible["M-OLD1"], ("P-ADA", "P-BOR"))
        self.assertIs(components.component_of_member["M-OLD1"], components.component_of_member["M-OLD2"])

    def test_merge_group_charges_old_ids_once_in_one_release(self):
        # M-OLD1 and M-OLD2 are two old numbers for one person.  A release that
        # mentions both must charge that person once, not twice.
        data = simple_case(
            members=[member("M-OLD1", "C-A"), member("M-OLD2", "C-A")],
            snapshots=[snapshot("S-A", "C-A", 1, ["M-OLD1", "M-OLD2"])],
            releases=[release("R-A", "S-A", "U-1", 1, "3/5")],
            windows=[window("W-1", 0, 10, ["U-1"], "1/2")],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-OLD1", "participant_id": "P-ADA"},
                        {"member_id": "M-OLD2", "participant_id": "P-ADA"},
                    ],
                    merge_groups=[["M-OLD1", "M-OLD2"]],
                )
            ],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        exposures = accounted_exposures(case, components)
        charges = charge_all(exposures, case.windows[0], components.choice_witness())
        self.assertEqual(charges, {"P-ADA": Fraction("3/5")})

        _, result = self.audit(data)
        # 3/5 <= cap 1/2?  No: 3/5 > 1/2, so exactly one violation, not two.
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].charge, Fraction("3/5"))

    def test_merged_old_account_survives_into_new_linkage(self):
        # Four old releases, two per old id, must merge as one running cost.
        data = simple_case(
            members=[member("M-OLD1", "C-A"), member("M-OLD2", "C-A")],
            snapshots=[
                snapshot("S-A1", "C-A", 1, ["M-OLD1"]),
                snapshot("S-A2", "C-A", 2, ["M-OLD2"]),
            ],
            releases=[
                release("R-1", "S-A1", "U-1", 1, "2/5"),
                release("R-2", "S-A2", "U-1", 2, "2/5"),
            ],
            windows=[window("W-1", 0, 10, ["U-1"], "3/4")],
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
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertEqual(result.violations[0].charge, Fraction("4/5"))
        self.assertEqual(result.violations[0].release_ids, ("R-1", "R-2"))


class SplitTest(CaseTestCase):
    def test_split_requires_joint_choice_across_releases(self):
        # One source member is split between two participants.  The same source
        # must receive the *same* participant in every release, so the two
        # releases together charge one participant twice -- never one each.
        data = simple_case(
            members=[member("M-SHARED", "C-A")],
            snapshots=[
                snapshot("S-A1", "C-A", 1, ["M-SHARED"]),
                snapshot("S-A2", "C-A", 2, ["M-SHARED"]),
            ],
            releases=[
                release("R-1", "S-A1", "U-1", 1, "3/5"),
                release("R-2", "S-A2", "U-1", 2, "3/5"),
            ],
            linkages=[
                linkage(
                    "L1",
                    [
                        {
                            "member_id": "M-SHARED",
                            "participant_ids": ["P-ADA", "P-BOR"],
                        }
                    ],
                )
            ],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        self.assertEqual(components.interpretation_count, 2)
        exposures = accounted_exposures(case, components)

        # Under either interpretation both releases charge the *same* person.
        for target in ("P-ADA", "P-BOR"):
            charges = charge_all(exposures, case.windows[0], components.choice_witness(target))
            self.assertEqual(charges, {target: Fraction("6/5")}, target)

        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        # Two participants can be over cap, each in a different interpretation.
        self.assertEqual(
            [(v.participant_id, v.charge) for v in result.violations],
            [("P-ADA", Fraction("6/5")), ("P-BOR", Fraction("6/5"))],
        )

    def test_split_choice_is_consistent_across_releases(self):
        case = case_from(
            simple_case(
                members=[member("M-SHARED", "C-A")],
                snapshots=[
                    snapshot("S-A1", "C-A", 1, ["M-SHARED"]),
                    snapshot("S-A2", "C-A", 2, ["M-SHARED"]),
                ],
                releases=[
                    release("R-1", "S-A1", "U-1", 1, "1/10"),
                    release("R-2", "S-A2", "U-1", 2, "1/10"),
                ],
                linkages=[
                    linkage(
                        "L1",
                        [
                            {
                                "member_id": "M-SHARED",
                                "participant_ids": ["P-ADA", "P-BOR"],
                            }
                        ],
                    )
                ],
            )
        )
        components = build_components(case.linkage("L1"))
        witness = components.witness("P-BOR")
        # A single global mapping, not one per release.
        self.assertEqual(witness, {"M-SHARED": "P-BOR"})


class AmbiguityTest(CaseTestCase):
    def test_ambiguous_member_creates_two_admissible_interpretations(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR"]},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        self.assertEqual(components.interpretation_count, 2)
        self.assertEqual(components.witness("P-ADA")["M-A1"], "P-ADA")
        self.assertEqual(components.witness("P-BOR")["M-A1"], "P-BOR")

    def test_worst_case_quantifies_over_all_admissible_interpretations(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR"]},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ],
        )
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        # Worst case for each participant steers the ambiguous member to them.
        # P-BOR is only exposed through M-A2, which is definite: 3/5 <= cap 1,
        # so P-BOR is *not* a violation.  Only P-ADA is over.
        self.assertEqual(
            [(v.participant_id, v.charge) for v in result.violations],
            [("P-ADA", Fraction("6/5"))],
        )

    def test_compliant_only_if_all_interpretations_compliant(self):
        # Two interpretations: one overruns, the other does not.  The audit must
        # not certify on the strength of the benign one.
        #
        # S-A holds M-A1 (ambiguous P-ADA/P-BOR) and M-A2 (P-BOR); S-B holds
        # M-B1 (P-ADA).  Under the "M-A1 -> P-BOR" reading, P-BOR is charged
        # 4/5 (R-A + R-B) and P-ADA 1/2, both within cap 1.  Under the
        # "M-A1 -> P-ADA" reading, P-ADA is charged 4/5 + 1/2 = 13/10, over.
        data = simple_case(
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
            releases=[
                release("R-A", "S-A", "U-1", 1, "4/5"),
                release("R-B", "S-B", "U-1", 2, "1/2"),
            ],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR"]},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        exposures = accounted_exposures(case, components)
        benign = charge_all(exposures, case.windows[0], components.choice_witness("P-BOR"))
        self.assertEqual(benign, {"P-BOR": Fraction("4/5"), "P-ADA": Fraction("1/2")})
        _, result = self.audit(data)
        self.assertFalse(result.compliant)
        self.assertEqual(result.violations[0].participant_id, "P-ADA")
        self.assertEqual(result.violations[0].charge, Fraction("13/10"))

    def test_interpretation_count_multiplies_across_choice_points(self):
        data = simple_case(
            participants=[participant("P-ADA"), participant("P-BOR"), participant("P-CYR")],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR", "P-CYR"]},
                        {"member_id": "M-A2", "participant_ids": ["P-BOR", "P-CYR"]},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ],
        )
        case = case_from(data)
        components = build_components(case.linkage("L1"))
        self.assertEqual(components.interpretation_count, 6)
        self.assertEqual(len(list(_choices(components))), 6)


def _choices(components):
    import itertools

    options = [components.admissible[root] for root in components.roots]
    for combo in itertools.product(*options):
        yield dict(zip(components.roots, combo))


class UnlinkedMemberTest(CaseTestCase):
    def test_snapshot_member_missing_from_linkage_is_an_error(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ],
        )
        with self.assertRaises(Exception) as caught:
            self.audit(data)
        error = caught.exception
        self.assertEqual(error.code, "unlinked-member")
        self.assertIn("M-A2", error.message)


if __name__ == "__main__":
    unittest.main()
