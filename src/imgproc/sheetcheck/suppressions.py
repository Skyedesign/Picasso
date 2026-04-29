"""Two-tier suppression for Sheet check findings.

Tier 1 — per-finding mute: hide one specific row+rule combination.
Tier 2 — per-rule mute: hide every finding produced by a rule across the
file (e.g., "I never want variant_gap warnings on this sheet").

Persisted as `{xlsx}.picasso-suppressions.json` so a re-run of the same
file silently respects past decisions. Sidecar lives next to the xlsx
on purpose: travels with the file when Alida moves it between folders,
and gets cleaned up when she archives a season.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1
SIDECAR_SUFFIX = ".picasso-suppressions.json"


class Suppressions(BaseModel):
    schema_version: int = SCHEMA_VERSION
    muted_findings: set[str] = Field(default_factory=set)  # per-row+rule keys
    muted_rules: set[str] = Field(default_factory=set)     # rule ids muted file-wide
    last_updated: str = ""


def suppression_path(xlsx_path: Path) -> Path:
    return xlsx_path.with_name(xlsx_path.name + SIDECAR_SUFFIX)


def read_suppressions(xlsx_path: Path) -> Suppressions:
    """Load the sidecar if present, else return an empty `Suppressions`.
    Corrupt JSON or schema mismatch falls back to empty so a manual edit
    that goes wrong doesn't blow up the linter."""
    p = suppression_path(xlsx_path)
    if not p.exists():
        return Suppressions()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Suppressions()
    if not isinstance(data, dict) or data.get("schema_version", 0) > SCHEMA_VERSION:
        return Suppressions()
    try:
        return Suppressions(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            muted_findings=set(data.get("muted_findings", []) or []),
            muted_rules=set(data.get("muted_rules", []) or []),
            last_updated=data.get("last_updated", ""),
        )
    except Exception:
        return Suppressions()


def write_suppressions(xlsx_path: Path, sup: Suppressions) -> Path:
    """Atomic write next to the xlsx. Tempfile in the same directory →
    rename, so a crash mid-write can't truncate a previously-good file.

    Returns the sidecar path. Raises OSError if the dir isn't writable
    (e.g. xlsx on a read-only network share); callers can choose to
    surface that to the UI as a soft warning.
    """
    sup.last_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    p = suppression_path(xlsx_path)
    payload = json.dumps({
        "schema_version": sup.schema_version,
        "muted_findings": sorted(sup.muted_findings),
        "muted_rules": sorted(sup.muted_rules),
        "last_updated": sup.last_updated,
    }, indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".picasso-sup.", suffix=".json", dir=str(xlsx_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, p)
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise
    return p


def apply_suppressions(findings: list, sup: Suppressions) -> tuple[list, list]:
    """Split a finding list into (visible, suppressed). Two-tier: rule-wide
    mutes hide everything from that rule; per-finding mutes hide one row.
    Always returns the suppressed-tail too, so the UI can show "N hidden"
    and let the user un-mute."""
    visible: list = []
    suppressed: list = []
    for f in findings:
        if f.rule in sup.muted_rules or f.suppression_key in sup.muted_findings:
            suppressed.append(f)
        else:
            visible.append(f)
    return visible, suppressed
