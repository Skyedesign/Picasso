"""Command-line entry point: walk a folder, process images, emit outputs + QA report."""

from __future__ import annotations

import shutil
import sys
import webbrowser
from pathlib import Path

import click
from PIL import Image

from .config import Config, find_project_root
from .engine import (
    Detection,
    compute_group_stats,
    detect_product,
    normalize_to_canvas,
)
from .engine.stats import GroupStats, is_outlier
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

    cfg = _resolve_config(folder, config_path, overrides)

    image_paths = _find_images(folder)
    if not image_paths:
        click.echo(f"No images found in {folder}", err=True)
        sys.exit(1)

    click.echo(f"Scanning {len(image_paths)} images in {folder}...")

    detections: list[Detection] = []
    for p in image_paths:
        try:
            img = Image.open(p)
            img.load()
        except Exception as e:
            click.echo(f"  skip {p.name}: {e}", err=True)
            continue
        det = detect_product(p, img, bg_threshold=cfg.bg_threshold)
        detections.append(det)

    if not detections:
        click.echo("No images could be processed.", err=True)
        sys.exit(1)

    # ─── Filter pass ────────────────────────────────────────────────────
    # Each filter tags an image with a skip reason or leaves it as a candidate.
    # Skip decisions happen BEFORE group stats so lifestyle shots don't drag
    # the median toward huge-"product" scenes.
    skipped: list[tuple[Detection, str]] = []  # (detection, reason)
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
        click.echo("All images were filtered out — nothing to process.", err=True)

    target_override = None if cfg.target_ratio == "auto" else float(cfg.target_ratio)
    stats = compute_group_stats(
        candidates,
        tolerance_mad=cfg.tolerance_mad,
        target_override=target_override,
    ) if candidates else None

    if stats:
        click.echo(
            f"  group median occupied ratio: {stats.median_ratio:.3f} "
            f"(MAD {stats.mad:.3f}, bounds {stats.lower_bound:.3f}–{stats.upper_bound:.3f})"
        )

    processed_dir = folder / "processed"
    review_dir = folder / "review"
    skipped_dir = folder / "skipped"
    if not dry_run:
        processed_dir.mkdir(exist_ok=True)
        review_dir.mkdir(exist_ok=True)
        if skipped:
            skipped_dir.mkdir(exist_ok=True)

    report_rows = []
    n_processed = n_reviewed = n_outliers = n_skipped = 0

    # Skipped images first — unchanged copy, with the reason recorded.
    for det, reason in skipped:
        n_skipped += 1
        output_path = None
        if not dry_run:
            output_path = skipped_dir / det.source_path.name
            shutil.copy2(det.source_path, output_path)
        report_rows.append({
            "name": det.source_path.name,
            "status": f"skipped-{reason}",
            "occupied_ratio": det.occupied_ratio,
            "confidence": det.confidence,
            "bg_is_white": det.bg.is_white,
            "bg_purity": det.bg.purity,
            "output_path": output_path,
        })

    # Then the candidates — processed or sent to review.
    for det in candidates:
        outlier = is_outlier(det, stats) if stats else False
        low_conf = det.confidence < cfg.min_confidence
        status: str
        output_path: Path | None = None

        if low_conf:
            status = "review"
            n_reviewed += 1
            if not dry_run:
                output_path = review_dir / det.source_path.name
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
                output_path = processed_dir / det.source_path.name
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

    click.echo(
        f"  processed: {n_processed} (of which {n_outliers} rescaled as outliers), "
        f"review: {n_reviewed}, skipped: {n_skipped}"
    )

    if not dry_run:
        report_path = write_report(folder, detections, stats, report_rows, cfg)
        click.echo(f"  report: {report_path}")
        if open_report:
            webbrowser.open(report_path.as_uri())


if __name__ == "__main__":
    main()
