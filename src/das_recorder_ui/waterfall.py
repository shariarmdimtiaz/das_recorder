from __future__ import annotations

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg


class WaterfallWidget(QtWidgets.QFrame):
    """Rolling live DAS waterfall.

    Display direction used in this version:
        X-axis = distance / channel position along fiber
        Y-axis = recent time window
        Color  = signal amplitude / phase intensity

    The saved data format is not changed by this widget. This class only changes
    the live display orientation.
    """

    def __init__(
        self,
        sample_rate_hz: float,
        n_channels: int,
        window_seconds: int = 10,
        display_columns: int = 1200,
        max_display_channels: int = 900,
        distance_min_m: float = 0.0,
        distance_max_m: float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("WaterfallFrame")

        self.sample_rate_hz = float(sample_rate_hz)
        self.n_channels = int(n_channels)
        self.window_seconds = int(window_seconds)
        self.display_rows = int(display_columns)  # vertical time pixels/rows
        self.max_display_channels = int(max_display_channels)
        self.display_channels = min(self.n_channels, self.max_display_channels)
        self.distance_min_m = float(distance_min_m)
        self.distance_max_m = float(distance_max_m) if distance_max_m is not None else float(self.n_channels)

        # Internal buffer shape is (time_rows, distance_columns).
        # It is transposed when sent to ImageItem because pyqtgraph maps array
        # axis 0 to X and axis 1 to Y.
        self.buffer = np.zeros((self.display_rows, self.display_channels), dtype=np.float32)

        self.plot = pg.PlotWidget(background="k")
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=False, y=False)
        self.plot.setLabel("bottom", "Distance", units="m")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.setLabel("left", "Recent time", units="s")
        self.plot.setMouseEnabled(x=False, y=False)

        # Distance is horizontal, time is vertical.
        self.plot.setLimits(
            xMin=self.distance_min_m,
            xMax=self.distance_max_m,
            yMin=0,
            yMax=max(1, self.window_seconds),
        )
        self.plot.setXRange(self.distance_min_m, self.distance_max_m, padding=0)
        self.plot.setYRange(0, max(1, self.window_seconds), padding=0)
        self._update_distance_ticks()

        self.image_item = pg.ImageItem()
        self.plot.addItem(self.image_item)
        self._update_image_rect()

        # RGB heat palette used by the UI color bar: dark -> blue -> cyan -> green -> yellow -> red -> white.
        cmap = pg.ColorMap(
            [0.0, 0.18, 0.34, 0.52, 0.70, 0.86, 1.0],
            [
                (0, 0, 0),
                (0, 0, 160),
                (0, 180, 255),
                (64, 255, 64),
                (255, 255, 0),
                (255, 0, 0),
                (255, 255, 255),
            ],
        )
        self.image_item.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))
        self.image_item.setLevels([0, 2500])
        self.image_item.setImage(self.buffer.T, autoLevels=False)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)

    def _update_image_rect(self) -> None:
        """Map image pixels to real axes: X=distance, Y=time seconds."""
        width = max(1.0, float(self.distance_max_m) - float(self.distance_min_m))
        height = max(1.0, float(self.window_seconds))
        self.image_item.setRect(QtCore.QRectF(float(self.distance_min_m), 0.0, width, height))

    def _nice_distance_step(self, span_m: float, target_intervals: int = 8) -> float:
        """Return a clean tick step for the distance axis.

        For a 20000 m span this returns 2500 m, so the bottom axis becomes:
        0, 2500, 5000, 7500, ..., 20000 m.
        """
        span_m = max(1.0, float(span_m))
        raw_step = span_m / max(1, int(target_intervals))
        magnitude = 10 ** np.floor(np.log10(raw_step))
        normalized = raw_step / magnitude

        if normalized <= 1.0:
            nice = 1.0
        elif normalized <= 2.0:
            nice = 2.0
        elif normalized <= 2.5:
            nice = 2.5
        elif normalized <= 5.0:
            nice = 5.0
        else:
            nice = 10.0

        return float(nice * magnitude)

    def _format_distance_tick(self, value: float) -> str:
        if abs(value - round(value)) < 1e-6:
            return f"{int(round(value))} m"
        return f"{value:.1f} m"

    def _update_distance_ticks(self) -> None:
        """Show distance tick labels based on the current X-axis range."""
        min_m = float(self.distance_min_m)
        max_m = float(self.distance_max_m)
        if max_m <= min_m:
            max_m = min_m + 1.0

        span = max_m - min_m
        step = self._nice_distance_step(span, target_intervals=8)
        first = np.ceil(min_m / step) * step
        values = []

        if abs(first - min_m) > 1e-6:
            values.append(min_m)

        v = first
        # Limit to prevent accidentally creating too many tick labels.
        while v <= max_m + 1e-6 and len(values) < 20:
            values.append(float(v))
            v += step

        if not values or abs(values[-1] - max_m) > 1e-6:
            values.append(max_m)

        ticks = [(float(v), self._format_distance_tick(v)) for v in values]
        self.plot.getAxis("bottom").setTicks([ticks])

    def set_distance_range(self, distance_min_m: float, distance_max_m: float) -> None:
        """Update X-axis distance range without resetting the data buffer."""
        self.distance_min_m = float(distance_min_m)
        self.distance_max_m = float(distance_max_m)
        if self.distance_max_m <= self.distance_min_m:
            self.distance_max_m = self.distance_min_m + 1.0

        self._update_image_rect()
        self.plot.setLimits(
            xMin=self.distance_min_m,
            xMax=self.distance_max_m,
            yMin=0,
            yMax=max(1, self.window_seconds),
        )
        self.plot.setXRange(self.distance_min_m, self.distance_max_m, padding=0)
        self.plot.setYRange(0, max(1, self.window_seconds), padding=0)
        self._update_distance_ticks()

    def reset(
        self,
        sample_rate_hz: float,
        n_channels: int,
        window_seconds: int,
        display_columns: int,
        max_display_channels: int,
        distance_min_m: float = 0.0,
        distance_max_m: float | None = None,
    ) -> None:
        self.sample_rate_hz = float(sample_rate_hz)
        self.n_channels = int(n_channels)
        self.window_seconds = int(window_seconds)
        self.display_rows = int(display_columns)
        self.max_display_channels = int(max_display_channels)
        self.display_channels = min(self.n_channels, self.max_display_channels)
        self.distance_min_m = float(distance_min_m)
        self.distance_max_m = float(distance_max_m) if distance_max_m is not None else float(self.n_channels)
        if self.distance_max_m <= self.distance_min_m:
            self.distance_max_m = self.distance_min_m + 1.0

        self.buffer = np.zeros((self.display_rows, self.display_channels), dtype=np.float32)
        self._update_image_rect()
        self.plot.setLimits(
            xMin=self.distance_min_m,
            xMax=self.distance_max_m,
            yMin=0,
            yMax=max(1, self.window_seconds),
        )
        self.plot.setXRange(self.distance_min_m, self.distance_max_m, padding=0)
        self.plot.setYRange(0, max(1, self.window_seconds), padding=0)
        self._update_distance_ticks()
        self.image_item.setImage(self.buffer.T, autoLevels=False)

    def set_levels(self, low: float, high: float) -> None:
        if high <= low:
            high = low + 1
        self.image_item.setLevels([low, high])

    def _map_channels_for_display(self, data: np.ndarray) -> np.ndarray:
        """Map received channel rows into the configured distance grid.

        A short device stream occupies the beginning of the configured probing
        range. Unreceived channels remain zero/black instead of stretching the
        available rows across the full distance axis.
        """
        expected_channels = max(1, int(self.n_channels))
        display_channels = max(1, int(self.display_channels))
        available_channels = min(int(data.shape[0]), expected_channels)
        source_indices = np.linspace(
            0,
            expected_channels - 1,
            display_channels,
        ).astype(int)

        view = np.zeros(
            (display_channels, data.shape[1]),
            dtype=data.dtype,
        )
        available = source_indices < available_channels
        if np.any(available):
            view[available, :] = data[source_indices[available], :]
        return view

    def append_block(self, data: np.ndarray) -> None:
        """Append live DAS data block with shape (channels, samples).

        The input data convention stays the same:
            data.shape = (distance_channels, time_samples)

        Display conversion:
            input channels  -> horizontal distance axis
            input samples   -> vertical time axis
        """
        if data is None or data.ndim != 2:
            return

        # Preserve physical channel positions across the configured distance.
        # Missing/unreceived rows stay zero and display as black.
        view = self._map_channels_for_display(data)

        # Time decimation to avoid overloading the GUI.
        samples_per_row = max(1, int(round(self.sample_rate_hz * self.window_seconds / self.display_rows)))
        if view.shape[1] >= samples_per_row:
            usable = (view.shape[1] // samples_per_row) * samples_per_row
            view = view[:, :usable]
            view = view.reshape(view.shape[0], -1, samples_per_row).mean(axis=2)

        # view shape after decimation: (distance_channels, time_rows_new)
        rows = view.shape[1]
        if rows <= 0:
            return

        # Convert signed phase to display intensity. The field device uses
        # -15708 as a repeated no-data fill for inactive cable positions.
        # Mask it only in the live view; writers keep the original raw values.
        view_f = np.abs(view.astype(np.float32, copy=False))
        view_f[view == -15708] = 0.0

        # Any other high, perfectly flat channel is also a static fill value,
        # not a measurable DAS event. Keep the configured distance visible as
        # black/no signal instead of clipping the entire channel to white.
        flat_channels = np.ptp(view, axis=1) == 0
        if np.any(flat_channels):
            view_f[flat_channels, :] = 0.0
        new_rows = view_f.T

        if rows >= self.display_rows:
            self.buffer[:, :] = new_rows[-self.display_rows:, :]
        else:
            # Old time moves upward; newest time appears at the bottom.
            self.buffer = np.roll(self.buffer, -rows, axis=0)
            self.buffer[-rows:, :] = new_rows

        # ImageItem expects array axis 0 = X and axis 1 = Y.
        # buffer.T => X=distance, Y=time.
        self.image_item.setImage(self.buffer.T, autoLevels=False)
