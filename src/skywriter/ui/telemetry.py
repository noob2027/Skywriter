"""Shared read-only telemetry presentation helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QVBoxLayout, QWidget

from skywriter.application.telemetry import (
    TelemetryFreshness,
    TelemetryMapLayers,
    TelemetryPoint,
    TelemetryRoutePoint,
    TimedSignal,
)

T = TypeVar("T")


class TelemetryCard(QFrame):
    """Small status card that never converts missing data into a healthy state."""

    def __init__(self, title: str, object_name: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #586474; font-size: 12px; font-weight: 700;")
        self.value_label = QLabel("Unavailable")
        self.value_label.setObjectName(f"{object_name}Value")
        self.value_label.setWordWrap(True)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        self.set_state(TelemetryFreshness.UNAVAILABLE)

    def set_value(self, value: str, freshness: TelemetryFreshness) -> None:
        self.value_label.setText(value)
        self.set_state(freshness)

    def set_state(self, freshness: TelemetryFreshness) -> None:
        self.setProperty("freshness", freshness.value)
        colors = {
            TelemetryFreshness.FRESH: ("#e7f5ee", "#1f6b45"),
            TelemetryFreshness.STALE: ("#fff2d6", "#7b4e00"),
            TelemetryFreshness.UNAVAILABLE: ("#edf0f4", "#59636f"),
        }
        background, foreground = colors[freshness]
        self.setStyleSheet(
            f"QFrame {{ background: {background}; border: 1px solid {foreground}; "
            "border-radius: 6px; }} "
            f"QLabel {{ border: none; color: {foreground}; }}"
        )


class NativeMessagesList(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("nativeStatusMessages")
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)

    def render_messages(self, messages: Iterable[tuple[int, str]]) -> None:
        self.clear()
        for severity, text in messages:
            self.addItem(f"Severity {severity} · {text}")
        if self.count() == 0:
            self.addItem("No native status messages received")


class TelemetryMapLayersWidget(QWidget):
    """Read-only deterministic vehicle/home/route layer renderer."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("telemetryMapLayers")
        self.setAccessibleName("Read-only aircraft and mission telemetry map layers")
        self.setMinimumHeight(310)
        self._layers = TelemetryMapLayers(None, None, None, (), ())
        self.setStyleSheet("background: #eef3f5; border: 1px solid #91a2ad; border-radius: 8px;")

    def set_layers(self, layers: TelemetryMapLayers) -> None:
        self._layers = layers
        self.update()

    @property
    def layers(self) -> TelemetryMapLayers:
        return self._layers

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#eef3f5"))
        painter.setPen(QColor("#52616b"))
        painter.setFont(QFont("Arial", 10))
        painter.drawText(16, 24, "READ-ONLY TELEMETRY LAYERS · no basemap")

        points = [point.point for point in self._layers.completed_route]
        points.extend(point.point for point in self._layers.remaining_route)
        if self._layers.aircraft is not None:
            points.append(self._layers.aircraft.point)
        if self._layers.home is not None:
            points.append(self._layers.home.point)
        if not points:
            painter.setPen(QColor("#6d7780"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Position and route unavailable"
            )
            painter.end()
            return

        viewport = QRectF(34, 42, max(self.width() - 68, 1), max(self.height() - 76, 1))
        projector = _projector(points, viewport)
        self._draw_route(painter, self._layers.remaining_route, projector, QColor("#59738a"))
        self._draw_route(painter, self._layers.completed_route, projector, QColor("#23845c"))
        if self._layers.completed_route and self._layers.remaining_route:
            painter.setPen(QPen(QColor("#59738a"), 4))
            painter.drawLine(
                projector(self._layers.completed_route[-1].point),
                projector(self._layers.remaining_route[0].point),
            )

        if self._layers.home is not None:
            center = projector(self._layers.home.point)
            painter.setPen(QPen(QColor("#24576c"), 2))
            painter.setBrush(QColor("#d8f1fa"))
            painter.drawRect(QRectF(center.x() - 6, center.y() - 6, 12, 12))
            painter.drawText(center + QPointF(9, 18), "Home")
        if self._layers.current_target is not None:
            center = projector(self._layers.current_target.point)
            painter.setPen(QPen(QColor("#b36b00"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 10, 10)
            painter.drawText(center + QPointF(12, 4), "Current target")
        if self._layers.aircraft is not None:
            center = projector(self._layers.aircraft.point)
            painter.setPen(QPen(QColor("#173f5f"), 2))
            painter.setBrush(QColor("#2b7bbb"))
            polygon = QPolygonF(
                [
                    center + QPointF(0, -11),
                    center + QPointF(8, 9),
                    center,
                    center + QPointF(-8, 9),
                ]
            )
            painter.drawPolygon(polygon)
            painter.drawText(center + QPointF(12, -8), "Aircraft")
        painter.end()

    def _draw_route(
        self,
        painter: QPainter,
        route: tuple[TelemetryRoutePoint, ...],
        projector: Callable[[TelemetryPoint], QPointF],
        color: QColor,
    ) -> None:
        typed_route = tuple(route)
        if len(typed_route) > 1:
            painter.setPen(QPen(color, 4))
            for first, second in zip(typed_route, typed_route[1:], strict=False):
                painter.drawLine(projector(first.point), projector(second.point))
        painter.setPen(QPen(color, 2))
        painter.setBrush(color)
        for point in typed_route:
            center = projector(point.point)
            painter.drawEllipse(center, 4, 4)


def render_signal(
    card: TelemetryCard,
    signal: TimedSignal[T],
    *,
    now_s: float,
    formatter: Callable[[T], str],
) -> None:
    freshness = signal.freshness(now_s)
    if signal.value is None:
        card.set_value("Unavailable", TelemetryFreshness.UNAVAILABLE)
        return
    value = formatter(signal.value)
    card.set_value(
        f"Stale · {value}" if freshness is TelemetryFreshness.STALE else value,
        freshness,
    )


def _projector(
    points: list[TelemetryPoint], viewport: QRectF
) -> Callable[[TelemetryPoint], QPointF]:
    latitudes = [point.latitude_deg for point in points]
    longitudes = [point.longitude_deg for point in points]
    min_latitude, max_latitude = min(latitudes), max(latitudes)
    min_longitude, max_longitude = min(longitudes), max(longitudes)
    latitude_span = max(max_latitude - min_latitude, 0.0001)
    longitude_span = max(max_longitude - min_longitude, 0.0001)

    def project(point: TelemetryPoint) -> QPointF:
        x = viewport.left() + (
            (point.longitude_deg - min_longitude) / longitude_span * viewport.width()
        )
        y = viewport.bottom() - (
            (point.latitude_deg - min_latitude) / latitude_span * viewport.height()
        )
        return QPointF(x, y)

    return project
