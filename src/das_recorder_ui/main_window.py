from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

from PyQt5 import QtCore, QtWidgets, QtGui

from .config import (
    DEFAULT_APP_VERSION,
    RecorderConfig,
    clock_channel_spacing_m,
    phase_output_sample_rate_hz,
)
from .firewall import configure_dunay_firewall_rules
from .range_slider import DistanceRangeSlider
from .recorder_worker import RecorderWorker
from .style import APP_QSS
from .waterfall import WaterfallWidget


class Section(QtWidgets.QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("SectionTitle")
        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QFormLayout(self.body)
        self.body_layout.setContentsMargins(8, 7, 8, 7)
        self.body_layout.setSpacing(5)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title)
        layout.addWidget(self.body)


class RgbLevelSlider(QtWidgets.QSlider):
    """Thin vertical slider painted like the RGB controls in the reference UI."""

    def __init__(self, color: QtGui.QColor, parent=None):
        super().__init__(QtCore.Qt.Vertical, parent)
        self.track_color = QtGui.QColor(color)
        self.setFixedWidth(26)
        self.setMinimumHeight(180)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Waterfall brightness level")

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = self.rect()
        top = 3
        bottom = rect.height() - 4
        rail_x = rect.center().x() + 3

        painter.setPen(QtGui.QPen(QtGui.QColor(80, 80, 80), 1))
        painter.drawLine(rail_x - 2, top, rail_x - 2, bottom)
        painter.drawLine(rail_x + 2, top, rail_x + 2, bottom)

        painter.setPen(QtGui.QPen(self.track_color, 3))
        painter.drawLine(rail_x, top, rail_x, bottom)

        span = max(1, self.maximum() - self.minimum())
        ratio = (self.value() - self.minimum()) / span
        y = bottom - int(round(ratio * (bottom - top)))

        triangle = QtGui.QPolygon(
            [
                QtCore.QPoint(1, y - 7),
                QtCore.QPoint(1, y + 7),
                QtCore.QPoint(rail_x - 5, y),
            ]
        )
        painter.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        painter.setBrush(QtGui.QBrush(QtCore.Qt.black))
        painter.drawPolygon(triangle)

        painter.setPen(QtGui.QPen(QtGui.QColor(45, 45, 45), 1))
        painter.drawLine(rail_x + 6, y, rail_x + 12, y)


class RgbControlPanel(QtWidgets.QWidget):
    """Light side panel with fixed Brighter/Darker labels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(104)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        config: RecorderConfig,
        config_path: str | Path | None = None,
    ):
        super().__init__()
        self.config = config
        self.app_version = (
            str(getattr(config, "app_version", DEFAULT_APP_VERSION)).strip()
            or DEFAULT_APP_VERSION
        )
        self.config_path = Path(config_path).resolve() if config_path else None
        self.thread = None
        self.worker = None
        self.connecting = False
        self.disconnecting = False
        self.saving_active = False

        # Connection state is packet-based, not only socket-based.
        # Green is shown only after at least one data block/packet arrives.
        self.last_packet_time = None
        self.packet_timeout_s = 3.0
        self.connection_ok = False
        self.device_initialized = False
        self.phase_received_count = 0
        self.last_received_channel_count = 0
        self._centered_on_first_show = False

        icon_path = Path(__file__).resolve().parent / "assets" / "das_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self.setWindowTitle(f"DAS Recorder (Dunay) v{self.app_version}")
        self.resize(1680, 900)
        self.setStyleSheet(APP_QSS)

        self._build_actions()
        self._build_menubar()
        self._build_ui()
        self._apply_config_to_ui()
        self._update_disk_status()
        self._set_connection_default("Ready: Start will listen on UDP 8227")

        self.connection_watchdog = QtCore.QTimer(self)
        self.connection_watchdog.timeout.connect(self._check_connection_timeout)
        self.connection_watchdog.start(1000)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if self._centered_on_first_show:
            return
        self._centered_on_first_show = True
        QtCore.QTimer.singleShot(0, self._center_on_display)

    def _center_on_display(self) -> None:
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        self.resize(
            min(self.width(), available.width()),
            min(self.height(), available.height()),
        )
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _build_actions(self) -> None:
        """Create menu/toolbar actions shared by buttons and menu items."""
        self.start_action = QtWidgets.QAction("Start Recording", self)
        self.start_action.setShortcut("Ctrl+R")
        self.start_action.setStatusTip("Start DAS recording")
        self.start_action.triggered.connect(self.start_recording)

        self.stop_action = QtWidgets.QAction("Stop Recording", self)
        self.stop_action.setShortcut("Ctrl+Shift+R")
        self.stop_action.setStatusTip("Stop DAS recording")
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stop_recording)

        self.open_output_action = QtWidgets.QAction("Open Output Folder", self)
        self.open_output_action.setShortcut("Ctrl+O")
        self.open_output_action.setStatusTip("Open the selected output folder")
        self.open_output_action.triggered.connect(self._open_output_dir)

        self.save_defaults_action = QtWidgets.QAction(
            "Save Current Values as Defaults",
            self,
        )
        self.save_defaults_action.setShortcut("Ctrl+S")
        self.save_defaults_action.setStatusTip(
            "Use the current main-window values the next time DASRecorder starts"
        )
        self.save_defaults_action.setEnabled(self.config_path is not None)
        self.save_defaults_action.triggered.connect(self._save_current_defaults)

        self.exit_action = QtWidgets.QAction("Exit", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)

        self.about_action = QtWidgets.QAction("About", self)
        self.about_action.triggered.connect(self._show_about)

        self.firewall_action = QtWidgets.QAction("Configure Windows Firewall", self)
        self.firewall_action.setStatusTip("Allow Dunay UDP ports 8201, 8211, and 8227")
        self.firewall_action.triggered.connect(self._configure_firewall)

        self.request_stream_action = QtWidgets.QAction("Request Dunay Stream on Connect", self)
        self.request_stream_action.setCheckable(True)
        self.request_stream_action.setStatusTip(
            "Send the Dunay initialization/setup sequence after opening the receiver"
        )

    def _build_menubar(self) -> None:
        """Build a simple menu bar with the same Start/Stop controls as the top band."""
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.open_output_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        recording_menu = self.menuBar().addMenu("Recording")
        recording_menu.addAction(self.start_action)
        recording_menu.addAction(self.stop_action)

        settings_menu = self.menuBar().addMenu("Settings")
        settings_menu.addAction(self.save_defaults_action)

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction(self.firewall_action)
        tools_menu.addAction(self.request_stream_action)

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self.about_action)

    def _set_recording_controls(self, recording: bool, stopping: bool = False) -> None:
        """Keep top-band buttons and menu-bar Start/Stop actions synchronized."""
        receiver_active = self.worker is not None
        start_enabled = not recording and not stopping and not self.connecting
        stop_enabled = recording and not stopping
        busy = stopping or self.disconnecting
        # Once the receiver exists, the connection button must remain available so
        # a packet wait can be cancelled. Its label reflects packet state below.
        connect_enabled = (receiver_active and not busy) or (
            not receiver_active and not recording and not self.connecting and not busy
        )

        if hasattr(self, "start_action"):
            self.start_action.setEnabled(start_enabled)
        if hasattr(self, "stop_action"):
            self.stop_action.setEnabled(stop_enabled)
        if hasattr(self, "start_btn"):
            self.start_btn.setEnabled(start_enabled)
        if hasattr(self, "stop_btn"):
            self.stop_btn.setEnabled(stop_enabled)
        if hasattr(self, "connect_btn"):
            self.connect_btn.setEnabled(connect_enabled)

        geometry_editable = not recording and not self.connecting and not busy
        if hasattr(self, "origin_offset"):
            self.origin_offset.setEnabled(geometry_editable)
        if hasattr(self, "probing_length"):
            self.probing_length.setEnabled(geometry_editable)
        if hasattr(self, "channel_spacing"):
            self.channel_spacing.setEnabled(geometry_editable)
            self.channel_spacing.setReadOnly(
                not geometry_editable or self._clock_spacing_enabled()
            )
        if hasattr(self, "clock_spacing_checkbox"):
            self.clock_spacing_checkbox.setEnabled(geometry_editable)
        if hasattr(self, "index_of_refraction"):
            self.index_of_refraction.setEnabled(geometry_editable)
        timing_editable = not receiver_active and not recording and not self.connecting and not busy
        if hasattr(self, "freq"):
            self.freq.setEnabled(timing_editable)
        if hasattr(self, "duration"):
            self.duration.setEnabled(timing_editable)
        range_editable = not recording and not stopping and not busy
        if hasattr(self, "range_min_spin"):
            self.range_min_spin.setEnabled(range_editable)
        if hasattr(self, "range_max_spin"):
            self.range_max_spin.setEnabled(range_editable)
        if hasattr(self, "distance_range_slider"):
            self.distance_range_slider.setEnabled(range_editable)

    def _is_network_source(self) -> bool:
        if hasattr(self, "source_combo"):
            return self.source_combo.currentText() == "dunay_network"
        return getattr(self.config, "source", "") == "dunay_network"

    def _show_about(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "About DASRecorder",
            f"DASRecorder v{self.app_version}\n\nDeveloped by H4Tech\n\n"
            "Listen-first Dunay UDP recording, live waterfall display, and H5/TDMS saving."
        )

    def _configure_firewall(self) -> None:
        reply = QtWidgets.QMessageBox.question(
            self,
            "Configure Windows Firewall",
            "This will add Windows Firewall rules for Dunay UDP ports 8201, 8211, and 8227.\n\n"
            "Administrator permission may be required. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        ok, message = configure_dunay_firewall_rules(elevate=True)
        if ok:
            QtWidgets.QMessageBox.information(self, "Windows Firewall", message)
        else:
            QtWidgets.QMessageBox.warning(self, "Windows Firewall", message)
        self.statusBar().showMessage(message.splitlines()[0] if message else "Firewall setup finished")

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_band())
        root.addWidget(self._range_band())

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._left_panel())
        body.addWidget(self._visual_panel(), 1)

        root.addLayout(body, 1)
        self.setCentralWidget(central)
        self._show_connection_footer()

        self.developer_status_label = QtWidgets.QLabel("Developed by H4Tech")
        self.developer_status_label.setStyleSheet("color: #6b7280; padding-right: 8px;")
        self.statusBar().addPermanentWidget(self.developer_status_label)

    # ------------------------------------------------------------------
    # Connection button status
    # ------------------------------------------------------------------
    def _show_connection_footer(
        self,
        *,
        data_received: bool = False,
        connection_failed: bool = False,
    ) -> None:
        if (
            not self.device_initialized
            and not data_received
            and not connection_failed
            and self.worker is None
        ):
            self.statusBar().showMessage("Ready to initialize")
            return

        device_text = (
            "Device initialized" if self.device_initialized else "Device not initialized"
        )
        if data_received:
            message = f"{device_text} | Data received; Port: 8227"
        elif connection_failed:
            message = f"{device_text} | Connection failed"
        else:
            message = device_text
        if self.phase_received_count > 0:
            message += f" | Phase received: {self.phase_received_count:,}"
        self.statusBar().showMessage(message)

    def _set_connection_default(self, message: str = "Device unreachable") -> None:
        """Show the idle Connect state."""
        self.connection_ok = False
        self.connecting = False
        self.device_initialized = False
        self.phase_received_count = 0
        self.connect_btn.setText("🔵  Connect")
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #eef3f8;
                color: #1f2937;
                border: 1px solid #b7c3d0;
                border-radius: 4px;
                font-weight: 500;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #e2edf7;
            }
        """)
        self.connect_btn.setToolTip(message)

        self._show_connection_footer()
        self._set_recording_controls(recording=self.saving_active)

    def _set_connection_connecting(self, message: str = "Connecting to device...") -> None:
        self.connection_ok = False
        self.connecting = True
        self.connect_btn.setText("🟡  Connecting")
        self.connect_btn.setToolTip(message)
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #fff7db;
                color: #8a5a00;
                border: 1px solid #d39b12;
                border-radius: 4px;
                font-weight: 600;
                padding: 6px 14px;
            }
        """)

        self._show_connection_footer()
        self._set_recording_controls(recording=self.saving_active)

    def _set_connection_ok(self, message: str = "Device connected - packets receiving") -> None:
        """Keep Disconnect as the button action when packets are receiving."""
        self.connection_ok = True
        self.connecting = False
        self.device_initialized = True
        self.connect_btn.setText("🔴  Disconnect")
        self.connect_btn.setToolTip(message)
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #fdecec;
                color: #b42318;
                border: 1px solid #e5484d;
                border-radius: 4px;
                font-weight: 700;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #fbdada;
            }
        """)

        self._show_connection_footer(data_received=True)
        self._set_recording_controls(recording=self.saving_active)

    def _check_connection_timeout(self) -> None:
        """Keep connection state synchronized with packet activity."""
        if self.worker is None:
            return

        if self.last_packet_time is None:
            self._set_connection_connecting("Waiting for packets...")
            return

        elapsed = time.time() - self.last_packet_time
        if elapsed > self.packet_timeout_s:
            self._set_connection_connecting("Packet stream interrupted; waiting for packets...")
            self._show_connection_footer(connection_failed=True)

    def _on_packet_received(self, data_block) -> None:
        """Update connection state and forward live data to the waterfall display."""
        self.last_packet_time = time.time()
        if not self.connection_ok:
            self._set_connection_ok("Device connected - packets receiving")
        else:
            self._show_connection_footer(data_received=True)

        self.waterfall.append_block(data_block)

    def _on_channel_counts(self, received_count: int, active_count: int) -> None:
        self.receiving_channels_value.setText(f"{int(received_count):,}")
        self.active_channels_value.setText(f"{int(active_count):,}")

    def _on_channel_geometry(self, channel_count: int, channel_spacing_m: float) -> None:
        channel_count = max(1, int(channel_count))
        channel_spacing_m = max(0.000001, float(channel_spacing_m))
        self.last_received_channel_count = channel_count
        self.config.n_channels = channel_count
        self.config.channel_spacing_m = channel_spacing_m

        self.channel_spacing.blockSignals(True)
        self.channel_spacing.setValue(channel_spacing_m)
        self.channel_spacing.blockSignals(False)
        self._update_calculated_channel_display()

        if self.waterfall.n_channels != channel_count:
            self.waterfall.reset(
                phase_output_sample_rate_hz(self.freq.value()),
                channel_count,
                self.config.waterfall_window_s,
                self.config.display_columns,
                self.config.max_display_channels,
                distance_min_m=float(self.range_min_spin.value()),
                distance_max_m=float(self.range_max_spin.value()),
            )
            self._update_waterfall_levels()

    def _on_phase_received(self, phase_count: int) -> None:
        self.phase_received_count = max(0, int(phase_count))
        self._show_connection_footer(data_received=self.connection_ok)

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------
    def _top_band(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setObjectName("TopBand")
        layout = QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(16)

        self.connect_btn = QtWidgets.QPushButton("🔵  Connect")
        self.connect_btn.setObjectName("ConnectButton")
        self.connect_btn.setMinimumWidth(125)
        self.connect_btn.setFixedHeight(42)
        self.connect_btn.clicked.connect(self.connect_device)
        layout.addWidget(self.connect_btn)

        line1 = QtWidgets.QFrame()
        line1.setFrameShape(QtWidgets.QFrame.VLine)
        layout.addWidget(line1)

        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setObjectName("StartButton")
        self.start_btn.setToolTip("Start recording")

        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.setToolTip("Stop recording")
        self.stop_btn.setEnabled(False)

        self.start_btn.clicked.connect(self.start_action.trigger)
        self.stop_btn.clicked.connect(self.stop_action.trigger)

        layout.addWidget(self.start_btn)
        layout.addWidget(self.stop_btn)

        line2 = QtWidgets.QFrame()
        line2.setFrameShape(QtWidgets.QFrame.VLine)
        layout.addWidget(line2)

        ip_form = QtWidgets.QFormLayout()
        ip_form.setHorizontalSpacing(8)
        self.device_ip = QtWidgets.QLineEdit()
        self.local_ip = QtWidgets.QLineEdit()
        self.device_ip.setFixedWidth(140)
        self.local_ip.setFixedWidth(140)
        ip_form.addRow("Device IP", self.device_ip)
        ip_form.addRow("Local IP", self.local_ip)
        layout.addLayout(ip_form)

        line3 = QtWidgets.QFrame()
        line3.setFrameShape(QtWidgets.QFrame.VLine)
        layout.addWidget(line3)

        prop_layout = QtWidgets.QVBoxLayout()
        self.file_size_label = QtWidgets.QLabel("▣  Size of array/file: 0.00 MB / 0.00 MB")
        self.disk_label = QtWidgets.QLabel("▣  Free disk space: checking...")
        prop_layout.addWidget(self.file_size_label)
        prop_layout.addWidget(self.disk_label)
        layout.addLayout(prop_layout)
        layout.addStretch(1)
        return frame

    def _range_band(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setObjectName("TopBand")
        frame.setMinimumHeight(72)
        outer_layout = QtWidgets.QVBoxLayout(frame)
        outer_layout.setContentsMargins(320, 3, 24, 3)
        outer_layout.setSpacing(1)

        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        initial_start, initial_end = self._distance_bounds_from_values(
            getattr(self.config, "origin_offset_m", 0.0),
            getattr(self.config, "probing_length_m", 20000.0),
        )
        initial_min, initial_max = self._normalized_distance_values(
            getattr(self.config, "distance_min_m", initial_start),
            getattr(self.config, "distance_max_m", initial_end),
            initial_start,
            initial_end,
        )

        self.range_min_spin = QtWidgets.QSpinBox()
        self.range_min_spin.setRange(initial_start, initial_end - 1)
        self.range_min_spin.setSuffix(" m")
        self.range_min_spin.setSingleStep(10)
        self.range_min_spin.setValue(initial_min)
        self.range_min_spin.setFixedWidth(110)

        self.range_max_spin = QtWidgets.QSpinBox()
        self.range_max_spin.setRange(initial_start + 1, initial_end)
        self.range_max_spin.setSuffix(" m")
        self.range_max_spin.setSingleStep(10)
        self.range_max_spin.setValue(initial_max)
        self.range_max_spin.setFixedWidth(110)

        self.range_status = QtWidgets.QLabel()
        self.range_status.setMinimumWidth(310)

        controls_layout.addWidget(QtWidgets.QLabel("Save distance"))
        controls_layout.addWidget(QtWidgets.QLabel("Min"))
        controls_layout.addWidget(self.range_min_spin)
        controls_layout.addWidget(QtWidgets.QLabel("Max"))
        controls_layout.addWidget(self.range_max_spin)
        controls_layout.addWidget(self.range_status)
        controls_layout.addStretch(1)
        outer_layout.addLayout(controls_layout)

        slider_layout = QtWidgets.QHBoxLayout()
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(8)

        self.range_left = QtWidgets.QLabel(f"{initial_min} m")
        self.range_left.setMinimumWidth(70)
        self.range_left.setAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        slider_layout.addWidget(self.range_left)

        self.distance_range_slider = DistanceRangeSlider()
        self.distance_range_slider.setRange(initial_start, initial_end)
        self.distance_range_slider.setValues(
            initial_min,
            initial_max,
            emit=False,
        )
        slider_layout.addWidget(self.distance_range_slider, 1)

        self.range_right = QtWidgets.QLabel(f"{initial_max} m")
        self.range_right.setMinimumWidth(70)
        slider_layout.addWidget(self.range_right)
        outer_layout.addLayout(slider_layout)

        self.range_min_spin.valueChanged.connect(self._on_distance_range_changed)
        self.range_max_spin.valueChanged.connect(self._on_distance_range_changed)
        self.distance_range_slider.rangeChanged.connect(
            self._on_distance_slider_changed
        )
        self._on_distance_range_changed()
        return frame

    @staticmethod
    def _distance_bounds_from_values(
        origin_offset_m: float,
        probing_length_m: float,
    ) -> tuple[int, int]:
        start = int(math.ceil(float(origin_offset_m) - 1e-9))
        end = int(math.floor(float(probing_length_m) + 1e-9))
        return start, max(start + 1, end)

    @staticmethod
    def _normalized_distance_values(
        low: float,
        high: float,
        region_start: int,
        region_end: int,
    ) -> tuple[int, int]:
        low = int(round(float(low)))
        high = int(round(float(high)))

        low = max(region_start, min(low, region_end - 1))
        high = max(region_start + 1, min(high, region_end))
        if low >= high:
            low = max(region_start, high - 1)
        return low, high

    def _current_distance_bounds_m(self) -> tuple[int, int]:
        if hasattr(self, "origin_offset"):
            origin = self.origin_offset.value()
        else:
            origin = getattr(self.config, "origin_offset_m", 0.0)
        if hasattr(self, "probing_length"):
            length = self.probing_length.value()
        else:
            length = getattr(self.config, "probing_length_m", 20000.0)
        return self._distance_bounds_from_values(origin, length)

    def _calculated_channel_count_from_ui(self) -> int:
        spacing = self._effective_channel_spacing_from_ui()
        probing_limit = (
            float(self.probing_length.value())
            if hasattr(self, "probing_length")
            else float(getattr(self.config, "probing_length_m", 0.0))
        )
        origin = (
            float(self.origin_offset.value())
            if hasattr(self, "origin_offset")
            else float(getattr(self.config, "origin_offset_m", 0.0))
        )
        if spacing <= 0:
            return max(1, int(getattr(self.config, "n_channels", 1)))
        span_m = max(0.0, probing_limit - origin)
        return max(1, int((span_m / spacing) + 1e-9) + 1)

    def _clock_spacing_enabled(self) -> bool:
        if hasattr(self, "clock_spacing_checkbox"):
            return bool(self.clock_spacing_checkbox.isChecked())
        return bool(getattr(self.config, "auto_channel_spacing", True))

    def _effective_channel_spacing_from_ui(self) -> float:
        if self._clock_spacing_enabled():
            clock_period_ns = (
                float(self.clock_period.value())
                if hasattr(self, "clock_period")
                else float(getattr(self.config, "clock_period_ns", 10.0))
            )
            index = (
                float(self.index_of_refraction.value())
                if hasattr(self, "index_of_refraction")
                else float(getattr(self.config, "index_of_refraction", 1.4680))
            )
            return clock_channel_spacing_m(clock_period_ns, index)
        if hasattr(self, "channel_spacing"):
            return float(self.channel_spacing.value())
        return float(getattr(self.config, "channel_spacing_m", 0.0))

    def _sync_clock_spacing_to_ui(self) -> None:
        if not hasattr(self, "channel_spacing") or not self._clock_spacing_enabled():
            return
        spacing = self._effective_channel_spacing_from_ui()
        self.channel_spacing.blockSignals(True)
        self.channel_spacing.setValue(spacing)
        self.channel_spacing.blockSignals(False)

    def _update_calculated_channel_display(self) -> None:
        if not hasattr(self, "channels_value"):
            return
        count = self._calculated_channel_count_from_ui()
        spacing = self._effective_channel_spacing_from_ui()
        origin = float(self.origin_offset.value()) if hasattr(self, "origin_offset") else 0.0
        last_distance = origin + max(0, count - 1) * spacing
        self.channels_value.setText(f"{count:,}")
        self.channels_value.setToolTip(
            f"Exact spacing: {spacing:.9f} m; "
            f"last channel distance: {last_distance:.3f} m"
        )

    def _sync_distance_controls_to_probing_length(self, keep_user_selection: bool = True) -> None:
        """Clamp direct inputs and slider to offset..probing limit."""
        region_start, region_end = self._current_distance_bounds_m()
        old_minimum = self.range_min_spin.minimum()
        old_maximum = self.range_max_spin.maximum()
        old_min = int(self.range_min_spin.value())
        old_max = int(self.range_max_spin.value())

        selected_range_was_full = (
            old_min <= old_minimum and old_max >= old_maximum
        )

        if keep_user_selection and not selected_range_was_full:
            new_min, new_max = self._normalized_distance_values(
                old_min,
                old_max,
                region_start,
                region_end,
            )
        else:
            new_min = region_start
            new_max = region_end

        self.range_min_spin.blockSignals(True)
        self.range_max_spin.blockSignals(True)
        self.range_min_spin.setRange(region_start, region_end - 1)
        self.range_max_spin.setRange(region_start + 1, region_end)
        self.range_min_spin.setValue(new_min)
        self.range_max_spin.setValue(new_max)
        self.range_min_spin.blockSignals(False)
        self.range_max_spin.blockSignals(False)
        self.distance_range_slider.setRange(region_start, region_end)
        self.distance_range_slider.setValues(
            new_min,
            new_max,
            emit=False,
        )

        self._on_distance_range_changed()

    def _on_probing_length_changed(self) -> None:
        """When probing length changes, update distance limits and waterfall X-axis."""
        if self.probing_length.value() <= self.origin_offset.value():
            self.origin_offset.blockSignals(True)
            self.origin_offset.setValue(self.probing_length.value() - 1.0)
            self.origin_offset.blockSignals(False)
        self._sync_distance_controls_to_probing_length(keep_user_selection=True)
        self._update_calculated_channel_display()
        self._apply_live_geometry()

    def _on_channel_spacing_changed(self) -> None:
        self._update_calculated_channel_display()
        self._apply_live_geometry()

    def _on_clock_spacing_toggled(self, checked: bool) -> None:
        self.channel_spacing.setReadOnly(bool(checked))
        self.channel_spacing.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.NoButtons
            if checked
            else QtWidgets.QAbstractSpinBox.UpDownArrows
        )
        if checked:
            self._sync_clock_spacing_to_ui()
        self._update_calculated_channel_display()
        self._apply_live_geometry()

    def _on_index_of_refraction_changed(self) -> None:
        if self._clock_spacing_enabled():
            self._sync_clock_spacing_to_ui()
            self._update_calculated_channel_display()
            self._apply_live_geometry()

    def _on_origin_offset_changed(self) -> None:
        if self.origin_offset.value() >= self.probing_length.value():
            self.probing_length.blockSignals(True)
            self.probing_length.setValue(self.origin_offset.value() + 1.0)
            self.probing_length.blockSignals(False)
        self._sync_distance_controls_to_probing_length(
            keep_user_selection=True
        )
        self._update_calculated_channel_display()
        self._apply_live_geometry()

    def _apply_live_geometry(self) -> None:
        """Apply editable acquisition geometry while connected but not recording."""
        if (
            self.worker is None
            or self.saving_active
            or self.connecting
            or self.disconnecting
        ):
            return

        updated = self._read_config_from_ui()
        self.config = updated
        self.worker.apply_geometry_settings(updated)
        self._update_calculated_channel_display()
        self.waterfall.reset(
            updated.phase_sample_rate_hz(),
            updated.selected_n_channels(),
            updated.waterfall_window_s,
            updated.display_columns,
            updated.max_display_channels,
            distance_min_m=updated.distance_min_m,
            distance_max_m=updated.distance_max_m,
        )
        self._update_waterfall_levels()
        self._show_connection_footer(data_received=self.connection_ok)

    def _on_distance_range_changed(self) -> None:
        """Keep the selected distance range valid and update labels/waterfall axis."""
        region_start, region_end = self._current_distance_bounds_m()

        min_m = int(self.range_min_spin.value())
        max_m = int(self.range_max_spin.value())

        min_m = max(region_start, min(min_m, region_end - 1))
        max_m = max(region_start + 1, min(max_m, region_end))

        # Prevent invalid range. Keep at least 1 m difference.
        sender = self.sender()
        if min_m >= max_m:
            if sender is self.range_min_spin:
                max_m = min(region_end, min_m + 1)
            else:
                min_m = max(region_start, max_m - 1)

        # Write corrected values back without recursive signals.
        self.range_min_spin.blockSignals(True)
        self.range_max_spin.blockSignals(True)
        self.range_min_spin.setValue(min_m)
        self.range_max_spin.setValue(max_m)
        self.range_min_spin.blockSignals(False)
        self.range_max_spin.blockSignals(False)
        self.distance_range_slider.setRange(region_start, region_end)
        self.distance_range_slider.setValues(
            min_m,
            max_m,
            emit=False,
        )

        self.range_left.setText(f"{min_m} m")
        self.range_right.setText(f"{max_m} m")
        self.range_status.setText(
            f"Saving: {min_m} m - {max_m} m  |  "
            f"Limit: {region_start}-{region_end} m"
        )

        # Update the waterfall X-axis immediately if the visual panel is already built.
        if hasattr(self, "waterfall"):
            self.waterfall.set_distance_range(float(min_m), float(max_m))

    def _on_distance_slider_changed(self, min_m: int, max_m: int) -> None:
        self.range_min_spin.blockSignals(True)
        self.range_max_spin.blockSignals(True)
        self.range_min_spin.setValue(int(min_m))
        self.range_max_spin.setValue(int(max_m))
        self.range_min_spin.blockSignals(False)
        self.range_max_spin.blockSignals(False)
        self._on_distance_range_changed()

    def _left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("LeftPanel")
        panel.setFixedWidth(340)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ------------------------------------------------------------
        # Mode section
        # ------------------------------------------------------------
        mode = Section("Mode")
        self.rb_unprocessed = QtWidgets.QRadioButton("Unprocessed data")
        self.rb_phase = QtWidgets.QRadioButton('Restored "phase"')
        mode.body_layout.addRow(self.rb_unprocessed)
        mode.body_layout.addRow(self.rb_phase)
        layout.addWidget(mode)

        # ------------------------------------------------------------
        # File section
        # File path, dataset name, data source, save format, file duration, comment
        # ------------------------------------------------------------
        file_sec = Section("File")

        self.output_dir = QtWidgets.QLineEdit()
        self.output_dir.setReadOnly(True)
        self.output_dir.setPlaceholderText("Select storage folder...")
        self.output_dir.setToolTip("Full storage path will appear here")
        self.output_dir.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.output_browse = QtWidgets.QPushButton("...")
        self.output_browse.setFixedWidth(32)

        self.open_output_btn = QtWidgets.QPushButton("Open")
        self.open_output_btn.setFixedWidth(48)

        output_row_widget = QtWidgets.QWidget()
        output_row = QtWidgets.QHBoxLayout(output_row_widget)
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(4)
        output_row.addWidget(self.output_dir, 1)
        output_row.addWidget(self.output_browse)
        output_row.addWidget(self.open_output_btn)

        self.output_browse.clicked.connect(self._choose_output_dir)
        self.open_output_btn.clicked.connect(self._open_output_dir)

        self.dataset_name = QtWidgets.QLineEdit()

        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.addItem("dunay_network")
        self.source_combo.currentTextChanged.connect(self._on_source_changed)

        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["h5", "tdms", "both"])

        self.segment_duration = QtWidgets.QSpinBox()
        self.segment_duration.setRange(1, 3600)
        self.segment_duration.setSuffix(" s")
        self.segment_duration.setToolTip("Recorded file duration for each segment. Example: 60 s = one file per minute.")

        self.comment = QtWidgets.QTextEdit()
        self.comment.setFixedHeight(78)

        file_sec.body_layout.addRow("Path to the file", output_row_widget)
        file_sec.body_layout.addRow("Dataset name", self.dataset_name)
        file_sec.body_layout.addRow("Data source", self.source_combo)
        file_sec.body_layout.addRow("Save format", self.format_combo)
        file_sec.body_layout.addRow("File duration", self.segment_duration)
        file_sec.body_layout.addRow("Comment", self.comment)
        layout.addWidget(file_sec)

        # ------------------------------------------------------------
        # Impulse parameters section
        # ------------------------------------------------------------
        imp = Section("Impulse parameters")
        self.freq = QtWidgets.QDoubleSpinBox()
        self.freq.setRange(1, 100000)
        self.freq.setDecimals(1)
        self.freq.setSuffix(" Hz")
        self.duration = QtWidgets.QSpinBox()
        self.duration.setRange(1, 1000000)
        self.duration.setSuffix(" ns")
        imp.body_layout.addRow("Frequency", self.freq)
        imp.body_layout.addRow("Duration", self.duration)
        layout.addWidget(imp)

        # ------------------------------------------------------------
        # Data acquisition region section
        # ------------------------------------------------------------
        acq = Section("Data acquisition region")
        self.origin_offset = QtWidgets.QDoubleSpinBox()
        self.origin_offset.setRange(-1000000, 1000000)
        self.origin_offset.setSuffix(" m")
        self.origin_offset.setToolTip(
            "Editable before or after connecting. Locked while recording."
        )
        self.origin_offset.valueChanged.connect(self._on_origin_offset_changed)
        self.probing_length = QtWidgets.QDoubleSpinBox()
        self.probing_length.setRange(1, 1000000)
        self.probing_length.setSuffix(" m")
        self.probing_length.setToolTip(
            "Editable before or after connecting. Locked while recording."
        )
        self.probing_length.valueChanged.connect(self._on_probing_length_changed)
        self.channel_spacing = QtWidgets.QDoubleSpinBox()
        self.channel_spacing.setRange(0.001, 1000000.0)
        self.channel_spacing.setDecimals(2)
        self.channel_spacing.setSingleStep(0.01)
        self.channel_spacing.setSuffix(" m")
        self.channel_spacing.setToolTip(
            "Physical distance between device line points. The default is "
            "calculated from the 10 ns device clock and refractive index."
        )
        self.channel_spacing.valueChanged.connect(self._on_channel_spacing_changed)
        self.clock_spacing_checkbox = QtWidgets.QCheckBox(
            "Calculate spacing"
        )
        self.clock_spacing_checkbox.setToolTip(
            "Calculate exact channel spacing from the device clock pulse and "
            "fiber index. Clear this to enter a manual spacing."
        )
        self.clock_spacing_checkbox.toggled.connect(self._on_clock_spacing_toggled)

        self.channels_value = QtWidgets.QLabel("0")
        self.channels_value.setObjectName("SmallMuted")
        self.channels_value.setToolTip(
            "Distance-grid count: floor(probing length / channel spacing) + 1"
        )
        self.receiving_channels_value = QtWidgets.QLabel("Not connected")
        self.receiving_channels_value.setObjectName("SmallMuted")
        self.receiving_channels_value.setToolTip(
            "Unique device line points in the latest complete packet block; "
            "independent of the configured probing length"
        )
        self.active_channels_value = QtWidgets.QLabel("Not connected")
        self.active_channels_value.setObjectName("SmallMuted")
        self.active_channels_value.setToolTip(
            "Received rows containing data other than the Dunay no-data marker"
        )

        acq.body_layout.addRow("Origin offset", self.origin_offset)
        acq.body_layout.addRow("Probing length", self.probing_length)
        acq.body_layout.addRow("Channel spacing", self.channel_spacing)
        acq.body_layout.addRow(self.clock_spacing_checkbox)
        acq.body_layout.addRow("Calculated channels", self.channels_value)
        acq.body_layout.addRow("Received channels", self.receiving_channels_value)
        acq.body_layout.addRow("Active channels", self.active_channels_value)
        layout.addWidget(acq)

        # ------------------------------------------------------------
        # Fiber properties section
        # Chunk samples is intentionally hidden from the main window.
        # It remains in config/default_config.json as an internal buffer setting.
        # ------------------------------------------------------------
        fiber = Section("Fiber properties")
        self.index_of_refraction = QtWidgets.QDoubleSpinBox()
        self.index_of_refraction.setRange(1.0000, 2.0000)
        self.index_of_refraction.setDecimals(4)
        self.index_of_refraction.setSingleStep(0.0001)
        self.index_of_refraction.setValue(1.4680)
        self.index_of_refraction.setToolTip("Fiber index of refraction. Typical optical fiber value is about 1.4680.")
        self.index_of_refraction.valueChanged.connect(
            self._on_index_of_refraction_changed
        )

        self.clock_period = QtWidgets.QDoubleSpinBox()
        self.clock_period.setRange(0.01, 1000.0)
        self.clock_period.setDecimals(2)
        self.clock_period.setSuffix(" ns")
        self.clock_period.setValue(10.0)
        self.clock_period.setReadOnly(True)
        self.clock_period.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.clock_period.setToolTip("Dunay hardware clock pulse period")

        fiber.body_layout.addRow("Index of refraction", self.index_of_refraction)
        fiber.body_layout.addRow("Clock pulse", self.clock_period)
        layout.addWidget(fiber)

        # ------------------------------------------------------------
        # Noise reduction section
        # Only noise processing controls are kept here.
        # ------------------------------------------------------------
        noise = Section("Noise reduction")

        self.noise_replace_checkbox = QtWidgets.QCheckBox("Replace noise by zeros")
        noise.body_layout.addRow(self.noise_replace_checkbox)

        self.noise_suppression_label = QtWidgets.QLabel("Noise suppression factor: 0%")
        noise.body_layout.addRow(self.noise_suppression_label)

        self.noise_suppression_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.noise_suppression_slider.setRange(0, 100)
        self.noise_suppression_slider.setValue(0)
        self.noise_suppression_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.noise_suppression_slider.setTickInterval(25)
        # The reference UI shows High on the left and Low on the right.
        self.noise_suppression_slider.setInvertedAppearance(True)
        self.noise_suppression_slider.setToolTip(
            "0 = no noise suppression, 100 = strongest noise suppression"
        )
        self.noise_suppression_slider.valueChanged.connect(self._on_noise_slider_changed)
        noise.body_layout.addRow(self.noise_suppression_slider)

        noise_scale = QtWidgets.QWidget()
        noise_scale_layout = QtWidgets.QHBoxLayout(noise_scale)
        noise_scale_layout.setContentsMargins(0, 0, 0, 0)
        noise_scale_layout.addWidget(QtWidgets.QLabel("High"))
        noise_scale_layout.addStretch(1)
        noise_scale_layout.addWidget(QtWidgets.QLabel("Low"))
        noise.body_layout.addRow(noise_scale)

        layout.addWidget(noise)
        layout.addStretch(1)
        return panel

    def _visual_panel(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(8, 4, 8, 0)
        layout.setSpacing(6)

        scale_panel = RgbControlPanel()
        scale_layout = QtWidgets.QVBoxLayout(scale_panel)
        scale_layout.setContentsMargins(4, 6, 4, 8)
        scale_layout.setSpacing(6)

        darker_label = QtWidgets.QLabel("Darker")
        darker_label.setAlignment(QtCore.Qt.AlignCenter)
        darker_label.setFixedHeight(22)
        darker_label.setStyleSheet("color: #3d5c82; font-weight: 500;")
        scale_layout.addWidget(darker_label)

        sliders = QtWidgets.QHBoxLayout()
        sliders.setContentsMargins(5, 0, 5, 0)
        sliders.setSpacing(8)
        self.low_slider = self._vertical_slider(
            -5000,
            5000,
            int(getattr(self.config, "waterfall_low_position", 0)),
            QtGui.QColor(220, 0, 0),
        )
        self.high_slider = self._vertical_slider(
            -5000,
            5000,
            int(getattr(self.config, "waterfall_high_position", 2500)),
            QtGui.QColor(0, 210, 40),
        )
        self.gamma_slider = self._vertical_slider(
            1,
            100,
            int(getattr(self.config, "waterfall_gamma_position", 50)),
            QtGui.QColor(30, 60, 220),
        )
        sliders.addWidget(self.low_slider)
        sliders.addWidget(self.high_slider)
        sliders.addWidget(self.gamma_slider)

        scale_layout.addLayout(sliders, 1)
        brighter_label = QtWidgets.QLabel("Brighter")
        brighter_label.setAlignment(QtCore.Qt.AlignCenter)
        brighter_label.setFixedHeight(22)
        brighter_label.setStyleSheet("color: #3d5c82; font-weight: 500;")
        scale_layout.addWidget(brighter_label)
        layout.addWidget(scale_panel)

        self.waterfall = WaterfallWidget(
            self.config.phase_sample_rate_hz(),
            self.config.calculated_n_channels(),
            self.config.waterfall_window_s,
            self.config.display_columns,
            self.config.max_display_channels,
            distance_min_m=getattr(self.config, "distance_min_m", 0.0),
            distance_max_m=getattr(self.config, "distance_max_m", getattr(self.config, "probing_length_m", 20000.0)),
        )
        layout.addWidget(self.waterfall, 1)
        self.low_slider.valueChanged.connect(self._update_waterfall_levels)
        self.high_slider.valueChanged.connect(self._update_waterfall_levels)
        return widget

    def _vertical_slider(self, low: int, high: int, value: int, color: QtGui.QColor) -> QtWidgets.QSlider:
        slider = RgbLevelSlider(color)
        slider.setMinimum(low)
        slider.setMaximum(high)
        slider.setValue(value)
        return slider

    # ------------------------------------------------------------------
    # Config / status helpers
    # ------------------------------------------------------------------
    def _set_output_path(self, path: str) -> None:
        """Show the full absolute output path in the left panel."""
        if not path:
            path = "recordings"

        try:
            full_path = str(Path(path).expanduser().resolve())
        except Exception:
            full_path = str(Path(path).expanduser())

        self.output_dir.setText(full_path)
        self.output_dir.setToolTip(full_path)
        self.output_dir.setCursorPosition(len(full_path))

    def _apply_config_to_ui(self) -> None:
        self.device_ip.setText(self.config.device_ip)
        self.local_ip.setText(self.config.local_ip)
        self._set_output_path(self.config.output_dir)
        self.dataset_name.setText(self.config.dataset_name)
        self.freq.setValue(self.config.sample_rate_hz)
        self.duration.setValue(self.config.impulse_duration_ns)
        self.origin_offset.blockSignals(True)
        self.origin_offset.setValue(self.config.origin_offset_m)
        self.origin_offset.blockSignals(False)
        self.index_of_refraction.blockSignals(True)
        self.index_of_refraction.setValue(
            float(getattr(self.config, "index_of_refraction", 1.4680))
        )
        self.index_of_refraction.blockSignals(False)
        self.clock_period.setValue(
            float(getattr(self.config, "clock_period_ns", 10.0))
        )
        self.clock_spacing_checkbox.blockSignals(True)
        self.clock_spacing_checkbox.setChecked(
            bool(getattr(self.config, "auto_channel_spacing", True))
        )
        self.clock_spacing_checkbox.blockSignals(False)

        # Block the signal here so we can apply saved min/max distance values first,
        # then clamp them once using the current probing length.
        self.probing_length.blockSignals(True)
        self.probing_length.setValue(self.config.probing_length_m)
        self.probing_length.blockSignals(False)
        self.channel_spacing.setValue(
            float(
                getattr(
                    self.config,
                    "channel_spacing_m",
                    self.config._legacy_channel_spacing(),
                )
            )
        )
        self._on_clock_spacing_toggled(self.clock_spacing_checkbox.isChecked())

        region_start, region_end = self._current_distance_bounds_m()
        selected_min, selected_max = self._normalized_distance_values(
            getattr(self.config, "distance_min_m", region_start),
            getattr(self.config, "distance_max_m", region_end),
            region_start,
            region_end,
        )
        self.range_min_spin.blockSignals(True)
        self.range_max_spin.blockSignals(True)
        self.range_min_spin.setRange(region_start, region_end - 1)
        self.range_max_spin.setRange(region_start + 1, region_end)
        self.range_min_spin.setValue(selected_min)
        self.range_max_spin.setValue(selected_max)
        self.range_min_spin.blockSignals(False)
        self.range_max_spin.blockSignals(False)
        self.distance_range_slider.setRange(region_start, region_end)
        self.distance_range_slider.setValues(
            selected_min,
            selected_max,
            emit=False,
        )
        self._on_distance_range_changed()
        self._update_calculated_channel_display()
        self.segment_duration.setValue(self.config.segment_duration_s)
        self.rb_phase.setChecked(True)
        self.noise_replace_checkbox.setChecked(bool(getattr(self.config, "noise_replace_by_zeros", False)))
        self.noise_suppression_slider.setValue(int(round(float(getattr(self.config, "noise_suppression_factor", 0.0)))))
        self._on_noise_slider_changed(self.noise_suppression_slider.value())
        self.low_slider.setValue(
            int(getattr(self.config, "waterfall_low_position", 0))
        )
        self.high_slider.setValue(
            int(getattr(self.config, "waterfall_high_position", 2500))
        )
        self.gamma_slider.setValue(
            int(getattr(self.config, "waterfall_gamma_position", 50))
        )
        self._update_waterfall_levels()
        self.request_stream_action.setChecked(bool(getattr(self.config, "device_control_enabled", False)))
        self.source_combo.setCurrentText(self.config.source)
        if set(self.config.formats) == {"h5", "tdms"}:
            self.format_combo.setCurrentText("both")
        else:
            self.format_combo.setCurrentText(self.config.formats[0])
        self._set_recording_controls(recording=False)

    def _read_config_from_ui(self) -> RecorderConfig:
        formats = [self.format_combo.currentText()]
        if formats[0] == "both":
            formats = ["h5", "tdms"]

        return RecorderConfig(
            app_version=self.app_version,
            source=self.source_combo.currentText(),
            output_dir=self.output_dir.text().strip() or "recordings",
            formats=formats,
            segment_duration_s=int(self.segment_duration.value()),
            sample_rate_hz=float(self.freq.value()),
            n_channels=self._calculated_channel_count_from_ui(),
            # Hidden from the main window, but still used internally for efficient streaming.
            chunk_samples=int(getattr(self.config, "chunk_samples", 500)),
            dataset_name=self.dataset_name.text().strip() or "DS",
            h5_template_path=self.config.h5_template_path,
            tdms_template_path=self.config.tdms_template_path,
            device_ip=self.device_ip.text().strip(),
            local_ip=self.local_ip.text().strip(),
            device_control_enabled=bool(self.request_stream_action.isChecked()),
            device_command_mode=str(getattr(self.config, "device_command_mode", "phase")),
            origin_offset_m=float(self.origin_offset.value()),
            probing_length_m=float(self.probing_length.value()),
            channel_spacing_m=self._effective_channel_spacing_from_ui(),
            auto_channel_spacing=self._clock_spacing_enabled(),
            clock_period_ns=float(self.clock_period.value()),
            index_of_refraction=float(self.index_of_refraction.value()),
            line_point_start=int(getattr(self.config, "line_point_start", 73)),
            distance_min_m=float(self.range_min_spin.value()),
            distance_max_m=float(self.range_max_spin.value()),
            noise_replace_by_zeros=bool(self.noise_replace_checkbox.isChecked()),
            noise_suppression_factor=float(self.noise_suppression_slider.value()),
            impulse_duration_ns=int(self.duration.value()),
            mode="Restored phase" if self.rb_phase.isChecked() else "Unprocessed data",
            waterfall_window_s=self.config.waterfall_window_s,
            max_display_channels=self.config.max_display_channels,
            display_columns=self.config.display_columns,
            waterfall_low_position=int(self.low_slider.value()),
            waterfall_high_position=int(self.high_slider.value()),
            waterfall_gamma_position=int(self.gamma_slider.value()),
        )

    def _persist_current_defaults(self, show_confirmation: bool = True) -> bool:
        if self.config_path is None:
            if show_confirmation:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Default settings",
                    "The default settings file is not available.",
                )
            return False

        try:
            saved_config = self._read_config_from_ui()
            saved_config.save(self.config_path)
        except Exception as exc:
            if show_confirmation:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Default settings",
                    f"Could not save default settings:\n{exc}",
                )
            return False

        self.config = saved_config
        message = f"Default settings saved: {self.config_path}"
        self.statusBar().showMessage(message, 5000)
        if show_confirmation:
            QtWidgets.QMessageBox.information(
                self,
                "Default settings",
                "Current values were saved as the startup defaults.",
            )
        return True

    def _save_current_defaults(self) -> None:
        self._persist_current_defaults(show_confirmation=True)

    def _choose_output_dir(self) -> None:
        start_path = self.output_dir.text().strip() or self.config.output_dir or "recordings"
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose output storage folder",
            start_path,
        )
        if path:
            self._set_output_path(path)
            self._update_disk_status()

    def _open_output_dir(self) -> None:
        path = Path(self.output_dir.text().strip() or "recordings").expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _update_disk_status(self) -> None:
        path = Path(self.output_dir.text() or ".")
        try:
            usage = shutil.disk_usage(path if path.exists() else path.parent)
            free_mb = usage.free / 1024 / 1024
            self.disk_label.setText(f"▣  Free disk space: {free_mb:,.2f} MB")
        except Exception:
            self.disk_label.setText("▣  Free disk space: unknown")

    def _update_waterfall_levels(self) -> None:
        self.waterfall.set_levels(self.low_slider.value(), self.high_slider.value())

    def _on_noise_slider_changed(self, value: int) -> None:
        value = int(value)
        if value <= 0:
            level_text = "Off"
        elif value < 35:
            level_text = "Low"
        elif value < 70:
            level_text = "Medium"
        else:
            level_text = "High"
        self.noise_suppression_label.setText(
            f"Noise suppression factor: {value}% ({level_text})"
        )

    def _on_source_changed(self) -> None:
        self.last_packet_time = None
        self._set_connection_default("Ready: Start will listen on UDP 8227")

    # ------------------------------------------------------------------
    # Recorder control
    # ------------------------------------------------------------------
    def connect_device(self) -> None:
        if self.worker is not None:
            self._disconnect_receiver()
            return

        self.config = self._read_config_from_ui()
        if self.config.source != "dunay_network":
            self._set_connection_error("Unsupported data source")
            return

        self._start_receiver(initial_saving=False)

    def start_recording(self) -> None:
        if self.worker is not None:
            self.config = self._read_config_from_ui()
            self.worker.apply_recording_settings(self.config)
            self.saving_active = True
            self.worker.begin_saving()
            self._set_recording_controls(recording=True)
            self._show_connection_footer(data_received=self.connection_ok)
            return

        self.config = self._read_config_from_ui()
        self._start_receiver(initial_saving=True)

    def _start_receiver(self, initial_saving: bool) -> None:
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)

        self.last_packet_time = None
        self.disconnecting = False
        self.saving_active = bool(initial_saving)
        self.receiving_channels_value.setText("Waiting...")
        self.active_channels_value.setText("Waiting...")
        self._set_connection_connecting("Opening UDP receiver...")

        if self.config.source == "dunay_network":
            if initial_saving:
                receiver_message = "Recording: listening on UDP 8227"
            else:
                receiver_message = "Listening on UDP 8227"
        else:
            receiver_message = "Waiting for data..."
        self._show_connection_footer()

        selected_channels = self.config.selected_n_channels()
        self.waterfall.reset(
            self.config.phase_sample_rate_hz(),
            selected_channels,
            self.config.waterfall_window_s,
            self.config.display_columns,
            self.config.max_display_channels,
            distance_min_m=self.config.distance_min_m,
            distance_max_m=self.config.distance_max_m,
        )
        self._update_waterfall_levels()

        self.thread = QtCore.QThread(self)
        self.worker = RecorderWorker(self.config, initial_saving=initial_saving)
        self.worker.moveToThread(self.thread)
        self._update_calculated_channel_display()

        self.thread.started.connect(self.worker.run)

        # IMPORTANT: do not connect block_ready directly to waterfall.append_block.
        # This wrapper updates the connection icon only when real packets arrive.
        self.worker.block_ready.connect(self._on_packet_received)

        self.worker.status.connect(self._on_worker_status)
        self.worker.error.connect(self._show_error)
        self.worker.channel_counts.connect(self._on_channel_counts)
        self.worker.channel_geometry.connect(self._on_channel_geometry)
        self.worker.phase_received.connect(self._on_phase_received)
        self.worker.finished.connect(self._recording_finished)
        self.worker.file_closed.connect(
            lambda _path: self._show_connection_footer(data_received=self.connection_ok)
        )
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()
        self._set_connection_connecting("Receiver open; requesting Dunay packet stream...")
        self._set_recording_controls(recording=initial_saving)

    def _on_worker_status(self, message: str) -> None:
        normalized = str(message).lower()
        if "vendor initialization" in normalized:
            self.device_initialized = True
        elif (
            "stream request failed" in normalized
            or "device rejected" in normalized
            or "connection failed" in normalized
        ):
            self.device_initialized = False
            self._show_connection_footer(connection_failed=True)
            return

        self._show_connection_footer(data_received=self.connection_ok)

    def stop_recording(self) -> None:
        if self.worker is not None and self.saving_active:
            self.saving_active = False
            self.worker.stop_saving()
            self._set_recording_controls(recording=False)
            self._show_connection_footer(data_received=self.connection_ok)
            return

        self._disconnect_receiver()

    def _disconnect_receiver(self) -> None:
        self.disconnecting = True
        if self.worker is not None:
            if self.saving_active:
                self.worker.stop_saving()
            self.worker.stop()
        if self.thread is not None:
            self.thread.quit()

        self.saving_active = False
        self._set_recording_controls(recording=False, stopping=True)
        self.last_packet_time = None
        self._set_connection_default("Disconnecting...")

    def _recording_finished(self) -> None:
        self.saving_active = False
        self.last_packet_time = None
        self.worker = None
        self.disconnecting = self.thread is not None and self.thread.isRunning()
        self._set_connection_default("Closing receiver..." if self.disconnecting else "Receiver stopped")
        self._update_disk_status()

    def _thread_finished(self) -> None:
        finished_thread = self.sender()
        if finished_thread is self.thread:
            self.thread = None
        self.disconnecting = False
        self.receiving_channels_value.setText("Not connected")
        self.active_channels_value.setText("Not connected")
        self._update_calculated_channel_display()
        self._set_connection_default("Receiver stopped")

    def _show_error(self, message: str) -> None:
        self.saving_active = False
        self._set_recording_controls(recording=False)
        self.last_packet_time = None
        self._set_connection_default("Connection failed / recorder error")
        QtWidgets.QMessageBox.critical(self, "Recorder error", message)
        self._show_connection_footer(connection_failed=True)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Stop the receiver thread before allowing the application to exit."""
        if self.worker is not None:
            if self.saving_active:
                self.worker.stop_saving()
            self.worker.stop()

        thread = self.thread
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(3000):
                self.statusBar().showMessage("Waiting for receiver thread to stop...")
                event.ignore()
                return

        self.worker = None
        self.thread = None
        event.accept()
