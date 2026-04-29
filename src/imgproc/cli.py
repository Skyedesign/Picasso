"""Command-line entry point: walk a folder, process images, emit outputs + QA report."""

from __future__ import annotations

import shutil
import sys
import webbrowser
from pathlib import Path

import click
from PIL import Image

from .batch_meta import (
    BatchMeta,
    BatchStats,
    ImageRow,
    now_iso,
    read_meta,
    write_meta,
)
from .config import Config, find_project_root
from .engine import (
    Detection,
    compute_group_stats,
    detect_product,
    normalize_to_canvas,
)
from .engine.stats import GroupStats, is_outlier
from .output import resolve_output_path, status_subfolder
from .report.writer import write_report

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _find_images(folder: Path) -> list[Path]:
    # Only scan the top level — a `processed/` subdirectory from a previous run would
    # otherwise get re-processed in circles.
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _resolve_config(folder: Path, config_path: Path | None, cli_overrides: dict) -> Config:
    # Precedence (low → high): base imgproc.yaml → folder.yaml → CLI flags.
    # Folder-level overrides let each product family tune its own settings without
    # touching the global defaults.
    import yaml  # local import keeps the CLI startup light when yaml is unused

    base_path = config_path or find_project_root() / "imgproc.yaml"
    data: dict = {}
    if base_path.exists():
        data.update(yaml.safe_load(base_path.read_text(encoding="utf-8")) or {})
    folder_override_path = folder / "folder.yaml"
    if folder_override_path.exists():
        data.update(yaml.safe_load(folder_override_path.read_text(encoding="utf-8")) or {})
    data.update(cli_overrides)
    return Config(**data)


def process_folder(
    folder: Path,
    *,
    config_path: Path | None = None,
    cli_overrides: dict | None = None,
    progress=None,
    log=None,
    dry_run: bool = False,
) -> dict:
    """Run the full batch pipeline on `folder`.

    Hot loops in this function call the `progress` callback with dicts of
    shape ``{"phase": "scanning"|"writing"|"report"|"done", "current": int,
    "total": int}`` so the web UI can render a live counter. The CLI
    command passes click.echo as `log`; the web server passes a closure
    that appends to the job's running log buffer. Both are optional —
    when omitted, the function runs silently.

    Returns a dict with `ok`, `error` (on failure), `stats`, the per-bucket
    counts, and `report_path` (None on dry-run or empty batch).
    """
    log = log or (lambda _: None)
    progress = progress or (lambda _: None)
    cli_overrides = cli_overrides or {}

    cfg = _resolve_config(folder, config_path, cli_overrides)

    image_paths = _find_images(folder)
    if not image_paths:
        log(f"No images found in {folder}")
        progress({"phase": "done", "current": 0, "total": 0})
        return {"ok": False, "error": "no images found"}

    n_total = len(image_paths)
    log(f"Scanning {n_total} images in {folder}...")
    progress({"phase": "scanning", "current": 0, "total": n_total})

    detections: list[Detection] = []
    for i, p in enumerate(image_paths, 1):
        try:
            img = Image.open(p)
            img.load()
        except Exception as e:
            log(f"  skip {p.name}: {e}")
            progress({"phase": "scanning", "current": i, "total": n_total})
            continue
        det = detect_product(p, img, bg_threshold=cfg.bg_threshold)
        detections.append(det)
        progress({"phase": "scanning", "current": i, "total": n_total})

    if not detections:
        log("No images could be processed.")
        progress({"phase": "done", "current": 0, "total": 0})
        return {"ok": False, "error": "no detections"}

    # ─── Filter pass ────────────────────────────────────────────────────
    # Each filter tags an image with a skip reason or leaves it as a candidate.
    # Skip decisions happen BEFORE group stats so lifestyle shots don't drag
    # the median toward huge-"product" scenes.
    skipped: list[tuple[Detection, str]] = []
    candidates: list[Detection] = []
    for det in detections:
        reason: str | None = None
        if cfg.skip_lifestyle and det.bg.purity < cfg.lifestyle_bg_threshold:
            reason = "lifestyle-bg"
        if reason:
            skipped.append((det, reason))
        else:
            candidates.append(det)

    if not candidates:
        log("All images were filtered out — nothing to process.")

    target_override = None if cfg.target_ratio == "auto" else float(cfg.target_ratio)
    stats = compute_group_stats(
        candidates,
        tolerance_mad=cfg.tolerance_mad,
        target_override=target_override,
    ) if candidates else None

    if stats:
        log(
            f"  group median occupied ratio: {stats.median_ratio:.3f} "
            f"(MAD {stats.mad:.3f}, bounds {stats.lower_bound:.3f}–{stats.upper_bound:.3f})"
        )

    # Output dirs are created lazily by `resolve_output_path` callers (parents
    # of each output file get mkdir'd on the way in). v1.1 sub-batches add
    # per-group subdirs without an upfront enumeration.
    report_rows: list[dict] = []
    n_processed = n_reviewed = n_outliers = n_skipped = 0
    written = 0
    n_writes = len(detections)
    progress({"phase": "writing", "current": 0, "total": n_writes})

    for det, reason in skipped:
        n_skipped += 1
        status = f"skipped-{reason}"
        output_path = None
        if not dry_run:
            output_path = resolve_output_path(folder, det.source_path.name, status, group=None)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(det.source_path, output_path)
        report_rows.append({
            "name": det.source_path.name,
            "status": status,
            "occupied_ratio": det.occupied_ratio,
            "confidence": det.confidence,
            "bg_is_white": det.bg.is_white,
            "bg_purity": det.bg.purity,
            "output_path": output_path,
        })
        written += 1
        progress({"phase": "writing", "current": written, "total": n_writes})

    for det in candidates:
        outlier = is_outlier(det, stats) if stats else False
        low_conf = det.confidence < cfg.min_confidence
        status: str
        output_path: Path | None = None

        if low_conf:
            status = "review"
            n_reviewed += 1
            if not dry_run:
                output_path = resolve_output_path(folder, det.source_path.name, status, group=None)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(det.source_path, output_path)
        else:
            normalized = normalize_to_canvas(
                det,
                target_ratio=stats.target_ratio,
                canvas_size=cfg.output_canvas,
                padding_pct=cfg.padding_pct,
                max_upscale=cfg.max_upscale,
                recenter_on_mask_centroid=cfg.recenter,
            )
            status = "outlier" if outlier else "within-tolerance"
            if outlier:
                n_outliers += 1
            n_processed += 1
            if not dry_run:
                output_path = resolve_output_path(folder, det.source_path.name, status, group=None)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                normalized.save(output_path, quality=92)

        report_rows.append({
            "name": det.source_path.name,
            "status": status,
            "occupied_ratio": det.occupied_ratio,
            "confidence": det.confidence,
            "bg_is_white": det.bg.is_white,
            "bg_purity": det.bg.purity,
            "output_path": output_path,
        })
        written += 1
        progress({"phase": "writing", "current": written, "total": n_writes})

    log(
        f"  processed: {n_processed} (of which {n_outliers} rescaled as outliers), "
        f"review: {n_reviewed}, skipped: {n_skipped}"
    )

    report_path: Path | None = None
    if not dry_run:
        progress({"phase": "report", "current": 0, "total": 1})
        report_path = write_report(folder, detections, stats, report_rows, cfg)
        log(f"  report: {report_path}")
        # Sidecar batch.json mirrors the report rows in machine-readable form
        # so the visual reviewer (M2) can render the batch without re-running
        # detection. Sibling to report.html, distinct from user-edited
        # folder.yaml.
        meta = _build_batch_meta(folder, report_rows, stats, cfg)
        write_meta(folder, meta)
        progress({"phase": "report", "current": 1, "total": 1})

    progress({"phase": "done", "current": n_writes, "total": n_writes})
    return {
        "ok": True,
        "stats": stats,
        "n_total": n_total,
        "n_processed": n_processed,
        "n_outliers": n_outliers,
        "n_reviewed": n_reviewed,
        "n_skipped": n_skipped,
        "report_path": report_path,
    }


@click.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Path to imgproc.yaml (defaults to ./imgproc.yaml).")
@click.option("--tolerance", type=float, help="Override tolerance_mad for this run.")
@click.option("--target-ratio", type=float, help="Force a target occupied ratio (0..1); overrides 'auto'.")
@click.option("--dry-run", is_flag=True, help="Analyze and report only; do not write processed images.")
@click.option("--open-report/--no-open-report", default=True, help="Open the QA report in a browser on completion.")
def main(
    folder: Path,
    config_path: Path | None,
    tolerance: float | None,
    target_ratio: float | None,
    dry_run: bool,
    open_report: bool,
) -> None:
    """Resize a folder of product images so they share a consistent fill ratio."""
    overrides: dict = {}
    if tolerance is not None:
        overrides["tolerance_mad"] = tolerance
    if target_ratio is not None:
        overrides["target_ratio"] = target_ratio

    result = process_folder(
        folder,
        config_path=config_path,
        cli_overrides=overrides,
        log=click.echo,
        dry_run=dry_run,
    )
    if not result["ok"]:
        # `log` already wrote a human-readable error line via click.echo.
        sys.exit(1)
    if open_report and result.get("report_path"):
        webbrowser.open(result["report_path"].as_uri())


def _build_batch_meta(
    folder: Path,
    report_rows: list[dict],
    stats: GroupStats | None,
    cfg: Config,
) -> BatchMeta:
    """Translate the CLI's row dicts + stats into the BatchMeta sidecar shape.

    Carries forward fields owned outside the CLI — `xlsx_filename`
    (set by the import / attach flow), the send-to-Pegasus state
    (`last_sent_at` etc.), and per-image verdicts. Without this merge,
    every Process would silently wipe Alida's reviewer accept/reject
    decisions and detach the working xlsx, which is what the
    SHEET C CHRISTMAS BELLS bug exposed.
    """
    prior = read_meta(folder)
    prior_verdicts: dict[str, "ImageVerdict"] = {}
    if prior:
        for row in prior.images:
            if row.verdict is not None:
                prior_verdicts[row.name] = row.verdict

    images: list[ImageRow] = []
    for row in report_rows:
        out_path: Path | None = row.get("output_path")
        sub = status_subfolder(row["status"]) if out_path else None
        images.append(ImageRow(
            name=row["name"],
            status=row["status"],
            occupied_ratio=row["occupied_ratio"],
            confidence=row["confidence"],
            bg_is_white=row["bg_is_white"],
            bg_purity=row["bg_purity"],
            output_subfolder=sub,
            output_filename=out_path.name if out_path else None,
            group=None,  # v1.1 hook
            # Preserve any prior reviewer verdict on this filename. A
            # re-process re-derives status / metrics / output, but the
            # verdict belongs to the user, not the engine.
            verdict=prior_verdicts.get(row["name"]),
        ))
    n_total = len(images)
    n_processed = sum(1 for r in images if r.status in ("within-tolerance", "outlier"))
    n_review = sum(1 for r in images if r.status == "review")
    n_skipped = sum(1 for r in images if r.status.startswith("skipped"))
    bs = None
    if stats is not None:
        bs = BatchStats(
            median_ratio=stats.median_ratio,
            mad=stats.mad,
            target_ratio=stats.target_ratio,
            lower_bound=stats.lower_bound,
            upper_bound=stats.upper_bound,
            n_total=n_total,
            n_processed=n_processed,
            n_review=n_review,
            n_skipped=n_skipped,
        )
    return BatchMeta(
        batch_name=folder.name,
        last_run_timestamp=now_iso(),
        last_run_config=cfg.model_dump(),
        stats=bs,
        images=images,
        # Preserve attach + send state from the prior sidecar. These are
        # batch-level facts the engine has no opinion on.
        xlsx_filename=prior.xlsx_filename if prior else None,
        last_sent_at=prior.last_sent_at if prior else None,
        last_sent_count=prior.last_sent_count if prior else None,
        last_sent_dest=prior.last_sent_dest if prior else None,
        pegasus_received_at=prior.pegasus_received_at if prior else None,
    )


if __name__ == "__main__":
    main()
