"""Command-line entry point: ``python -m release_budget <command> CASE.json``.

Commands
--------
audit   CASE.json [--view VIEW_ID]
recheck CASE.json [--view VIEW_ID] [--new-linkage LINKAGE_ID]
choose  CASE.json [--view VIEW_ID]
verify  CASE.json [--view VIEW_ID]

Every command writes exactly one deterministic UTF-8 JSON object to stdout and
exits 0.  A rejected input writes an error object naming ``code``, ``message``,
``path`` and, when the failure belongs to a record, ``record``; it exits 2.
"""

from __future__ import annotations

import argparse
import sys

from .audit import run_audit, run_recheck
from .bruteforce import compare_with_fast, brute_force_audit, fast_worst_by_cell
from .casefile import Case, load_case
from .choose import run_choose
from .errors import CaseError
from .identity import build_components
from .jsonout import emit
from .rational import format_rational

COMMANDS = ("audit", "recheck", "choose", "verify")


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # pragma: no cover - exercised via CLI tests
        raise CaseError("usage-error", message, "arguments", None)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m release_budget", add_help=True)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("case", metavar="CASE.json")
    parser.add_argument("--view", dest="view_id", default=None)
    parser.add_argument("--new-linkage", dest="new_linkage_id", default=None)
    return parser


def _resolve_view(case: Case, view_id: str):
    if view_id not in case.audit_views:
        raise CaseError(
            "unknown-view", f"unknown audit view {view_id!r}", "view_id", view_id
        )
    return case.audit_views[view_id]


def _command_audit(case: Case, args) -> dict:
    result = run_audit(case, view_id=args.view_id)
    if args.view_id is not None:
        view = _resolve_view(case, args.view_id)
        body = result.to_json(view_id=view.view_id)
    else:
        body = result.to_json(view_id=None)
    body["case_id"] = case.case_id
    return body


def _command_recheck(case: Case, args) -> dict:
    result = run_recheck(case, view_id=args.view_id, new_linkage_id=args.new_linkage_id)
    body = result.to_json()
    body["case_id"] = case.case_id
    return body


def _command_choose(case: Case, args) -> dict:
    if args.view_id is not None:
        view = _resolve_view(case, args.view_id)
        components = build_components(case.linkage(view.linkage_id))
        selection = run_choose(case, components)
    else:
        selection = run_choose(case)
    return selection.to_json(case.case_id)


def _command_verify(case: Case, args) -> dict:
    if args.view_id is None:
        return compare_with_fast(case)

    view = _resolve_view(case, args.view_id)
    linkage = case.linkage(view.linkage_id)
    components = build_components(linkage)
    from .audit import audit_with_linkage

    fast = audit_with_linkage(case, linkage)
    slow = brute_force_audit(case, components)
    fast_cells = fast_worst_by_cell(case, components)
    mismatches = []
    for key in sorted(set(fast_cells) | set(slow.worst)):
        left = fast_cells.get(key, 0)
        right = slow.worst.get(key, 0)
        if left != right:
            mismatches.append(
                {
                    "window_id": key[0],
                    "participant_id": key[1],
                    "fast": format_rational(left),
                    "bruteforce": format_rational(right),
                }
            )
    return {
        "ok": True,
        "command": "verify",
        "case_id": case.case_id,
        "view_id": view.view_id,
        "linkage_id": linkage.linkage_id,
        "choices_enumerated": slow.choices_enumerated,
        "fast_compliant": fast.compliant,
        "bruteforce_compliant": slow.compliant,
        "agree": not mismatches and fast.compliant == slow.compliant,
        "mismatches": mismatches,
    }


_HANDLERS = {
    "audit": _command_audit,
    "recheck": _command_recheck,
    "choose": _command_choose,
    "verify": _command_verify,
}


def run(argv=None) -> int:
    # Deterministic UTF-8 on every console, regardless of the Windows code page.
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):  # pragma: no cover - redirected streams
        pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        case = load_case(args.case)
        body = _HANDLERS[args.command](case, args)
    except CaseError as error:
        emit(error.to_json())
        return 2
    emit(body)
    return 0


def main(argv=None) -> int:
    return run(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
