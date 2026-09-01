"""Pixel-level map-surface acceptance tests."""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from skywriter.ui.map.visual import CONTROLLED_TILE_RGB, inspect_map_surface

_CONTROL_DOM = {
    "leaflet_controls_dom": True,
    "zoom_control_rect": {"left": 10, "top": 10, "right": 50, "bottom": 90},
}


def test_black_surface_fails_even_when_dom_and_tile_state_claim_success() -> None:
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    dom = {**_CONTROL_DOM, "loaded_tile_elements": 12}

    evidence = inspect_map_surface(image, dom)

    assert evidence.leaflet_controls_dom is True
    assert evidence.non_black_pixel_ratio == 0.0
    assert evidence.fixture_pixel_ratio == 0.0
    assert evidence.leaflet_controls_visual is False
    assert evidence.visual_ready is False


def test_controlled_tile_color_and_visible_leaflet_controls_pass() -> None:
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(QColor(*CONTROLLED_TILE_RGB))
    painter = QPainter(image)
    painter.fillRect(QRect(10, 10, 40, 80), QColor("white"))
    painter.setPen(QPen(QColor("black"), 4))
    painter.drawLine(20, 30, 40, 30)
    painter.drawLine(30, 20, 30, 40)
    painter.drawLine(20, 70, 40, 70)
    painter.end()

    evidence = inspect_map_surface(image, _CONTROL_DOM)

    assert evidence.non_black_pixel_ratio > 0.99
    assert evidence.fixture_pixel_ratio > 0.90
    assert evidence.leaflet_controls_dom is True
    assert evidence.leaflet_controls_visual is True
    assert evidence.visual_ready is True
