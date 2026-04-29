"""Configuration schema, validated via pydantic and loaded from YAML."""

from __future__ import annotations

import sys
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
    # Filters — each is a bool toggle + any parameters it needs. Images that match
    # a filter are copied (unchanged) into a `skipped/` folder with a reason tag
    # so Alida can eyeball why they were excluded.
    skip_lifestyle: bool = True
    lifestyle_bg_threshold: float = Field(default=0.85, ge=0.0, le=1.0)

    # Send to Pegasus (M6) — Picasso writes the SKU-named sorted output to
    # this folder; Syncthing (or any equivalent) replicates it to the NUC.
    # Default lands under the user's home dir so it's visible in Explorer
    # without further setup; blank disables Send entirely.
    sync_folder: str = "~/Picasso-to-Pegasus"

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


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: cwd) looking for a project marker file.

    This lets tools anchor their paths (batches/, imgproc.yaml) to the project
    root regardless of the directory the user launched them from — important for
    `imgproc-ui`, which ends up running with cwd=.venv/Scripts on Windows when
    users double-click the installed script.

    PyInstaller frozen mode: the exe's directory IS the project root in a
    deployed install — `imgproc.yaml` and `batches/` live next to picasso.exe.
    The walk-up heuristic doesn't apply because pyproject.toml isn't shipped.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).parent

    start = start or Path.cwd()
    for parent in [start, *start.parents]:
        if (parent / "imgproc.yaml").exists() or (parent / "pyproject.toml").exists():
            return parent
    return start


def load_config(path: Path | None = None, overrides: dict | None = None) -> Config:
    """Load YAML config, then apply any overrides (e.g. a per-folder folder.yaml)."""
    data: dict = {}
    if path and path.exists():
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    if overrides:
        data.update(overrides)
    return Config(**data)
