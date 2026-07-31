from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt5 import QtCore

from .config import RecorderConfig
from .data_source import DunayNetworkSource
from .noise_filter import apply_noise_reduction
from .writers import SegmentManager


class RecorderWorker(QtCore.QObject):
    block_ready = QtCore.pyqtSignal(object)       # numpy ndarray
    status = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    file_closed = QtCore.pyqtSignal(str)
    channel_counts = QtCore.pyqtSignal(int, int)
    channel_geometry = QtCore.pyqtSignal(int, float)
    phase_received = QtCore.pyqtSignal(int)

    def __init__(self, config: RecorderConfig, initial_saving: bool = True):
        super().__init__()
        self.config = config
        self._source = None
        self._segment_manager = None
        self._running = False
        self._stop_requested = False
        self._saving_enabled = bool(initial_saving)
        self._close_saving_requested = False
        self._diagnostics_summary_path = None
        self._start_ch = 0
        self._end_ch = 0
        self._selected_channels = 0
        self._reported_finished_files = 0

    def _ensure_segment_manager(self) -> None:
        if self._segment_manager is not None:
            return
        if self._selected_channels <= 0:
            raise ValueError("Invalid distance range: no channel selected")

        self._segment_manager = SegmentManager(
            output_dir=self.config.output_dir,
            formats=self.config.formats,
            segment_duration_s=self.config.segment_duration_s,
            n_channels=self._selected_channels,
            sample_rate_hz=self.config.sample_rate_hz,
            dataset_name=self.config.dataset_name,
            h5_template_path=self.config.h5_template_path,
            tdms_template_path=self.config.tdms_template_path,
            chunk_samples=self.config.chunk_samples,
            record_start_channel=self._start_ch,
            record_end_channel=self._end_ch - 1,
            distance_min_m=self.config.distance_min_m,
            distance_max_m=self.config.distance_max_m,
            origin_offset_m=self.config.origin_offset_m,
            probing_length_m=self.config.probing_length_m,
            channel_spacing_m=self.config.channel_spacing_m,
            index_of_refraction=self.config.index_of_refraction,
            source_n_channels=self.config.n_channels,
            noise_replace_by_zeros=self.config.noise_replace_by_zeros,
            noise_suppression_factor=self.config.noise_suppression_factor,
            mode=self.config.mode,
            impulse_duration_ns=self.config.impulse_duration_ns,
            clock_period_ns=self.config.clock_period_ns,
        )
        self._reported_finished_files = 0

    def _emit_new_finished_files(self) -> None:
        if self._segment_manager is None:
            return
        paths = self._segment_manager.finished_files
        for path in paths[self._reported_finished_files :]:
            self.file_closed.emit(str(path))
        self._reported_finished_files = len(paths)

    def _close_segment_manager(self) -> None:
        if self._segment_manager is None:
            return
        self._segment_manager.close()
        self._emit_new_finished_files()
        self._segment_manager = None
        self._reported_finished_files = 0

    def _select_block_data(self, block) -> object:
        data = block.data
        source_start = (
            int(getattr(block, "source_line_start", 0))
            if bool(getattr(block, "compact_stream", False))
            else 0
        )
        source_stop = source_start + int(data.shape[0])

        selected = np.zeros(
            (self._selected_channels, int(data.shape[1])),
            dtype=data.dtype,
        )
        overlap_start = max(self._start_ch, source_start)
        overlap_stop = min(self._end_ch, source_stop)
        if overlap_stop > overlap_start:
            target_start = overlap_start - self._start_ch
            target_stop = overlap_stop - self._start_ch
            data_start = overlap_start - source_start
            data_stop = overlap_stop - source_start
            selected[target_start:target_stop, :] = data[data_start:data_stop, :]
        return selected

    @QtCore.pyqtSlot(object)
    def apply_geometry_settings(self, config: RecorderConfig) -> None:
        """Apply channel selection changes between live packet blocks."""
        if self._saving_enabled or self._segment_manager is not None:
            self.status.emit("Geometry is locked while a recording file is open")
            return

        self.config.origin_offset_m = float(config.origin_offset_m)
        self.config.probing_length_m = float(config.probing_length_m)
        self.config.channel_spacing_m = float(config.channel_spacing_m)
        self.config.auto_channel_spacing = bool(config.auto_channel_spacing)
        self.config.clock_period_ns = float(config.clock_period_ns)
        self.config.index_of_refraction = float(config.index_of_refraction)
        self.config.n_channels = int(config.n_channels)
        self.config.distance_min_m = float(config.distance_min_m)
        self.config.distance_max_m = float(config.distance_max_m)
        self._start_ch, self._end_ch = config.selected_channel_slice()
        self._selected_channels = self._end_ch - self._start_ch
        if self._source is not None:
            self._source.request_acquisition_update(
                n_channels=self.config.n_channels,
                probing_length_m=self.config.probing_length_m,
                sample_rate_hz=self.config.sample_rate_hz,
                impulse_duration_ns=self.config.impulse_duration_ns,
                clock_period_ns=self.config.clock_period_ns,
            )
        self.status.emit(
            f"Live geometry updated: {self.config.n_channels} calculated channels, "
            f"{self.config.channel_spacing_m:.6f} m spacing"
        )

    @QtCore.pyqtSlot(object)
    def apply_recording_settings(self, config: RecorderConfig) -> None:
        """Apply file and geometry settings selected after receiver connection."""
        self.config.output_dir = str(config.output_dir)
        self.config.formats = list(config.formats)
        self.config.segment_duration_s = int(config.segment_duration_s)
        self.config.dataset_name = str(config.dataset_name)
        self.config.h5_template_path = str(config.h5_template_path)
        self.config.tdms_template_path = str(config.tdms_template_path)
        self.apply_geometry_settings(config)

    @QtCore.pyqtSlot()
    def begin_saving(self) -> None:
        # These flags may be set by the GUI thread while run() owns this
        # QObject's worker thread. The receive loop reads them between blocks.
        self._saving_enabled = True
        self._close_saving_requested = False
        self.status.emit("Recording enabled on live stream")

    @QtCore.pyqtSlot()
    def stop_saving(self) -> None:
        self._saving_enabled = False
        self._close_saving_requested = True
        self.status.emit("Recording stop requested; live stream remains connected")

    @QtCore.pyqtSlot()
    def run(self) -> None:
        if self._stop_requested:
            self.finished.emit()
            return
        self._running = True
        try:
            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            log_dir = output_dir / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            diagnostics_path = log_dir / f"dunay_receiver_diagnostics_{stamp}.log"
            self._diagnostics_summary_path = (
                log_dir / f"dunay_receiver_diagnostics_{stamp}.summary.json"
            )

            if self.config.source != "dunay_network":
                raise ValueError(f"Unsupported data source: {self.config.source}")

            self.status.emit(f"Diagnostics log: {diagnostics_path}")
            self._source = DunayNetworkSource(
                self.config.device_ip,
                self.config.local_ip,
                self.config.n_channels,
                self.config.sample_rate_hz,
                self.config.chunk_samples,
                line_point_start=getattr(self.config, "line_point_start", 73),
                device_control_enabled=getattr(self.config, "device_control_enabled", False),
                device_command_mode=getattr(self.config, "device_command_mode", "phase"),
                status_callback=self.status.emit,
                diagnostics_path=str(diagnostics_path),
                impulse_duration_ns=self.config.impulse_duration_ns,
                probing_length_m=self.config.probing_length_m,
                clock_period_ns=self.config.clock_period_ns,
            )

            self._start_ch, self._end_ch = self.config.selected_channel_slice()
            self._selected_channels = self._end_ch - self._start_ch
            if self._selected_channels <= 0:
                raise ValueError("Invalid distance range: no channel selected")

            noise_mode = "hard-zero" if self.config.noise_replace_by_zeros else "soft"
            noise_factor = float(self.config.noise_suppression_factor)
            state = "Recording started" if self._saving_enabled else "Receiver started"
            self.status.emit(
                f"{state}: channels {self._start_ch} to {self._end_ch - 1} "
                f"({self.config.distance_min_m:.0f} m to {self.config.distance_max_m:.0f} m), "
                f"noise={noise_factor:.0f}% {noise_mode}"
            )

            if self._stop_requested:
                return

            for block in self._source.blocks():
                if not self._running:
                    break

                if self._close_saving_requested:
                    self._close_segment_manager()
                    self._close_saving_requested = False
                    self.status.emit("Recording stopped; live stream remains connected")

                received_count = int(
                    getattr(block, "received_channel_count", block.data.shape[0])
                )
                # Save and display only the selected distance/channel range.
                selected_data = self._select_block_data(block)

                # Apply simple real-time noise suppression before save/display.
                # If noise_suppression_factor is 0, this returns selected_data unchanged.
                processed_data = apply_noise_reduction(
                    selected_data,
                    replace_by_zeros=self.config.noise_replace_by_zeros,
                    suppression_factor=self.config.noise_suppression_factor,
                )

                active_count = int(
                    getattr(
                        block,
                        "active_channel_count",
                        (processed_data != -15708).any(axis=1).sum(),
                    )
                )
                if self._saving_enabled:
                    self._ensure_segment_manager()
                    self._segment_manager.write_block(
                        processed_data,
                        block.timestamp,
                        received_channel_count=received_count,
                    )
                    self._emit_new_finished_files()
                self.channel_counts.emit(received_count, active_count)
                self.phase_received.emit(
                    int(getattr(self._source, "phase_packet_count", 0))
                )
                self.block_ready.emit(processed_data)

            self.status.emit("Stopping receiver")
            self._close_segment_manager()
            if hasattr(self._source, "diagnostics_summary") and self._diagnostics_summary_path is not None:
                summary = self._source.diagnostics_summary()
                self._diagnostics_summary_path.write_text(
                    json.dumps(summary, indent=2),
                    encoding="utf-8",
                )
                self.status.emit(f"Diagnostics summary: {self._diagnostics_summary_path}")

        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self._running = False
            self.finished.emit()

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._stop_requested = True
        self._running = False
        if self._source is not None:
            self._source.stop()
