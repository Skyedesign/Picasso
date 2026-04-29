"""Sheet check — read-only xlsx linter for Alida's pre-buy spreadsheets.

The module's public surface is intentionally small: callers feed in an
xlsx path, get back a list of `Finding` objects (severity + row + message
+ suggestion), and persist mutes via `suppressions.py`.

Distinct from `ingest/sort.py`, which also reads xlsx but for image
matching — sheetcheck never inspects pixel data.
"""

from __future__ import annotations

from .rules import Finding, ParsedSheet, parse_sheet, run_rules
from .suffixes import SuffixDict, load_suffixes
from .suppressions import (
    Suppressions,
    apply_suppressions,
    read_suppressions,
    suppression_path,
    write_suppressions,
)

__all__ = [
    "Finding",
    "ParsedSheet",
    "SuffixDict",
    "Suppressions",
    "apply_suppressions",
    "load_suffixes",
    "parse_sheet",
    "read_suppressions",
    "run_rules",
    "suppression_path",
    "write_suppressions",
]
