"""HTML report writer: generates thumbnails and renders the Jinja template."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageDraw

from ..config import Config
from ..engine.detect import Detection
from ..engine.stats import GroupStats

_THUMB_SIZE = (240, 320)


def _bbox_overlay(detection: Detection) -> Image.Image:
    """A thumbnail of the source image with the detected bbox drawn in red —
    invaluable for eyeballing whether the detector is finding the right thing."""
    img = detection.image.copy().convert("RGB")
    img.thumbnail(_THUMB_SIZE, Image.LANCZOS)

    sx = img.width / detection.width
    sy = img.height / detection.height
    left, top, right, bottom = detection.bbox
    draw = ImageDraw.Draw(img)
    draw.rectangle(
        [(left * sx, top * sy), (right * sx, bottom * sy)],
        outline=(220, 50, 50),
        width=2,
    )
    return img


def _thumb(src: Path | Image.Image) -> Image.Image:
    if isinstance(src, Path):
        img = Image.open(src).convert("RGB")
    else:
        img = src.convert("RGB")
    img.thumbnail(_THUMB_SIZE, Image.LANCZOS)
    return img


def write_report(
    folder: Path,
    detections: Sequence[Detection],
    stats: GroupStats | None,
    rows: list[dict],
    cfg: Config,
) -> Path:
    assets = folder / "_report_assets"
    assets.mkdir(exist_ok=True)

    det_by_name = {d.source_path.name: d for d in detections}

    for row in rows:
        name = row["name"]
        det = det_by_name[name]

        before = _bbox_overlay(det)
        before_path = assets / f"{Path(name).stem}_before.jpg"
        before.save(before_path, quality=82)
        row["before_thumb"] = f"_report_assets/{before_path.name}"

        # "After" is the processed output if we made one (processed/ only), otherwise
        # the original (so review/skipped images still have something side-by-side).
        show_after = (
            row["output_path"]
            and row["status"] != "review"
            and not row["status"].startswith("skipped")
        )
        if show_after:
            after = _thumb(Path(row["output_path"]))
        else:
            after = _thumb(det.image)
        after_path = assets / f"{Path(name).stem}_after.jpg"
        after.save(after_path, quality=82)
        row["after_thumb"] = f"_report_assets/{after_path.name}"

    # Group "skipped-*" statuses under a single "skipped" bucket for display; keep
    # the specific reason available for the per-card badge.
    counts = Counter()
    for r in rows:
        if r["status"].startswith("skipped"):
            counts["skipped"] += 1
        else:
            counts[r["status"]] += 1
    for k in ("within-tolerance", "outlier", "review", "skipped"):
        counts.setdefault(k, 0)

    template_dir = Path(__file__).parent
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape())
    template = env.get_template("template.html.j2")
    html = template.render(
        folder_name=folder.name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        stats=stats,
        rows=rows,
        counts=counts,
        canvas=cfg.output_canvas,
    )

    report_path = folder / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
