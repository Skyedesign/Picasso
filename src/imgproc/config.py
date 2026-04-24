"""Configuration schema, validated via pydantic and loaded from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    tolerance_mad: float = Field(default=1.5, ge=0.0)
    target_ratio: float | Literal["auto"] = "auto"
    bg_threshold: int = Field(default=245, ge=0, le=255)
    padding_pct: float = Field(default=5.0, ge=0.0, le=40.0)
    recenter: bool = True
    min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    max_upscale: float = Field(default=1.0, ge=1.0)
    output_canvas: tuple[int, int] = (600, 800)

    @field_validator("target_ratio")
    @classmethod
    def _ratio_range(cls, v):
        if isinstance(v, float) and not (0.0 < v < 1.0):
            raise ValueError("target_ratio must be between 0 and 1, or the string 'auto'")
        return v

    @field_validator("output_canvas")
    @classmethod
    def _canvas_positive(cls, v):
        w, h = v
        if w <= 0 or h <= 0:
            raise ValueError("output_canvas dimensions must be positive")
        return v


def load_config(path: Path | None = None, overrides: dict | None = None) -> Config:
    """Load YAML config, then apply any overrides (e.g. a per-folder folder.yaml)."""
    data: dict = {}
    if path and path.exists():
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    if overrides:
        data.update(overrides)
    return Config(**data)
