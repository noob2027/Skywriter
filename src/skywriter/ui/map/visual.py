"""Pixel-level acceptance checks for the mounted WebEngine map surface."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import cast

from PySide6.QtGui import QImage

CONTROLLED_TILE_RGB = (32, 170, 110)


@dataclass(frozen=True, slots=True)
class MapSurfaceEvidence:
    """Deterministic visual facts that distinguish pixels from JavaScript state."""

    width_px: int
    height_px: int
    sampled_pixels: int
    non_black_pixel_ratio: float
    fixture_pixel_ratio: float
    leaflet_controls_dom: bool
    leaflet_controls_visual: bool
    visual_ready: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_map_surface(
    image: QImage,
    dom_diagnostics: dict[str, object],
    *,
    expected_rgb: tuple[int, int, int] = CONTROLLED_TILE_RGB,
) -> MapSurfaceEvidence:
    """Require the controlled tile color and visible zoom-control pixels."""

    converted = image.convertToFormat(QImage.Format.Format_RGB32)
    width = converted.width()
    height = converted.height()
    if width <= 0 or height <= 0:
        return MapSurfaceEvidence(width, height, 0, 0.0, 0.0, False, False, False)

    sample_step = 2
    sampled = 0
    non_black = 0
    fixture = 0
    expected_red, expected_green, expected_blue = expected_rgb
    for y_value in range(0, height, sample_step):
        for x_value in range(0, width, sample_step):
            color = converted.pixelColor(x_value, y_value)
            red, green, blue = color.red(), color.green(), color.blue()
            sampled += 1
            if max(red, green, blue) > 12:
                non_black += 1
            exact_fixture_color = (
                abs(red - expected_red) <= 12
                and abs(green - expected_green) <= 12
                and abs(blue - expected_blue) <= 12
            )
            fixture_green_signature = (
                green >= 150 and green - red >= 30 and green - blue >= 15 and red <= 180
            )
            if exact_fixture_color or fixture_green_signature:
                fixture += 1

    control_rect = dom_diagnostics.get("zoom_control_rect")
    controls_dom = bool(dom_diagnostics.get("leaflet_controls_dom")) and isinstance(
        control_rect, dict
    )
    controls_visual = False
    if controls_dom:
        rect = cast(dict[str, object], control_rect)
        controls_visual = _control_pixels_visible(converted, rect)

    non_black_ratio = non_black / sampled
    fixture_ratio = fixture / sampled
    visual_ready = (
        non_black_ratio >= 0.50 and fixture_ratio >= 0.10 and controls_dom and controls_visual
    )
    return MapSurfaceEvidence(
        width_px=width,
        height_px=height,
        sampled_pixels=sampled,
        non_black_pixel_ratio=round(non_black_ratio, 6),
        fixture_pixel_ratio=round(fixture_ratio, 6),
        leaflet_controls_dom=controls_dom,
        leaflet_controls_visual=controls_visual,
        visual_ready=visual_ready,
    )


def _control_pixels_visible(image: QImage, rect: dict[str, object]) -> bool:
    coordinates = tuple(rect.get(key) for key in ("left", "top", "right", "bottom"))
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) for value in coordinates
    ):
        return False
    left_value, top_value, right_value, bottom_value = cast(
        tuple[int | float, int | float, int | float, int | float], coordinates
    )
    left = max(int(left_value), 0)
    top = max(int(top_value), 0)
    right = min(int(right_value) + 1, image.width())
    bottom = min(int(bottom_value) + 1, image.height())
    if right - left < 20 or bottom - top < 40:
        return False

    sampled = 0
    light = 0
    dark = 0
    for y_value in range(top, bottom):
        for x_value in range(left, right):
            color = image.pixelColor(x_value, y_value)
            red, green, blue = color.red(), color.green(), color.blue()
            sampled += 1
            if min(red, green, blue) >= 185:
                light += 1
            if max(red, green, blue) <= 90:
                dark += 1
    return sampled >= 800 and light / sampled >= 0.20 and dark / sampled >= 0.003
