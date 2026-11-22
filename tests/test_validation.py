"""Input validation: errors must be traceable to the field and record."""

from __future__ import annotations

import json
import os
import unittest

from release_budget.casefile import build_case, load_case
from release_budget.errors import CaseError

from .support import (
    CaseTestCase,
    candidate,
    linkage,
    member,
    release,
    run_module,
    simple_case,
    snapshot,
    window,
    write_case,
)


class FieldPathTest(CaseTestCase):
    def expect(self, data, code, path_fragment, record=None):
        with self.assertRaises(CaseError) as caught:
            build_case(data)
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertIn(path_fragment, error.path)
        if record is not None:
            self.assertEqual(error.record, record)
        return error

    def test_missing_release_cost_names_the_field(self):
        data = simple_case(releases=[release("R-A", "S-A", "U-1", 1, "3/5")])
        del data["releases"][0]["cost"]
        error = self.expect(data, "missing-field", "releases[0].cost", "R-A")
        self.assertIn("cost", error.message)

    def test_bad_rational_names_field_and_record(self):
        data = simple_case(releases=[release("R-A", "S-A", "U-1", 1, "three fifths")])
        self.expect(data, "invalid-rational", "releases[0].cost", "R-A")

    def test_negative_cost_rejected(self):
        data = simple_case(releases=[release("R-A", "S-A", "U-1", 1, "-1/5")])
        self.expect(data, "out-of-range", "releases[0].cost", "R-A")

    def test_unknown_snapshot_names_field_and_record(self):
        data = simple_case(releases=[release("R-A", "S-MISSING", "U-1", 1, "3/5")])
        self.expect(data, "unknown-snapshot", "releases[0].snapshot_id", "R-A")

    def test_unknown_purpose_in_window(self):
        data = simple_case(windows=[window("W-1", 0, 10, ["U-NOPE"], "1")])
        self.expect(data, "unknown-purpose", "windows[0].purpose_ids[0]", "W-1")

    def test_inverted_window_rejected(self):
        data = simple_case(windows=[window("W-1", 10, 10, ["U-1"], "1")])
        self.expect(data, "invalid-window", "windows[0].end", "W-1")

    def test_duplicate_release_id_rejected(self):
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, "1/5"),
                release("R-A", "S-B", "U-1", 2, "1/5"),
            ]
        )
        self.expect(data, "duplicate-id", "releases[1]", "R-A")

    def test_duplicate_snapshot_version_rejected(self):
        data = simple_case(
            snapshots=[
                snapshot("S-A", "C-A", 1, ["M-A1"]),
                snapshot("S-A2", "C-A", 1, ["M-A2"]),
                snapshot("S-B", "C-B", 1, ["M-B1"]),
            ]
        )
        self.expect(data, "duplicate-snapshot-version", "snapshots[1].version", "S-A2")

    def test_cross_cohort_member_rejected(self):
        data = simple_case(
            snapshots=[
                snapshot("S-A", "C-A", 1, ["M-B1"]),
                snapshot("S-B", "C-B", 1, ["M-B1"]),
            ]
        )
        self.expect(data, "cross-cohort-member", "snapshots[0].members[0]", "S-A")

    def test_unknown_member_in_linkage(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-GHOST", "participant_id": "P-ADA"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ]
        )
        self.expect(data, "unknown-member", "linkages[0].assignments[0].member_id", "L1")

    def test_unknown_participant_in_linkage(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_id": "P-GHOST"},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ]
        )
        self.expect(
            data, "unknown-participant", "linkages[0].assignments[0].participant_id", "L1"
        )

    def test_infeasible_merge_group_rejected_at_load(self):
        data = simple_case(
            participants=[{"participant_id": "P-ADA"}, {"participant_id": "P-BOR"}],
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                    merge_groups=[["M-A1", "M-A2"]],
                )
            ],
        )
        self.expect(data, "infeasible-merge-group", "linkages[0].merge_groups[0]", "L1")

    def test_merge_group_needs_two_members(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                    merge_groups=[["M-A1"]],
                )
            ]
        )
        self.expect(data, "invalid-merge-group", "linkages[0].merge_groups[0]", "L1")

    def test_duplicate_assignment_rejected(self):
        data = simple_case(
            linkages=[
                linkage(
                    "L1",
                    [
                        {"member_id": "M-A1", "participant_id": "P-ADA"},
                        {"member_id": "M-A1", "participant_id": "P-BOR"},
                        {"member_id": "M-A2", "participant_id": "P-BOR"},
                        {"member_id": "M-B1", "participant_id": "P-ADA"},
                    ],
                )
            ]
        )
        self.expect(data, "duplicate-assignment", "linkages[0].assignments[1]", "L1")

    def test_unsupported_case_version(self):
        data = simple_case(case_version=99)
        self.expect(data, "unsupported-version", "case.case_version")

    def test_candidate_reusing_bound_id_rejected(self):
        data = simple_case(
            candidates=[candidate("R-A", "S-B", "U-1", 3, "1/5", "1")]
        )
        self.expect(data, "duplicate-id", "candidates", "R-A")

    def test_unknown_view_rejected(self):
        data = simple_case(audit_views=[{"view_id": "V", "linkage_id": "L-GHOST"}])
        self.expect(data, "unknown-linkage", "audit_views[0].linkage_id", "V")

    def test_json_float_literal_kept_exact(self):
        # Written through the JSON parser, not constructed in Python.
        data = simple_case(
            releases=[
                release("R-A", "S-A", "U-1", 1, 0.6666666666666666),
                release("R-B", "S-B", "U-1", 2, 0.0000000000000004),
            ]
        )
        path = write_case(self.tmpdir, "float.json", data)
        case = load_case(path)
        # Exact decimal rationals, not binary approximations.
        self.assertEqual(case.releases[0].cost, __import__("fractions").Fraction("0.6666666666666666"))
        self.assertEqual(case.releases[1].cost, __import__("fractions").Fraction("0.0000000000000004"))

    def test_boolean_cost_rejected(self):
        data = simple_case(releases=[release("R-A", "S-A", "U-1", 1, True)])
        self.expect(data, "invalid-rational", "releases[0].cost", "R-A")

    def test_malformed_json_is_located(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"case_version": 1, "case_id": }')
        with self.assertRaises(CaseError) as caught:
            load_case(path)
        self.assertEqual(caught.exception.code, "invalid-json")
        self.assertIn("line", caught.exception.path)

    def test_missing_file(self):
        with self.assertRaises(CaseError) as caught:
            load_case(os.path.join(self.tmpdir, "nope.json"))
        self.assertEqual(caught.exception.code, "io-error")


class CliTest(CaseTestCase):
    def test_audit_command_is_deterministic_json(self):
        path = write_case(self.tmpdir, "case.json", simple_case())
        first = run_module(["audit", path])
        second = run_module(["audit", path])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)

        body = json.loads(first.stdout)
        self.assertTrue(body["ok"])
        self.assertEqual(body["command"], "audit")
        self.assertFalse(body["compliant"])
        self.assertEqual(body["violations"][0]["charge"], "6/5")

    def test_recheck_command(self):
        data = simple_case(
            audit_views=[{"view_id": "V", "linkage_id": "L1"}],
            recheck={"view_id": "V", "new_linkage": "L1"},
        )
        path = write_case(self.tmpdir, "case.json", data)
        completed = run_module(["recheck", path])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["view"]["view_id"], "V")
        self.assertEqual(body["changed"], [])

    def test_choose_command(self):
        data = simple_case(
            releases=[release("R-BASE", "S-A", "U-1", 1, "1/4")],
            candidates=[candidate("CAND-1", "S-A", "U-1", 2, "1/4", "5")],
            windows=[window("W-1", 0, 10, ["U-1"], "1")],
        )
        path = write_case(self.tmpdir, "case.json", data)
        completed = run_module(["choose", path])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["selected_release_ids"], ["CAND-1"])
        self.assertEqual(body["research_value"], "5")

    def test_verify_command_agrees(self):
        path = write_case(self.tmpdir, "case.json", simple_case())
        completed = run_module(["verify", path])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertTrue(body["agree"])

    def test_error_exit_code_and_shape(self):
        data = simple_case(releases=[release("R-A", "S-A", "U-1", 1, "oops")])
        path = write_case(self.tmpdir, "case.json", data)
        completed = run_module(["audit", path])
        self.assertEqual(completed.returncode, 2)
        body = json.loads(completed.stdout)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "invalid-rational")
        self.assertEqual(body["error"]["path"], "releases[0].cost")
        self.assertEqual(body["error"]["record"], "R-A")

    def test_usage_error_is_json(self):
        completed = run_module(["nonsense", "whatever.json"])
        self.assertEqual(completed.returncode, 2)
        body = json.loads(completed.stdout)
        self.assertEqual(body["error"]["code"], "usage-error")


if __name__ == "__main__":
    unittest.main()
