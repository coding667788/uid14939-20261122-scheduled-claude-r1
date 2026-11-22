"""Structured, field-addressable case errors.

Every rejection carries a machine path such as ``releases[3].costs.purpose_id``
plus a ``record`` hint naming the offending record id, so a caller can point at
the exact field of the exact record that failed validation.
"""

from __future__ import annotations

from typing import Any


class CaseError(Exception):
    """A deterministic, printable case-validation failure."""

    def __init__(self, code: str, message: str, path: str, record: Any = None):
        self.code = code
        self.message = message
        self.path = path
        self.record = record
        super().__init__(f"{path}: {message}")

    def to_json(self) -> dict:
        body = {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "path": self.path,
            },
        }
        if self.record is not None:
            body["error"]["record"] = self.record
        return body


def require_mapping(value, *, path, record=None, what="value") -> dict:
    if not isinstance(value, dict):
        raise CaseError(
            "expected-object",
            f"{what} must be a JSON object, got {type(value).__name__}",
            path,
            record,
        )
    return value


def require_list(value, *, path, record=None, what="value", nonempty=False) -> list:
    if not isinstance(value, list):
        raise CaseError(
            "expected-array",
            f"{what} must be a JSON array, got {type(value).__name__}",
            path,
            record,
        )
    if nonempty and not value:
        raise CaseError(
            "empty-array", f"{what} must not be empty", path, record
        )
    return value


def require_str(value, *, path, record=None, what="value", nonempty=True) -> str:
    if not isinstance(value, str):
        raise CaseError(
            "expected-string",
            f"{what} must be a string, got {type(value).__name__}",
            path,
            record,
        )
    if nonempty and not value:
        raise CaseError("empty-string", f"{what} must not be empty", path, record)
    return value


def require_int(value, *, path, record=None, what="value") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaseError(
            "expected-integer",
            f"{what} must be an integer, got {type(value).__name__}",
            path,
            record,
        )
    return value


def require_field(mapping: dict, key: str, *, path, record=None) -> Any:
    if key not in mapping:
        raise CaseError(
            "missing-field",
            f"required field {key!r} is missing",
            f"{path}.{key}",
            record,
        )
    return mapping[key]


__all__ = [
    "CaseError",
    "require_mapping",
    "require_list",
    "require_str",
    "require_int",
    "require_field",
]
