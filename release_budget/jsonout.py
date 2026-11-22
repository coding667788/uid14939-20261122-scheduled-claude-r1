"""Deterministic UTF-8 JSON output.

Keys are emitted in insertion order (never sorted by accident), floats never
appear because every rational is serialised as exact text, and the payload is
written without a trailing newline so byte-for-byte comparison is stable.
"""

from __future__ import annotations

import json
import sys


def dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def emit(payload, stream=None) -> None:
    stream = stream if stream is not None else sys.stdout
    stream.write(dump(payload))
    stream.write("\n")
    stream.flush()


__all__ = ["dump", "emit"]
