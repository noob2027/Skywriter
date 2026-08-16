"""Qt-native offline route canvas used without a map-network dependency."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    MissionAction,
)


class MissionMapCanvas(QWidget):
    """Render and edit mission coordinates on an offline schematic surface."""

    map_clicked = Signal(object)
    point_selected = Signal(int)
    point_dragged = Signal(int, object)

    _DEFAULT_CENTER = GeoPoint(38.8895, -77.0353)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("missionMapCanvas")
        self.setAccessibleName("Offline mission route canvas")
        self.setMinimumSize(520, 440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._actions: tuple[MissionAction, ...] = ()
        self._pending_point: GeoPoint | None = None
        self._selected_index: int | None = None
        self._screen_points: tuple[QPointF, ...] = ()
        self._drawing_rect = QRectF()
        self._bounds = (38.87, 38.91, -77.06, -77.01)
        self._drag_index: int | None = None

    def render_mission(
        self,
        actions: tuple[MissionAction, ...],
        pending_point: GeoPoint | None,
        selected_index: int | None,
    ) -> None:
        """Replace the sanitized render snapshot and schedule a repaint."""

        self._actions = tuple(actions)
        self._pending_point = pending_point
        self._selected_index = selected_index
        self.update()

    def simulate_click(self, point: GeoPoint) -> None:
        """Deterministic map-content hook used by contract fakes and headless tests."""

        self.map_clicked.emit(point)

    def simulate_drag(self, index: int, point: GeoPoint) -> None:
        """Deterministic coordinate-drag hook used by headless tests."""

        self.point_dragged.emit(index, point)

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_background(painter)
        self._update_projection()
        self._paint_grid(painter)
        self._paint_route(painter)
        for index, action in enumerate(self._actions):
            self._paint_action(painter, index, action, self._screen_points[index])
        if self._pending_point is not None:
            self._paint_pending(painter, self._project(self._pending_point))
        self._paint_legend(painter)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            return
        index = self._hit_test(event.position())
        if index is not None:
            self._drag_index = index
            self.point_selected.emit(index)
            event.accept()
            return
        self.map_clicked.emit(self._unproject(event.position()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton and self._drag_index is not None:
            self.point_dragged.emit(self._drag_index, self._unproject(event.position()))
            self._drag_index = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _paint_background(self, painter: QPainter) -> None:
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#edf4f3"))
        gradient.setColorAt(1, QColor("#dfe9e7"))
        painter.fillRect(self.rect(), gradient)
        painter.setPen(QColor("#183b3a"))
        painter.setFont(_font(11, QFont.Weight.DemiBold))
        painter.drawText(24, 30, "OFFLINE ROUTE CANVAS")
        painter.setPen(QColor("#52706d"))
        painter.setFont(_font(9))
        painter.drawText(24, 49, "Schematic coordinates • altitudes Above Home")

    def _paint_grid(self, painter: QPainter) -> None:
        painter.save()
        painter.setPen(QPen(QColor(84, 118, 113, 35), 1))
        for step in range(1, 8):
            x = self._drawing_rect.left() + self._drawing_rect.width() * step / 8
            y = self._drawing_rect.top() + self._drawing_rect.height() * step / 8
            painter.drawLine(
                QPointF(x, self._drawing_rect.top()), QPointF(x, self._drawing_rect.bottom())
            )
            painter.drawLine(
                QPointF(self._drawing_rect.left(), y), QPointF(self._drawing_rect.right(), y)
            )
        painter.setPen(QPen(QColor("#a5bbb7"), 1))
        painter.drawRoundedRect(self._drawing_rect, 14, 14)
        painter.restore()

    def _paint_route(self, painter: QPainter) -> None:
        if not self._screen_points:
            return
        path = QPainterPath(self._screen_points[0])
        for point in self._screen_points[1:]:
            path.lineTo(point)
        painter.setPen(QPen(QColor("#176b68"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(path)
        if self._pending_point is not None:
            painter.setPen(QPen(QColor("#e18a35"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(self._screen_points[-1], self._project(self._pending_point))

    def _paint_action(
        self, painter: QPainter, index: int, action: MissionAction, point: QPointF
    ) -> None:
        selected = index == self._selected_index
        if isinstance(action, CircleAction):
            self._paint_circle_cue(painter, action, point)

        painter.save()
        if selected:
            painter.setPen(QPen(QColor("#f4a340"), 4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(point, 18, 18)
        if isinstance(action, LandAction):
            painter.setPen(QPen(QColor("#7e2437"), 2))
            painter.setBrush(QColor("#f4d5dc"))
            polygon = QPolygonF(
                [
                    QPointF(point.x(), point.y() - 13),
                    QPointF(point.x() + 13, point.y()),
                    QPointF(point.x(), point.y() + 13),
                    QPointF(point.x() - 13, point.y()),
                ]
            )
            painter.drawPolygon(polygon)
            painter.setPen(QColor("#7e2437"))
            painter.setFont(_font(9, QFont.Weight.Bold))
            painter.drawText(
                QRectF(point.x() - 10, point.y() - 9, 20, 18), Qt.AlignmentFlag.AlignCenter, "L"
            )
        else:
            painter.setPen(QPen(QColor("#0c4543"), 2))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(point, 13, 13)
            painter.setPen(QColor("#0c4543"))
            painter.setFont(_font(9, QFont.Weight.Bold))
            painter.drawText(
                QRectF(point.x() - 11, point.y() - 10, 22, 20),
                Qt.AlignmentFlag.AlignCenter,
                str(index + 1),
            )

        altitude_m = (
            action.approach_altitude_m if isinstance(action, LandAction) else action.altitude_m
        )
        label = f"{altitude_m:g} m"
        if isinstance(action, HoldAction):
            label += f"  •  Hold {action.hold_time_s:g} s"
        elif isinstance(action, CircleAction):
            label += f"  •  Circle {action.radius_m:g} m clockwise"
        elif isinstance(action, LandAction):
            label += "  •  Land"
        self._paint_badge(painter, point + QPointF(18, -26), label)
        painter.restore()

    def _paint_circle_cue(self, painter: QPainter, action: CircleAction, center: QPointF) -> None:
        latitude_span = max(self._bounds[1] - self._bounds[0], 1e-9)
        radius_px = (action.radius_m / 111_320) / latitude_span * self._drawing_rect.height()
        radius_px = max(38.0, min(radius_px, 82.0))
        painter.save()
        painter.setPen(QPen(QColor("#9c5a14"), 2, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(225, 138, 53, 25))
        painter.drawEllipse(center, radius_px, radius_px)
        painter.setPen(QPen(QColor("#b76b1c"), 2))
        painter.drawLine(center, center + QPointF(radius_px, 0))
        painter.drawArc(
            QRectF(
                center.x() - radius_px,
                center.y() - radius_px,
                radius_px * 2,
                radius_px * 2,
            ),
            45 * 16,
            -250 * 16,
        )
        arrow = center + QPointF(-radius_px * 0.72, radius_px * 0.7)
        painter.setBrush(QColor("#b76b1c"))
        painter.drawPolygon(
            QPolygonF(
                [
                    arrow,
                    arrow + QPointF(-2, -9),
                    arrow + QPointF(8, -5),
                ]
            )
        )
        painter.restore()

    def _paint_pending(self, painter: QPainter, point: QPointF) -> None:
        painter.save()
        painter.setPen(QPen(QColor("#e18a35"), 3, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawEllipse(point, 15, 15)
        painter.setPen(QColor("#9c5a14"))
        painter.setFont(_font(9, QFont.Weight.Bold))
        painter.drawText(
            QRectF(point.x() - 12, point.y() - 10, 24, 20),
            Qt.AlignmentFlag.AlignCenter,
            str(len(self._actions) + 1),
        )
        self._paint_badge(painter, point + QPointF(20, 18), "Pending • choose an action")
        painter.restore()

    def _paint_badge(self, painter: QPainter, anchor: QPointF, text: str) -> None:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 18
        rectangle = QRectF(anchor.x(), anchor.y(), width, 24)
        painter.setPen(QPen(QColor(24, 59, 58, 45), 1))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawRoundedRect(rectangle, 8, 8)
        painter.setPen(QColor("#294f4d"))
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_legend(self, painter: QPainter) -> None:
        painter.setPen(QColor("#52706d"))
        painter.setFont(_font(8))
        painter.drawText(
            QRectF(24, self.height() - 28, self.width() - 48, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "Click to add • drag a numbered point to move • no tile network",
        )

    def _update_projection(self) -> None:
        self._drawing_rect = QRectF(24, 64, max(self.width() - 48, 1), max(self.height() - 108, 1))
        points = [action.point for action in self._actions]
        if self._pending_point is not None:
            points.append(self._pending_point)
        if not points:
            center = self._DEFAULT_CENTER
            self._bounds = (
                center.latitude_deg - 0.02,
                center.latitude_deg + 0.02,
                center.longitude_deg - 0.025,
                center.longitude_deg + 0.025,
            )
        else:
            latitudes = [point.latitude_deg for point in points]
            longitudes = [point.longitude_deg for point in points]
            lat_min, lat_max = min(latitudes), max(latitudes)
            lon_min, lon_max = min(longitudes), max(longitudes)
            lat_span = max(lat_max - lat_min, 0.01)
            lon_span = max(lon_max - lon_min, 0.012)
            self._bounds = (
                (lat_min + lat_max) / 2 - lat_span * 0.7,
                (lat_min + lat_max) / 2 + lat_span * 0.7,
                (lon_min + lon_max) / 2 - lon_span * 0.7,
                (lon_min + lon_max) / 2 + lon_span * 0.7,
            )
        self._screen_points = tuple(self._project(action.point) for action in self._actions)

    def _project(self, point: GeoPoint) -> QPointF:
        lat_min, lat_max, lon_min, lon_max = self._bounds
        x_fraction = (point.longitude_deg - lon_min) / max(lon_max - lon_min, 1e-9)
        y_fraction = (point.latitude_deg - lat_min) / max(lat_max - lat_min, 1e-9)
        return QPointF(
            self._drawing_rect.left() + x_fraction * self._drawing_rect.width(),
            self._drawing_rect.bottom() - y_fraction * self._drawing_rect.height(),
        )

    def _unproject(self, point: QPointF) -> GeoPoint:
        lat_min, lat_max, lon_min, lon_max = self._bounds
        x_fraction = (point.x() - self._drawing_rect.left()) / max(self._drawing_rect.width(), 1)
        y_fraction = (self._drawing_rect.bottom() - point.y()) / max(self._drawing_rect.height(), 1)
        latitude = lat_min + y_fraction * (lat_max - lat_min)
        longitude = lon_min + x_fraction * (lon_max - lon_min)
        return GeoPoint(
            latitude_deg=max(-90.0, min(latitude, 90.0)),
            longitude_deg=max(-180.0, min(longitude, 180.0)),
        )

    def _hit_test(self, point: QPointF) -> int | None:
        for index, screen_point in enumerate(self._screen_points):
            if math.hypot(point.x() - screen_point.x(), point.y() - screen_point.y()) <= 18:
                return index
        return None


def _font(point_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setPointSize(point_size)
    font.setWeight(weight)
    return font
