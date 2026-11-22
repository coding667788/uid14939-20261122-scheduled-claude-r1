"""The fast audit must agree with independent exhaustive enumeration."""

from __future__ import annotations

import itertools
import unittest
from fractions import Fraction

from release_budget.audit import audit_with_linkage
from release_budget.bruteforce import (
    brute_force_audit,
    compare_with_fast,
    enumerate_choices,
)
from release_budget.identity import build_components
from release_budget.ledger import accounted_exposures

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


class EnumerationTest(CaseTestCase):
    def test_enumerates_every_choice(self):
        case = case_from(
            simple_case(
                participants=[participant("P-ADA"), participant("P-BOR"), participant("P-CYR")],
                linkages=[
                    linkage(
                        "L1",
                        [
                            {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR"]},
                            {"member_id": "M-A2", "participant_ids": ["P-BOR", "P-CYR"]},
                            {"member_id": "M-B1", "participant_id": "P-ADA"},
                        ],
                    )
                ],
            )
        )
        components = build_components(case.linkage("L1"))
        choices = list(enumerate_choices(components))
        self.assertEqual(len(choices), 4)
        self.assertEqual(len({tuple(sorted(c.items())) for c in choices}), 4)

    def test_limit_bounds_enumeration(self):
        case = case_from(
            simple_case(
                participants=[participant("P-ADA"), participant("P-BOR")],
                linkages=[
                    linkage(
                        "L1",
                        [
                            {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR"]},
                            {"member_id": "M-A2", "participant_ids": ["P-ADA", "P-BOR"]},
                            {"member_id": "M-B1", "participant_id": "P-ADA"},
                        ],
                    )
                ],
            )
        )
        components = build_components(case.linkage("L1"))
        self.assertEqual(len(list(enumerate_choices(components, limit=3))), 3)


class FastVersusBruteTest(CaseTestCase):
    def test_agree_on_the_base_overrun_case(self):
        case = case_from(simple_case())
        report = compare_with_fast(case)
        self.assertTrue(report["agree"])
        self.assertFalse(report["fast_compliant"])
        self.assertFalse(report["bruteforce_compliant"])

    def test_agree_on_ambiguous_case(self):
        case = case_from(
            simple_case(
                participants=[participant("P-ADA"), participant("P-BOR"), participant("P-CYR")],
                snapshots=[
                    snapshot("S-A", "C-A", 1, ["M-A1", "M-A2"]),
                    snapshot("S-B", "C-B", 1, ["M-B1"]),
                ],
                linkages=[
                    linkage(
                        "L1",
                        [
                            {"member_id": "M-A1", "participant_ids": ["P-ADA", "P-BOR", "P-CYR"]},
                            {"member_id": "M-A2", "participant_ids": ["P-BOR", "P-CYR"]},
                            {"member_id": "M-B1", "participant_ids": ["P-ADA", "P-CYR"]},
                        ],
                    )
                ],
            )
        )
        report = compare_with_fast(case)
        self.assertTrue(report["agree"])
        self.assertEqual(report["choices_enumerated"], 12)

    def test_agree_on_merged_case(self):
        case = case_from(
            simple_case(
                members=[
                    member("M-OLD1", "C-A"),
                    member("M-OLD2", "C-A"),
                    member("M-B1", "C-B"),
                ],
                snapshots=[
                    snapshot("S-A1", "C-A", 1, ["M-OLD1", "M-OLD2"]),
                    snapshot("S-A2", "C-A", 2, ["M-OLD1"]),
                    snapshot("S-B", "C-B", 1, ["M-B1"]),
                ],
                releases=[
                    release("R-1", "S-A1", "U-1", 1, "2/5"),
                    release("R-2", "S-A2", "U-1", 2, "1/5"),
                    release("R-3", "S-B", "U-1", 3, "1/5"),
                ],
                windows=[window("W-1", 0, 10, ["U-1"], "1/2")],
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
        )
        report = compare_with_fast(case)
        self.assertTrue(report["agree"])
        self.assertEqual(report["choices_enumerated"], 2)

    def test_brute_force_reports_same_worst_cell(self):
        case = case_from(simple_case())
        components = build_components(case.linkage("L1"))
        slow = brute_force_audit(case, components)
        self.assertEqual(slow.worst[("W-1", "P-ADA")], Fraction("6/5"))
        self.assertFalse(slow.compliant)
        self.assertIsNotNone(slow.witness)

    def test_randomised_agreement_with_merges_and_windows(self):
        """Cross-check on random cases with merge groups and overlapping windows."""

        import random
        from fractions import Fraction

        from release_budget.audit import assess_worst_case
        from release_budget.ledger import accounted_exposures

        rng = random.Random(7)
        for trial in range(60):
            participants = [participant(f"P{i}") for i in range(rng.randint(1, 4))]
            member_ids = [f"M{i}" for i in range(rng.randint(1, 5))]
            names = [p["participant_id"] for p in participants]
            assignments = []
            for mid in member_ids:
                picks = rng.sample(names, rng.randint(1, len(names)))
                assignments.append({"member_id": mid, "participant_ids": picks})
            by_id = {row["member_id"]: set(row["participant_ids"]) for row in assignments}
            merge = []
            if len(member_ids) >= 2 and rng.random() < 0.4:
                left, right = rng.sample(member_ids, 2)
                if by_id[left] & by_id[right]:
                    merge = [[left, right]]
            snapshots = []
            releases = []
            for index in range(rng.randint(1, 3)):
                sid = f"S{index}"
                snapshots.append(
                    snapshot(sid, "C-A", index + 1,
                             rng.sample(member_ids, rng.randint(1, len(member_ids))))
                )
                releases.append(
                    release(f"R{index}", sid, rng.choice(["U1", "U2"]),
                            rng.randint(0, 9),
                            f"{rng.randint(1, 9)}/{rng.randint(2, 10)}")
                )
            windows = [
                window("W1", 0, 5, ["U1", "U2"], f"{rng.randint(1, 20)}/10"),
                window("W2", 3, 9, ["U2"], f"{rng.randint(1, 20)}/10"),
            ]
            data = {
                "case_version": 1,
                "case_id": f"random-{trial}",
                "active_linkage": "L",
                "participants": participants,
                "cohorts": [{"cohort_id": "C-A"}],
                "purposes": [{"purpose_id": "U1"}, {"purpose_id": "U2"}],
                "members": [member(mid, "C-A") for mid in member_ids],
                "snapshots": snapshots,
                "windows": windows,
                "releases": releases,
                "candidates": [],
                "linkages": [
                    {"linkage_id": "L", "assignments": assignments,
                     "merge_groups": merge}
                ],
                "audit_views": [{"view_id": "V", "linkage_id": "L"}],
            }
            case = case_from(data)
            components = build_components(case.linkage("L"))
            fast = audit_with_linkage(case, case.linkage("L"))
            slow = brute_force_audit(case, components)

            exposures = accounted_exposures(case, components)
            fast_cells = {}
            for w in case.windows:
                for pid, charge in assess_worst_case(exposures, w, components).items():
                    fast_cells[(w.window_id, pid)] = charge
            for key in set(fast_cells) | set(slow.worst):
                self.assertEqual(
                    fast_cells.get(key, Fraction(0)),
                    slow.worst.get(key, Fraction(0)),
                    f"trial {trial} cell {key}",
                )
            self.assertEqual(fast.compliant, slow.compliant, f"trial {trial}")

    def test_randomised_agreement(self):
        """Cross-check on many small randomly-shaped cases."""

        import random

        rng = random.Random(20261122)
        for trial in range(40):
            participants = [participant(f"P{i}") for i in range(3)]
            members = [member(f"M{i}", "C-A") for i in range(3)]
            assignments = []
            for mid in [m["member_id"] for m in members]:
                choices = rng.sample([p["participant_id"] for p in participants],
                                     rng.choice([1, 2, 3]))
                assignments.append({"member_id": mid, "participant_ids": choices})
            snapshots = []
            releases = []
            for index in range(rng.randint(1, 3)):
                snap = f"S{index}"
                snapshots.append(
                    snapshot(snap, "C-A", index + 1,
                             rng.sample([m["member_id"] for m in members],
                                        rng.randint(1, 3)))
                )
                releases.append(
                    release(f"R{index}", snap, "U-1", rng.randint(0, 9),
                            f"{rng.randint(1, 6)}/10")
                )
            data = simple_case(
                participants=participants,
                members=members,
                snapshots=snapshots,
                releases=releases,
                windows=[window("W-1", 0, 10, ["U-1"], f"{rng.randint(3, 12)}/10")],
                linkages=[linkage("L1", assignments)],
                cohorts=[{"cohort_id": "C-A"}],
            )
            data["members"] = members
            case = case_from(data)
            report = compare_with_fast(case)
            self.assertTrue(report["agree"], f"trial {trial}: {report['mismatches']}")


if __name__ == "__main__":
    unittest.main()
