"""Pytest fixtures: synthetic images for fast, deterministic tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _make_circle(size: tuple[int, int], bbox: tuple[int, int, int, int], color=(200, 50, 50)) -> Image.Image:
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).ellipse(bbox, fill=color)
    return img


@pytest.fixture
def synthetic_small_circle() -> Image.Image:
    return _make_circle((1000, 1000), (400, 400, 600, 600))


@pytest.fixture
def synthetic_large_circle() -> Image.Image:
    return _make_circle((1000, 1000), (100, 100, 900, 900))


@pytest.fixture
def synthetic_tall_stem() -> Image.Image:
    img = Image.new("RGB", (1000, 1200), (255, 255, 255))
    ImageDraw.Draw(img).rectangle((450, 100, 550, 1100), fill=(80, 130, 80))
    return img


@pytest.fixture
def folder_of_mixed(tmp_path: Path) -> Path:
    """A folder containing three similar small circles plus one outlier."""
    d = tmp_path / "mixed"
    d.mkdir()
    for i, bbox in enumerate([(400, 400, 600, 600), (390, 390, 610, 610), (410, 410, 590, 590)]):
        _make_circle((1000, 1000), bbox).save(d / f"small_{i}.jpg")
    _make_circle((1000, 1000), (100, 100, 900, 900), (50, 120, 200)).save(d / "huge.jpg")
    return d


@pytest.fixture
def folder_with_lifestyle(tmp_path: Path) -> Path:
    """A folder with two clean-white heroes and one 'lifestyle' image with noisy
    background (painted full-bleed so corner sampling picks up non-white pixels)."""
    d = tmp_path / "with_lifestyle"
    d.mkdir()
    _make_circle((1000, 1000), (400, 400, 600, 600)).save(d / "hero_a.jpg")
    _make_circle((1000, 1000), (390, 390, 610, 610)).save(d / "hero_b.jpg")

    # "Lifestyle" — fill the whole canvas with a mid-tone colour so corner purity
    # collapses to 0 (no corner pixel meets the white threshold).
    lifestyle = Image.new("RGB", (1000, 1000), (150, 120, 100))
    ImageDraw.Draw(lifestyle).ellipse((400, 400, 600, 600), fill=(200, 50, 50))
    lifestyle.save(d / "lifestyle.jpg")
    return d
