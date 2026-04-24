"""Pure-function image processing engine. No file I/O here — callers pass PIL Images."""

from .background import detect_background
from .detect import Detection, detect_product
from .normalize import normalize_to_canvas
from .stats import GroupStats, compute_group_stats

__all__ = [
    "detect_background",
    "Detection",
    "detect_product",
    "GroupStats",
    "compute_group_stats",
    "normalize_to_canvas",
]
