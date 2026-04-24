"""Group-level statistics: median occupied-ratio and MAD-based outlier flagging.

Median + MAD (not mean + stdev) because one huge outlier would otherwise drag the
target ratio toward itself — exactly the case we're trying to correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .detect import Detection


@dataclass
class GroupStats:
    median_ratio: float
    mad: float              # median absolute deviation (robust scale)
    target_ratio: float     # either `median_ratio` or a user-forced override
    lower_bound: float      # below this → image is too small, needs upscaling
    upper_bound: float      # above this → image is too large, needs downscaling


def _mad(values: np.ndarray, median: float) -> float:
    return float(np.median(np.abs(values - median)))


def compute_group_stats(
    detections: Sequence[Detection],
    *,
    tolerance_mad: float = 1.5,
    target_override: float | None = None,
) -> GroupStats:
    """Compute median occupied ratio and outlier bounds.

    If MAD is zero (all images identically sized), bounds collapse to the median and
    nothing is flagged — a sensible no-op.
    """
    if not detections:
        raise ValueError("cannot compute group stats on empty detection list")

    ratios = np.array([d.occupied_ratio for d in detections], dtype=np.float64)
    median = float(np.median(ratios))
    mad = _mad(ratios, median)
    target = target_override if target_override is not None else median
    delta = tolerance_mad * mad
    return GroupStats(
        median_ratio=median,
        mad=mad,
        target_ratio=target,
        lower_bound=max(0.0, median - delta),
        upper_bound=min(1.0, median + delta),
    )


def is_outlier(detection: Detection, stats: GroupStats) -> bool:
    return (
        detection.occupied_ratio < stats.lower_bound
        or detection.occupied_ratio > stats.upper_bound
    )
