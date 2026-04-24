"""Single-image hero resize: detect → normalize → save at the validated 600x800.

Complement to the batch `imgproc` CLI. The batch tool derives its target fill ratio
from the group's median occupied ratio; that doesn't exist for a single image, so
`--target-ratio` is required and the caller (e.g. Pegasus at ingest time) is
expected to hold the tuned value in its own config.

All other pipeline settings (canvas size, padding, upscale cap, recenter, bg
threshold) are loaded from the project's imgproc.yaml so the batch and single-
image paths stay in lockstep.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from PIL import Image

from ..config import Config, find_project_root, load_config
from ..engine import detect_product, normalize_to_canvas


@click.command()
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", "output_path", required=True,
              type=click.Path(dir_okay=False, path_type=Path),
              help="Destination path for the resized image.")
@click.option("--target-ratio", type=click.FloatRange(0.0, 1.0, min_open=True, max_open=True),
              required=True,
              help="Target occupied ratio (product area / canvas area). "
                   "No batch median exists for a single image, so this must be set explicitly.")
@click.option("--config", "config_path",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Path to imgproc.yaml (defaults to the project root's imgproc.yaml).")
@click.option("--quality", type=click.IntRange(1, 100), default=92, show_default=True,
              help="JPEG quality for the output.")
def main(
    input_path: Path,
    output_path: Path,
    target_ratio: float,
    config_path: Path | None,
    quality: int,
) -> None:
    """Resize a single hero image using the validated Picasso pipeline."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    cfg: Config = load_config(config_path or find_project_root() / "imgproc.yaml")

    try:
        img = Image.open(input_path)
        img.load()
    except Exception as e:
        click.echo(f"cannot read {input_path}: {e}", err=True)
        sys.exit(1)

    det = detect_product(input_path, img, bg_threshold=cfg.bg_threshold)
    if det.bbox[2] - det.bbox[0] <= 0 or det.bbox[3] - det.bbox[1] <= 0:
        click.echo(f"no product detected in {input_path}", err=True)
        sys.exit(2)

    normalized = normalize_to_canvas(
        det,
        target_ratio=target_ratio,
        canvas_size=cfg.output_canvas,
        padding_pct=cfg.padding_pct,
        max_upscale=cfg.max_upscale,
        recenter_on_mask_centroid=cfg.recenter,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(output_path, quality=quality)
    click.echo(f"wrote {output_path} ({cfg.output_canvas[0]}x{cfg.output_canvas[1]}, "
               f"target_ratio={target_ratio}, confidence={det.confidence:.2f})")


if __name__ == "__main__":
    main()
