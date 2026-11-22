"""Exact rational arithmetic for the release-budget auditor.

Every privacy quantity in this package is a :class:`fractions.Fraction`.
Floats are never used in the accounting path: a value such as ``1/3`` that
arrives as the JSON number ``0.3333333333333333`` is parsed from its decimal
*literal text*, not through a binary float, so ``1/3 > 0.3333333333333333``
stays true and a strict budget overrun cannot be rounded away.
"""

from __future__ import annotations

import re
from fractions import Fraction

from .errors import CaseError

#: Canonical written form accepted for a rational:
#: ``5``, ``-2``, ``1/3``, ``0.25``, ``.5``, ``1.5e-3``.
_RATIONAL_RE = re.compile(
    r"""
    ^[+-]?
    (?:
        \d+\s*/\s*\d+
      | (?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?
    )$
    """,
    re.VERBOSE,
)


def parse_rational(value, *, path, record=None, what="value", minimum=None):
    """Parse ``value`` into an exact :class:`Fraction`.

    ``value`` may be an ``int``, a ``Fraction`` (what ``json.load`` produces
    when configured with ``parse_float=Fraction``), or a string.  ``bool`` and
    ``float`` are rejected: a binary float that slipped in would silently
    approximate the contract quantity.
    """

    if isinstance(value, bool):
        raise CaseError(
            "invalid-rational",
            f"{what} must be a rational, not a boolean",
            path,
            record,
        )
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, int):
        result = Fraction(value)
    elif isinstance(value, str):
        text = value.strip()
        if not _RATIONAL_RE.match(text):
            raise CaseError(
                "invalid-rational",
                f"{what} {value!r} is not a rational of the form 'n', 'n/d' "
                "or a decimal literal",
                path,
                record,
            )
        try:
            result = Fraction(text.replace(" ", ""))
        except (ValueError, ZeroDivisionError) as exc:
            raise CaseError(
                "invalid-rational",
                f"{what} {value!r} is not a rational: {exc}",
                path,
                record,
            ) from None
    else:
        raise CaseError(
            "invalid-rational",
            f"{what} must be a rational number or string, got {type(value).__name__}",
            path,
            record,
        )
    if minimum is not None and result < minimum:
        raise CaseError(
            "out-of-range",
            f"{what} must be >= {format_rational(Fraction(minimum))}, got {format_rational(result)}",
            path,
            record,
        )
    return result


def format_rational(value: Fraction) -> str:
    """Canonical, deterministic text form: ``3`` or ``2/3``."""

    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def format_excess(value: Fraction) -> str:
    """Excess is always strictly positive when reported; keep it canonical."""

    return format_rational(value)


__all__ = ["parse_rational", "format_rational", "format_excess"]
