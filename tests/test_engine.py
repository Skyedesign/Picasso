"""Engine-level tests: detection, stats, normalization."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from imgproc.engine import (
    compute_group_stats,
    detect_product,
    normalize_to_canvas,
)
from imgproc.engine.stats import is_outlier


def test_detect_bbox_matches_drawn_shape(synthetic_small_circle: Image.Image) -> None:
    det = detect_product(Path("synthetic"), synthetic_small_circle)
    # The drawn ellipse inhabits (400, 400)-(600, 600). Anti-aliased edges can nudge
    # the detected bbox by a pixel or two, so allow a small tolerance.
    left, top, right, bottom = det.bbox
    assert abs(left - 400) <= 3
    assert abs(top - 400) <= 3
    assert abs(right - 600) <= 3
    assert abs(bottom - 600) <= 3
    assert det.confidence > 0.8
    assert det.bg.is_white


def test_detect_flags_cropped_product_as_low_confidence(synthetic_large_circle: Image.Image) -> None:
    # Circle goes from (100,100) to (900,900) on a 1000x1000 canvas → doesn't touch edge.
    det = detect_product(Path("large"), synthetic_large_circle)
    assert det.confidence > 0.8  # still high; not touching edge

    # Now force an edge-touching bbox by cropping.
    cropped = synthetic_large_circle.crop((100, 100, 900, 900))
    det_cropped = detect_product(Path("cropped"), cropped)
    assert det_cropped.confidence < det.confidence


def test_group_stats_identifies_outlier(folder_of_mixed: Path) -> None:
    detections = [
        detect_product(p, Image.open(p))
        for p in sorted(folder_of_mixed.iterdir())
        if p.suffix == ".jpg"
    ]
    stats = compute_group_stats(detections, tolerance_mad=1.5)
    outliers = [d for d in detections if is_outlier(d, stats)]
    assert len(outliers) == 1
    assert outliers[0].source_path.name == "huge.jpg"


def test_normalize_produces_canvas_at_target_ratio(synthetic_large_circle: Image.Image) -> None:
    det = detect_product(Path("large"), synthetic_large_circle)
    out = normalize_to_canvas(
        det,
        target_ratio=0.1,
        canvas_size=(600, 800),
        padding_pct=5.0,
        max_upscale=1.0,
    )
    assert out.size == (600, 800)
    # Re-detect and verify the rescaled product lands near the target (within 1%).
    out_det = detect_product(Path("out"), out)
    assert abs(out_det.occupied_ratio - 0.1) < 0.02


def test_normalize_respects_max_upscale(synthetic_small_circle: Image.Image) -> None:
    det = detect_product(Path("small"), synthetic_small_circle)
    # Ask for a huge target — should be capped because max_upscale=1.0.
    out = normalize_to_canvas(
        det,
        target_ratio=0.9,
        canvas_size=(600, 800),
        max_upscale=1.0,
    )
    out_det = detect_product(Path("out"), out)
    # With a 200x200 bbox on 1000x1000 source, scale=1.0 → bbox=200x200 on canvas
    # → ratio = 200*200/(600*800) = 0.0833. Assert it didn't balloon past that.
    assert out_det.occupied_ratio < 0.1
