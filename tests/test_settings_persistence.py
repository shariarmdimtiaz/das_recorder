from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402

from das_recorder_ui.config import RecorderConfig  # noqa: E402
from das_recorder_ui.main_window import MainWindow  # noqa: E402
from run_app import get_config_path, get_writable_config_path  # noqa: E402


class SettingsPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_factory_geometry_defaults_are_synchronized(self) -> None:
        loaded = RecorderConfig.load(
            PROJECT_ROOT / "config" / "default_config.json"
        )

        self.assertEqual(loaded.origin_offset_m, 10.0)
        self.assertEqual(loaded.probing_length_m, 20000.0)
        self.assertEqual(loaded.distance_min_m, 10.0)
        self.assertEqual(loaded.distance_max_m, 20000.0)
        self.assertEqual(loaded.n_channels, 19578)
        self.assertEqual(loaded.n_channels, loaded.calculated_n_channels())

    def test_current_ui_values_are_saved_and_loaded_as_defaults(self) -> None:
        test_dir = PROJECT_ROOT / "tmp" / "settings_persistence_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        config_path = test_dir / "default_config.json"
        output_path = test_dir / "recordings"
        config_path.unlink(missing_ok=True)

        window = MainWindow(RecorderConfig(), config_path=config_path)
        try:
            window.device_ip.setText("192.168.180.20")
            window.local_ip.setText("192.168.180.21")
            window._set_output_path(str(output_path))
            window.freq.setValue(1000.0)
            window.origin_offset.setValue(10.0)
            window.probing_length.setValue(20000.0)
            window.segment_duration.setValue(120)
            window.format_combo.setCurrentText("both")
            window.low_slider.setValue(-750)
            window.high_slider.setValue(1800)
            window.gamma_slider.setValue(65)

            self.assertTrue(window.save_defaults_action.isEnabled())
            self.assertTrue(
                window._persist_current_defaults(show_confirmation=False)
            )

            loaded = RecorderConfig.load(config_path)
            self.assertEqual(loaded.app_version, "1.3.2")
            self.assertEqual(loaded.device_ip, "192.168.180.20")
            self.assertEqual(loaded.local_ip, "192.168.180.21")
            self.assertEqual(Path(loaded.output_dir), output_path.resolve())
            self.assertEqual(loaded.sample_rate_hz, 1000.0)
            self.assertEqual(loaded.origin_offset_m, 10.0)
            self.assertEqual(loaded.probing_length_m, 20000.0)
            self.assertEqual(loaded.distance_min_m, 10.0)
            self.assertEqual(loaded.distance_max_m, 20000.0)
            self.assertEqual(loaded.segment_duration_s, 120)
            self.assertEqual(loaded.formats, ["h5", "tdms"])
            self.assertEqual(loaded.waterfall_low_position, -750)
            self.assertEqual(loaded.waterfall_high_position, 1800)
            self.assertEqual(loaded.waterfall_gamma_position, 65)
            self.assertTrue(loaded.auto_channel_spacing)
            self.assertEqual(
                loaded.n_channels,
                loaded.calculated_n_channels(),
            )
        finally:
            window.close()
            config_path.unlink(missing_ok=True)

    def test_configured_version_is_shown_in_main_window(self) -> None:
        window = MainWindow(RecorderConfig(app_version="1.3.7"))
        try:
            self.assertEqual(window.app_version, "1.3.7")
            self.assertEqual(
                window.windowTitle(),
                "DAS Recorder (Dunay) v1.3.7",
            )
        finally:
            window.close()

    def test_user_settings_override_factory_config_at_startup(self) -> None:
        app_data = PROJECT_ROOT / "tmp" / "settings_appdata_test"
        expected = (
            app_data
            / "H4Tech"
            / "DASRecorder"
            / "default_config.json"
        )
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.unlink(missing_ok=True)
        try:
            with patch.dict(os.environ, {"APPDATA": str(app_data)}):
                self.assertEqual(get_writable_config_path(), expected)
                RecorderConfig(
                    device_ip="10.0.0.20",
                    origin_offset_m=10.0,
                    probing_length_m=20000.0,
                ).save(expected)
                self.assertEqual(get_config_path(), expected)
                loaded = RecorderConfig.load(get_config_path())
                self.assertEqual(loaded.device_ip, "10.0.0.20")
                self.assertEqual(loaded.origin_offset_m, 10.0)
                self.assertEqual(loaded.probing_length_m, 20000.0)
        finally:
            expected.unlink(missing_ok=True)

    def test_saved_waterfall_positions_are_restored_in_ui(self) -> None:
        config = RecorderConfig(
            waterfall_low_position=-1200,
            waterfall_high_position=1750,
            waterfall_gamma_position=72,
        )
        window = MainWindow(config)
        try:
            self.assertEqual(window.low_slider.value(), -1200)
            self.assertEqual(window.high_slider.value(), 1750)
            self.assertEqual(window.gamma_slider.value(), 72)
        finally:
            window.close()

    def test_distance_inputs_and_slider_follow_offset_bounds(self) -> None:
        config = RecorderConfig(
            origin_offset_m=10.0,
            probing_length_m=200.0,
            distance_min_m=10.0,
            distance_max_m=200.0,
        )
        window = MainWindow(config)
        try:
            self.assertEqual(window.range_min_spin.minimum(), 10)
            self.assertEqual(window.range_max_spin.maximum(), 200)
            self.assertEqual(window.range_min_spin.value(), 10)
            self.assertEqual(window.range_max_spin.value(), 200)
            self.assertEqual(window.distance_range_slider.lowValue(), 10)
            self.assertEqual(window.distance_range_slider.highValue(), 200)

            slider = window.distance_range_slider
            center_y = slider.height() / 2
            start_x = slider._value_to_x(10)
            end_x = slider._value_to_x(60)
            for event_type, x, button, buttons in (
                (
                    QtCore.QEvent.MouseButtonPress,
                    start_x,
                    QtCore.Qt.LeftButton,
                    QtCore.Qt.LeftButton,
                ),
                (
                    QtCore.QEvent.MouseMove,
                    end_x,
                    QtCore.Qt.NoButton,
                    QtCore.Qt.LeftButton,
                ),
                (
                    QtCore.QEvent.MouseButtonRelease,
                    end_x,
                    QtCore.Qt.LeftButton,
                    QtCore.Qt.NoButton,
                ),
            ):
                event = QtGui.QMouseEvent(
                    event_type,
                    QtCore.QPointF(x, center_y),
                    button,
                    buttons,
                    QtCore.Qt.NoModifier,
                )
                QtWidgets.QApplication.sendEvent(slider, event)
            self.assertEqual(window.range_min_spin.value(), 60)
            self.assertEqual(window.range_max_spin.value(), 200)

            window.distance_range_slider.setValues(21, 188, emit=True)
            self.assertEqual(window.range_min_spin.value(), 21)
            self.assertEqual(window.range_max_spin.value(), 188)

            window.range_min_spin.setValue(35)
            window.range_max_spin.setValue(175)
            self.assertEqual(window.distance_range_slider.lowValue(), 35)
            self.assertEqual(window.distance_range_slider.highValue(), 175)

            window.range_min_spin.setValue(-100)
            self.assertEqual(window.range_min_spin.value(), 10)

            window.range_min_spin.setValue(10)
            window.range_max_spin.setValue(200)
            window.origin_offset.setValue(20.0)
            self.assertEqual(window.range_min_spin.minimum(), 20)
            self.assertEqual(window.range_max_spin.maximum(), 200)
            self.assertEqual(window.range_min_spin.value(), 20)
            self.assertEqual(window.range_max_spin.value(), 200)

            window.probing_length.setValue(150.0)
            self.assertEqual(window.range_min_spin.minimum(), 20)
            self.assertEqual(window.range_max_spin.maximum(), 150)
            self.assertEqual(window.range_min_spin.value(), 20)
            self.assertEqual(window.range_max_spin.value(), 150)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
