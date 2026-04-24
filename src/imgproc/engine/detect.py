"""Product detection: produce a bbox, mask, centroid, and confidence for an image."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .background import BackgroundInfo, detect_background


@dataclass
class Detection:
    source_path: Path
    image: Image.Image
    width: int
    height: int
    bbox: tuple[int, int, int, int]      # left, top, right, bottom (right/bottom exclusive)
    mask: np.ndarray                     # bool (H, W); True = product
    centroid: tuple[float, float]        # (x, y) in image pixel coords
    occupied_ratio: float                # bbox_area / (width * height)
    confidence: float                    # 0..1
    bg: BackgroundInfo


def _morph(mask: np.ndarray, radius: int, mode: str) -> np.ndarray:
    if radius <= 0:
        return mask
    img = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    k = 2 * radius + 1
    if mode == "dilate":
        img = img.filter(ImageFilter.MaxFilter(size=k))
    elif mode == "erode":
        img = img.filter(ImageFilter.MinFilter(size=k))
    else:
        raise ValueError(f"unknown morph mode: {mode}")
    return np.asarray(img) > 127


def _open(mask: np.ndarray, radius: int) -> np.ndarray:
    """Erode then dilate — removes speckles without shrinking the product."""
    return _morph(_morph(mask, radius, "erode"), radius, "dilate")


def _close(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilate then erode — fills small holes inside the product (e.g. gingham pattern)."""
    return _morph(_morph(mask, radius, "dilate"), radius, "erode")


def _bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    if not mask.any():
        return None
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def _centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(mask)
    return (float(xs.mean()), float(ys.mean()))


def _confidence(bg: BackgroundInfo, bbox: tuple[int, int, int, int], w: int, h: int) -> float:
    """Combine background purity with 'does the bbox touch an edge' into a 0..1 score.

    Low confidence typically means: off-white background, or product appears cropped.
    The CLI then sends these to ./review/ instead of auto-processing them.
    """
    left, top, right, bottom = bbox
    edge_margin_px = 2
    touches_edge = (
        left <= edge_margin_px
        or top <= edge_margin_px
        or right >= w - edge_margin_px
        or bottom >= h - edge_margin_px
    )
    edge_score = 0.0 if touches_edge else 1.0
    # Weight: background purity matters most (if bg isn't clean, mask is unreliable).
    return 0.6 * bg.purity + 0.4 * edge_score


def detect_product(
    source_path: Path,
    image: Image.Image,
    *,
    bg_threshold: int = 245,
) -> Detection:
    """Detect the product in `image`. Returns a Detection with bbox, mask, centroid.

    Fast path: corner-sampled threshold + morphological close/open. This handles
    clean-white product photography well. For off-white backgrounds a segmentation
    fallback will be added in milestone 6.
    """
    # Flatten alpha onto white so transparent PNGs don't register as black background.
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        image = flat
    else:
        image = image.convert("RGB")
    w, h = image.size
    bg = detect_background(image, threshold=bg_threshold)

    arr = np.asarray(image)
    raw = ~((arr >= bg_threshold).all(axis=-1))

    # Morphology radii scale with image size so behavior is resolution-invariant.
    dim = min(w, h)
    close_r = max(2, dim // 150)   # fills small white holes (gingham, inner whites)
    open_r = max(1, dim // 400)    # removes JPEG speckles and dust
    mask = _close(raw, close_r)
    mask = _open(mask, open_r)

    bbox = _bbox_from_mask(mask)
    if bbox is None:
        # No product detected — likely a blank frame or unexpected background.
        return Detection(
            source_path=source_path,
            image=image,
            width=w,
            height=h,
            bbox=(0, 0, w, h),
            mask=mask,
            centroid=(w / 2, h / 2),
            occupied_ratio=0.0,
            confidence=0.0,
            bg=bg,
        )

    left, top, right, bottom = bbox
    occupied_ratio = ((right - left) * (bottom - top)) / (w * h)
    centroid = _centroid(mask)
    confidence = _confidence(bg, bbox, w, h)

    return Detection(
        source_path=source_path,
        image=image,
        width=w,
        height=h,
        bbox=bbox,
        mask=mask,
        centroid=centroid,
        occupied_ratio=occupied_ratio,
        confidence=confidence,
        bg=bg,
    )
