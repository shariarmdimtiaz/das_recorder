from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h5py
from nptdms import TdmsFile
from PyQt5 import QtCore, QtTest, QtWidgets


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from das_recorder_ui.config import RecorderConfig
from das_recorder_ui.main_window import MainWindow


def _h5_summary(path: Path) -> dict:
    with h5py.File(path, "r") as h5_file:
        dataset = next(iter(h5_file["DataStreams"].values()))
        return {
            "path": str(path),
            "shape": list(dataset.shape),
            "frequency": int(dataset.attrs["Frequency"][0]),
            "imp_period": int(dataset.attrs["ImpPeriod"][0]),
            "imp_duration": int(dataset.attrs["ImpDuration"][0]),
            "channel_spacing_m": float(h5_file.attrs["ChannelSpacing_m"][0]),
            "clock_period_ns": float(h5_file.attrs["ClockPeriod_ns"][0]),
            "received_channels": int(h5_file.attrs["ReceivedChannelCount"][0]),
        }


def _tdms_summary(path: Path) -> dict:
    tdms_file = TdmsFile.read(path)
    return {
        "path": str(path),
        "frequency": float(tdms_file.properties["FrequencyHz"]),
        "imp_period": int(tdms_file.properties["ImpPeriod"]),
        "imp_duration": int(tdms_file.properties["ImpDuration"]),
        "channel_spacing_m": float(tdms_file.properties["ChannelSpacing_m"]),
        "clock_period_ns": float(tdms_file.properties["ClockPeriod_ns"]),
        "received_channels": int(tdms_file.properties["ReceivedChannelCount"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise the real Dunay connection and recording through the Qt UI."
    )
    parser.add_argument("--connect-wait-s", type=float, default=15.0)
    parser.add_argument("--record-s", type=float, default=5.0)
    parser.add_argument("--frequency", type=float, default=1000.0)
    parser.add_argument("--probing-length", type=float, default=100.0)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "tmp" / "ui_live_recordings"),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in output_dir.glob("*") if path.is_file()}

    config = RecorderConfig.load(PROJECT_ROOT / "config" / "default_config.json")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(config)
    window._set_output_path(str(output_dir))
    window.freq.setValue(args.frequency)
    window.probing_length.setValue(args.probing_length)
    window.format_combo.setCurrentText("both")
    window.segment_duration.setValue(max(1, int(round(args.record_s))))
    window.show()
    QtTest.QTest.qWait(500)

    QtTest.QTest.mouseClick(window.connect_btn, QtCore.Qt.LeftButton)
    deadline_ms = max(1000, int(round(args.connect_wait_s * 1000)))
    waited_ms = 0
    while waited_ms < deadline_ms and not window.connection_ok:
        QtTest.QTest.qWait(250)
        waited_ms += 250

    result = {
        "connected": bool(window.connection_ok),
        "status": window.statusBar().currentMessage(),
        "spacing_display": window.channel_spacing.text(),
        "spacing_exact": window._effective_channel_spacing_from_ui(),
        "calculated_channels": window.channels_value.text(),
        "received_channels": window.receiving_channels_value.text(),
        "active_channels": window.active_channels_value.text(),
        "phase_received": int(window.phase_received_count),
    }

    if window.connection_ok:
        QtTest.QTest.mouseClick(window.start_btn, QtCore.Qt.LeftButton)
        QtTest.QTest.qWait(max(1000, int(round(args.record_s * 1000))))
        QtTest.QTest.mouseClick(window.stop_btn, QtCore.Qt.LeftButton)
        QtTest.QTest.qWait(1500)
        result.update(
            {
                "status_after_recording": window.statusBar().currentMessage(),
                "received_channels_after_recording": window.receiving_channels_value.text(),
                "active_channels_after_recording": window.active_channels_value.text(),
                "phase_received_after_recording": int(window.phase_received_count),
            }
        )

    screenshot_path = PROJECT_ROOT / "tmp" / "live_ui_clock_test.png"
    window.grab().save(str(screenshot_path))
    result["screenshot"] = str(screenshot_path)

    if window.worker is not None:
        QtTest.QTest.mouseClick(window.connect_btn, QtCore.Qt.LeftButton)
        QtTest.QTest.qWait(1500)
    window.close()
    QtTest.QTest.qWait(500)

    created = sorted(
        path.resolve()
        for path in output_dir.glob("*")
        if path.is_file() and path.resolve() not in before
    )
    h5_files = [path for path in created if path.suffix.lower() == ".h5"]
    tdms_files = [path for path in created if path.suffix.lower() == ".tdms"]
    result["created_files"] = [str(path) for path in created]
    if h5_files:
        result["h5"] = _h5_summary(h5_files[-1])
    if tdms_files:
        result["tdms"] = _tdms_summary(tdms_files[-1])

    print(json.dumps(result, indent=2))
    return 0 if result["connected"] and h5_files and tdms_files else 1


if __name__ == "__main__":
    raise SystemExit(main())
