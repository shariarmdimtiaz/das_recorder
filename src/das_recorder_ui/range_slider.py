from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets


class DistanceRangeSlider(QtWidgets.QWidget):
    """Integer two-handle slider for selecting a distance interval."""

    rangeChanged = QtCore.pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 100
        self._low = 0
        self._high = 100
        self._active_handle = "low"
        self._dragging = False
        self._handle_radius = 7
        self.setMinimumHeight(28)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Drag either handle to change the saved distance range")

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(500, 30)

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def lowValue(self) -> int:
        return self._low

    def highValue(self) -> int:
        return self._high

    def setRange(self, minimum: int, maximum: int) -> None:
        minimum = int(minimum)
        maximum = max(minimum + 1, int(maximum))
        self._minimum = minimum
        self._maximum = maximum
        self.setValues(self._low, self._high, emit=False)
        self.update()

    def setValues(self, low: int, high: int, *, emit: bool = False) -> None:
        low = max(self._minimum, min(int(low), self._maximum - 1))
        high = max(self._minimum + 1, min(int(high), self._maximum))
        if low >= high:
            if self._active_handle == "low":
                low = max(self._minimum, high - 1)
            else:
                high = min(self._maximum, low + 1)

        changed = low != self._low or high != self._high
        self._low = low
        self._high = high
        self.setToolTip(
            f"Saved distance: {self._low} m to {self._high} m"
        )
        self.update()
        if changed and emit:
            self.rangeChanged.emit(self._low, self._high)

    def _track_rect(self) -> QtCore.QRectF:
        margin = self._handle_radius + 2
        center_y = self.rect().center().y()
        return QtCore.QRectF(
            margin,
            center_y - 2,
            max(1, self.width() - (2 * margin)),
            4,
        )

    def _value_to_x(self, value: int) -> float:
        track = self._track_rect()
        ratio = (int(value) - self._minimum) / max(
            1,
            self._maximum - self._minimum,
        )
        return track.left() + ratio * track.width()

    def _x_to_value(self, x: float) -> int:
        track = self._track_rect()
        ratio = (float(x) - track.left()) / max(1.0, track.width())
        ratio = max(0.0, min(1.0, ratio))
        return int(
            round(
                self._minimum
                + ratio * (self._maximum - self._minimum)
            )
        )

    def paintEvent(self, event) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        track = self._track_rect()
        low_x = self._value_to_x(self._low)
        high_x = self._value_to_x(self._high)

        tick_color = QtGui.QColor("#c2ccd6")
        painter.setPen(QtGui.QPen(tick_color, 1))
        tick_count = min(60, max(10, self.width() // 18))
        for index in range(tick_count + 1):
            x = track.left() + (index / tick_count) * track.width()
            painter.drawLine(
                QtCore.QPointF(x, track.top() - 7),
                QtCore.QPointF(x, track.top() - 3),
            )
            painter.drawLine(
                QtCore.QPointF(x, track.bottom() + 3),
                QtCore.QPointF(x, track.bottom() + 7),
            )

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#d5dde5"))
        painter.drawRoundedRect(track, 2, 2)

        selected = QtCore.QRectF(
            low_x,
            track.top(),
            max(1.0, high_x - low_x),
            track.height(),
        )
        selected_color = (
            QtGui.QColor("#1683bd")
            if self.isEnabled()
            else QtGui.QColor("#91a7b5")
        )
        painter.setBrush(selected_color)
        painter.drawRoundedRect(selected, 2, 2)

        for name, x in (("low", low_x), ("high", high_x)):
            active = self.hasFocus() and name == self._active_handle
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor("#1677a8") if active else QtGui.QColor("#7d8994"),
                    2 if active else 1,
                )
            )
            painter.setBrush(
                QtGui.QColor("#ffffff")
                if self.isEnabled()
                else QtGui.QColor("#e7ebef")
            )
            painter.drawRoundedRect(
                QtCore.QRectF(
                    x - self._handle_radius,
                    self.rect().center().y() - 10,
                    self._handle_radius * 2,
                    20,
                ),
                3,
                3,
            )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self.isEnabled() or event.button() != QtCore.Qt.LeftButton:
            return
        low_distance = abs(event.x() - self._value_to_x(self._low))
        high_distance = abs(event.x() - self._value_to_x(self._high))
        self._active_handle = (
            "low" if low_distance <= high_distance else "high"
        )
        self._dragging = True
        self.setFocus(QtCore.Qt.MouseFocusReason)
        self._move_active_handle(event.x())

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging:
            self._move_active_handle(event.x())

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = False

    def _move_active_handle(self, x: float) -> None:
        value = self._x_to_value(x)
        if self._active_handle == "low":
            self.setValues(
                min(value, self._high - 1),
                self._high,
                emit=True,
            )
        else:
            self.setValues(
                self._low,
                max(value, self._low + 1),
                emit=True,
            )

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Tab:
            self._active_handle = (
                "high" if self._active_handle == "low" else "low"
            )
            self.update()
            event.accept()
            return

        if event.key() not in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            super().keyPressEvent(event)
            return

        step = -1 if event.key() == QtCore.Qt.Key_Left else 1
        if self._active_handle == "low":
            self.setValues(self._low + step, self._high, emit=True)
        else:
            self.setValues(self._low, self._high + step, emit=True)
        event.accept()
