"""Load `picasso-suffixes.yaml` and answer two questions per row:

  • for the SKU's trailing suffix, which column should describe it?
  • does the cell's text agree with the suffix, or is it a competing value?

Conservative by design — partial coverage shouldn't produce false
positives, so an unknown cell value is treated as "no opinion" rather
than "mismatch".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SuffixEntry:
    id: str
    column: str
    keywords: tuple[str, ...]  # all upper-cased


@dataclass(frozen=True)
class SuffixDict:
    entries: tuple[SuffixEntry, ...]

    @property
    def ids_longest_first(self) -> tuple[SuffixEntry, ...]:
        # Multi-char suffixes shadow shorter prefixes (`GOL` before `G`).
        return tuple(sorted(self.entries, key=lambda e: -len(e.id)))

    def lookup(self, suffix: str) -> SuffixEntry | None:
        suffix_u = suffix.upper()
        for e in self.entries:
            if e.id == suffix_u:
                return e
        return None

    def detect(self, sku: str) -> SuffixEntry | None:
        """Find the entry whose id matches the SKU's trailing token after
        the last dash. Returns None if there's no dash, no token, or no
        match in the dictionary."""
        if "-" not in sku:
            return None
        tail = sku.rsplit("-", 1)[1].strip().upper()
        if not tail:
            return None
        return self.lookup(tail)

    def cell_matches(self, entry: SuffixEntry, cell: str) -> bool:
        """Cell text contains any of this entry's keywords (case-insensitive
        substring)."""
        if not cell:
            return False
        cell_u = cell.upper()
        return any(kw in cell_u for kw in entry.keywords)

    def competing_match(self, entry: SuffixEntry, cell: str) -> SuffixEntry | None:
        """Find a *different* entry on the same column whose keyword the
        cell contains. That's the mismatch signal: the cell describes a
        sibling colour/size, not this row's."""
        if not cell:
            return None
        cell_u = cell.upper()
        for other in self.entries:
            if other.id == entry.id or other.column != entry.column:
                continue
            if any(kw in cell_u for kw in other.keywords):
                return other
        return None


def load_suffixes(yaml_path: Path) -> SuffixDict:
    """Read picasso-suffixes.yaml. Missing file → empty dict (rules
    that need it become no-ops, which is preferable to crashing the
    whole linter)."""
    if not yaml_path.exists():
        return SuffixDict(entries=())
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    entries: list[SuffixEntry] = []
    for item in raw.get("suffixes", []):
        sid = str(item.get("id", "")).strip().upper()
        col = str(item.get("column", "")).strip().upper()
        kws = tuple(str(k).strip().upper() for k in item.get("keywords", []) if str(k).strip())
        if sid and col and kws:
            entries.append(SuffixEntry(id=sid, column=col, keywords=kws))
    return SuffixDict(entries=tuple(entries))
