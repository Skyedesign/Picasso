"""Normalization: rescale a detection and paste it onto a pure-white canvas."""

from __future__ import annotations

import math

from PIL import Image

from .detect import Detection

_RESAMPLE = Image.LANCZOS


def normalize_to_canvas(
    detection: Detection,
    *,
    target_ratio: float,
    canvas_size: tuple[int, int] = (600, 800),
    padding_pct: float = 5.0,
    max_upscale: float = 1.0,
    recenter_on_mask_centroid: bool = True,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Rescale `detection` so its bbox occupies `target_ratio` of the canvas area,
    then paste it centered onto a fresh pure-white canvas.

    `recenter_on_mask_centroid` picks between mask-weighted placement (biased toward
    visually heavy parts — flower heads, vase bodies) and bbox-geometric placement
    (silhouette-centered). The site convention is roughly silhouette-centered, so
    `False` matches the catalogue; `True` is available for experimentation.
    """
    canvas_w, canvas_h = canvas_size
    left, top, right, bottom = detection.bbox
    bbox_w, bbox_h = right - left, bottom - top

    canvas = Image.new("RGB", canvas_size, bg_color)
    if bbox_w <= 0 or bbox_h <= 0:
        # No detectable product — return a blank canvas; caller will flag it via confidence.
        return canvas

    # Scale factor so the bbox area on the canvas equals the desired ratio.
    desired_area = target_ratio * canvas_w * canvas_h
    current_area = bbox_w * bbox_h
    scale = math.sqrt(desired_area / current_area)

    # Clamp: never exceed max_upscale; always leave at least padding_pct breathing room.
    pad_frac = padding_pct / 100.0
    max_scale_by_w = (canvas_w * (1 - 2 * pad_frac)) / bbox_w
    max_scale_by_h = (canvas_h * (1 - 2 * pad_frac)) / bbox_h
    scale = min(scale, max_upscale, max_scale_by_w, max_scale_by_h)

    # Crop with a small margin so the resample has context at the edges.
    margin_x = max(1, int(bbox_w * 0.05))
    margin_y = max(1, int(bbox_h * 0.05))
    crop_box = (
        max(0, left - margin_x),
        max(0, top - margin_y),
        min(detection.width, right + margin_x),
        min(detection.height, bottom + margin_y),
    )
    crop = detection.image.crop(crop_box)

    new_w = max(1, int(round(crop.width * scale)))
    new_h = max(1, int(round(crop.height * scale)))
    scaled = crop.resize((new_w, new_h), _RESAMPLE)

    if recenter_on_mask_centroid:
        cx, cy = detection.centroid
        anchor_x = (cx - crop_box[0]) * scale
        anchor_y = (cy - crop_box[1]) * scale
    else:
        # Geometric center of the bbox, projected into scaled-crop coordinates.
        anchor_x = (left - crop_box[0] + bbox_w / 2) * scale
        anchor_y = (top - crop_box[1] + bbox_h / 2) * scale

    paste_x = int(round(canvas_w / 2 - anchor_x))
    paste_y = int(round(canvas_h / 2 - anchor_y))

    canvas.paste(scaled, (paste_x, paste_y))
    return canvas
