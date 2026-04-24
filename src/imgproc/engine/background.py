"""Background analysis: sample corners/edges to determine background color and purity.

Drives two downstream decisions:
- Which detector path to run (fast threshold vs. segmentation fallback).
- Confidence scoring for the detection.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass
class BackgroundInfo:
    mean_color: tuple[int, int, int]   # average RGB of the sampled edge pixels
    std: float                         # std-dev across sampled pixels; low = uniform
    is_white: bool                     # mean_color meets threshold on all channels
    purity: float                      # fraction of sampled pixels above threshold (0..1)


def detect_background(
    img: Image.Image,
    threshold: int = 245,
    sample_px: int = 20,
) -> BackgroundInfo:
    """Sample the four corners and compute background stats."""
    arr = np.asarray(img.convert("RGB"))
    h, w, _ = arr.shape
    sp = max(1, min(sample_px, h // 4, w // 4))
    corners = np.concatenate(
        [
            arr[:sp, :sp].reshape(-1, 3),
            arr[:sp, -sp:].reshape(-1, 3),
            arr[-sp:, :sp].reshape(-1, 3),
            arr[-sp:, -sp:].reshape(-1, 3),
        ],
        axis=0,
    )
    mean_color = tuple(int(x) for x in corners.mean(axis=0))
    std = float(corners.std())
    is_white = all(c >= threshold for c in mean_color)
    purity = float((corners.min(axis=1) >= threshold).mean())
    return BackgroundInfo(
        mean_color=mean_color,  # type: ignore[arg-type]
        std=std,
        is_white=is_white,
        purity=purity,
    )
