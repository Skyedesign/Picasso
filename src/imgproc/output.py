"""Output-path routing.

Centralises where a processed/review/skipped image lands inside a batch.
v1 always writes to `<batch>/<sub>/<name>`; v1.1 will introduce sub-batch
groups (e.g., "stockings" inside a Textiles batch) and only this module will
need to learn the new layout.
"""

from __future__ import annotations

from pathlib import Path


def status_subfolder(status: str) -> str | None:
    """Map a processing status to the destination subfolder name.

    Returns None for statuses that don't produce an output (e.g. dry-run).
    """
    if status in ("within-tolerance", "outlier"):
        return "processed"
    if status == "review":
        return "review"
    if status.startswith("skipped"):
        return "skipped"
    return None


def resolve_output_path(
    batch_folder: Path,
    image_name: str,
    status: str,
    group: str | None = None,
) -> Path | None:
    """Resolve the output Path for an image given its processing status.

    `group` is accepted for forward compatibility with v1.1 sub-batches; in
    v1 it MUST be None and is ignored. Callers should keep passing `group=None`
    explicitly so the v1.1 transition is a one-line change.
    """
    sub = status_subfolder(status)
    if sub is None:
        return None
    if group:
        # Reserved for v1.1 — caller mistake to pass non-None today.
        return batch_folder / sub / group / image_name
    return batch_folder / sub / image_name
