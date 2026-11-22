"""Offline privacy-release budget auditor.

The package answers one approval-desk question: if one person appears in two
research cohorts, why can two individually compliant statistical reports
exceed the budget when published together?  It computes exact rational privacy
consumption from the case file only.  It contains no mechanism to modify the
data or to train a model.
"""

from __future__ import annotations

__version__ = "1.0.0"

from .casefile import Case, build_case, load_case

__all__ = ["Case", "__version__", "build_case", "load_case"]
