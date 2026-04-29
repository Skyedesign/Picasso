"""Per-batch sidecar metadata (`batch.json`).

Writing a structured machine-state file alongside each batch is the v1
foundation for the visual reviewer (M2) and for v1.1's sub-batch grouping.
It is deliberately distinct from `folder.yaml` (user-edited config): two
audiences, two files, no merging surprises.

Schema fields are intentionally additive — bumping `schema_version` and
adding optional fields is the migration path; readers fall back gracefully
if a sidecar is missing or has an older shape.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1
SIDECAR_NAME = "batch.json"


class ImageVerdict(BaseModel):
    """Reviewer's decision on one image. Absent ⇒ image hasn't been reviewed."""
    decision: Literal["accepted", "rejected", "rerun"] = "accepted"
    # Re-run overrides — only meaningful when decision == "rerun".
    target_ratio: float | None = None
    bbox: tuple[int, int, int, int] | None = None    # left, top, right, bottom in pixel coords
    centroid: tuple[float, float] | None = None      # (x, y) in pixel coords
    canvas_size: tuple[int, int] | None = None       # if she swaps canvas mid-review
    note: str = ""
    timestamp: str = ""


class ImageRow(BaseModel):
    """One image's processing result. Mirrors the row dict the report writer
    consumes today, plus the reviewer-facing verdict slot."""
    name: str
    status: str  # "within-tolerance" | "outlier" | "review" | "skipped-<reason>"
    occupied_ratio: float
    confidence: float
    bg_is_white: bool
    bg_purity: float
    output_subfolder: str | None = None  # "processed" | "review" | "skipped" | None
    output_filename: str | None = None
    group: str | None = None             # v1.1 hook — always None in v1
    verdict: ImageVerdict | None = None


class BatchStats(BaseModel):
    """Group-level stats from `compute_group_stats` plus simple counts."""
    median_ratio: float
    mad: float
    target_ratio: float
    lower_bound: float
    upper_bound: float
    n_total: int
    n_processed: int
    n_review: int
    n_skipped: int


class BatchMeta(BaseModel):
    schema_version: int = SCHEMA_VERSION
    batch_name: str
    last_run_timestamp: str | None = None  # ISO 8601 UTC
    last_run_config: dict | None = None    # snapshot of the resolved Config
    stats: BatchStats | None = None
    images: list[ImageRow] = Field(default_factory=list)
    # Send-to-Pegasus state (M6). All optional — older sidecars without
    # these fields stay valid because Pydantic + the field defaults
    # cover the absent case.
    last_sent_at: str | None = None        # ISO 8601 UTC of last successful Send
    last_sent_count: int | None = None     # files copied in that send
    last_sent_dest: str | None = None      # absolute path to the destination folder
    pegasus_received_at: str | None = None # mirror of `.picasso-ack.json`'s timestamp
    # xlsx-as-batch-asset (post-M6): a batch can carry its own working
    # copy of Alida's pre-buy xlsx so Sheet check can write fixes back
    # without touching the source. Filename only — the file lives at
    # `{batch_folder}/{xlsx_filename}`. None means "no xlsx attached"
    # and the legacy path-based Sheet check / Sort flows still apply.
    xlsx_filename: str | None = None


# ─── I/O helpers ──────────────────────────────────────────────────────────

def sidecar_path(batch_folder: Path) -> Path:
    return batch_folder / SIDECAR_NAME


def read_meta(batch_folder: Path) -> BatchMeta | None:
    """Load the sidecar if it exists, else return None.

    Schema-version mismatch (newer schema being read by older code) returns
    None so the caller falls back to a fresh scan rather than raising.
    """
    p = sidecar_path(batch_folder)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("schema_version", 0) > SCHEMA_VERSION:
        return None
    try:
        return BatchMeta(**data)
    except Exception:
        return None


def write_meta(batch_folder: Path, meta: BatchMeta) -> Path:
    """Atomically write the sidecar. Tempfile in same dir → rename, so a
    crash mid-write can't truncate a previously-good batch.json."""
    p = sidecar_path(batch_folder)
    payload = meta.model_dump_json(indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".batch.", suffix=".json", dir=str(batch_folder))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, p)  # atomic on Win + POSIX
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return p


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def update_verdict(batch_folder: Path, image_name: str, verdict: ImageVerdict) -> BatchMeta:
    """Apply a single-image verdict and persist. Creates a minimal sidecar
    if one doesn't exist yet (e.g., legacy batch processed before this
    schema landed)."""
    return update_verdicts_bulk(batch_folder, {image_name: verdict})


def update_verdicts_bulk(batch_folder: Path, verdicts: dict[str, ImageVerdict]) -> BatchMeta:
    """Apply many verdicts in a single sidecar read+write.

    Filenames already present have their verdict slot updated; unknown
    filenames get appended as stub rows (mirrors `update_verdict`'s
    per-file behaviour, lets bulk-verdict on a legacy scanned batch still
    persist).
    """
    meta = read_meta(batch_folder) or BatchMeta(batch_name=batch_folder.name)
    by_name = {row.name: row for row in meta.images}
    for fname, verdict in verdicts.items():
        if fname in by_name:
            by_name[fname].verdict = verdict
        else:
            meta.images.append(ImageRow(
                name=fname,
                status="unknown",
                occupied_ratio=0.0,
                confidence=0.0,
                bg_is_white=False,
                bg_purity=0.0,
                verdict=verdict,
            ))
    write_meta(batch_folder, meta)
    return meta
