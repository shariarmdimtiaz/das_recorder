from __future__ import annotations

import struct
import sys
import unittest
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from das_recorder_ui.config import (  # noqa: E402
    RecorderConfig,
    clock_channel_spacing_m,
    impulse_duration_clock_ticks,
    impulse_period_clock_ticks,
    phase_output_sample_rate_hz,
)
from das_recorder_ui.data_source import DunayCommandClient  # noqa: E402
from das_recorder_ui.writers import H5SegmentWriter, SegmentManager  # noqa: E402


class ClockGeometryTests(unittest.TestCase):
    def test_dunay_10ns_clock_values(self) -> None:
        spacing = clock_channel_spacing_m(10.0, 1.4680)
        self.assertAlmostEqual(spacing, 1.021091478201635, places=12)
        self.assertEqual(impulse_period_clock_ticks(1000.0, 10.0), 100000)
        self.assertEqual(impulse_period_clock_ticks(1500.0, 10.0), 66667)
        self.assertEqual(impulse_period_clock_ticks(2000.0, 10.0), 50000)
        self.assertEqual(impulse_duration_clock_ticks(200, 10.0), 20)
        self.assertEqual(phase_output_sample_rate_hz(2000.0), 500.0)
        self.assertEqual(phase_output_sample_rate_hz(1000.0), 250.0)

    def test_table_6_phase_decimation_closes_60_second_h5(self) -> None:
        output_dir = PROJECT_ROOT / "tmp" / "table_6_segment_test"
        output_dir.mkdir(parents=True, exist_ok=True)
        manager = SegmentManager(
            output_dir=str(output_dir),
            formats=["h5"],
            segment_duration_s=60,
            n_channels=2,
            sample_rate_hz=2000.0,
            dataset_name="Table6",
            chunk_samples=500,
            source_n_channels=2,
            impulse_duration_ns=200,
            clock_period_ns=10.0,
        )
        block = np.zeros((2, 500), dtype=np.int16)
        start_time = datetime(2026, 7, 29, 14, 0, 0)
        timestamp = start_time.timestamp()
        try:
            self.assertEqual(manager.phase_sample_rate_hz, 500.0)
            self.assertEqual(manager.samples_per_segment, 30000)

            for packet_index in range(59):
                manager.write_block(block, timestamp + packet_index)
            self.assertEqual(manager.finished_files, [])

            manager.write_block(block, timestamp + 59)
            self.assertEqual(len(manager.finished_files), 1)

            with h5py.File(manager.finished_files[0], "r") as h5_file:
                dataset = next(iter(h5_file["DataStreams"].values()))
                self.assertEqual(dataset.shape, (2, 30000))
                self.assertEqual(int(dataset.attrs["Frequency"][0]), 2000)
                self.assertEqual(int(dataset.attrs["ImpPeriod"][0]), 50000)
                self.assertEqual(
                    float(h5_file.attrs["PhaseSamplingFrequency_Hz"][0]),
                    500.0,
                )
                self.assertEqual(
                    int(h5_file.attrs["PhaseDecimationFactor"][0]),
                    4,
                )
        finally:
            manager.close()
            for path in output_dir.glob("Table6_*.h5"):
                path.unlink(missing_ok=True)

    def test_100m_clock_geometry_matches_vendor_channel_count(self) -> None:
        config = RecorderConfig(
            sample_rate_hz=1000.0,
            origin_offset_m=0.0,
            probing_length_m=100.0,
            distance_min_m=0.0,
            distance_max_m=100.0,
            index_of_refraction=1.4680,
            clock_period_ns=10.0,
            auto_channel_spacing=True,
        )
        self.assertEqual(config.n_channels, 98)
        self.assertAlmostEqual(
            config.channel_spacing_m,
            1.021091478201635,
            places=12,
        )

    def test_offset_is_removed_from_absolute_probing_limit(self) -> None:
        config = RecorderConfig(
            origin_offset_m=10.0,
            probing_length_m=100.0,
            distance_min_m=10.0,
            distance_max_m=100.0,
            index_of_refraction=1.4680,
            clock_period_ns=10.0,
            auto_channel_spacing=True,
        )
        self.assertEqual(config.n_channels, 89)
        self.assertAlmostEqual(config.distance_min_m, 10.0)
        self.assertAlmostEqual(config.distance_max_m, 100.0)

    def test_device_setup_payload_uses_clock_ticks(self) -> None:
        client = DunayCommandClient(
            "192.168.180.10",
            "192.168.180.11",
            98,
            sample_rate_hz=1000.0,
            impulse_duration_ns=200,
            probing_length_m=100.0,
            clock_period_ns=10.0,
        )
        words = struct.unpack(">16I", client.acquisition_setup_payload()[:64])
        self.assertEqual(words[0], 100000)
        self.assertEqual(words[1], 20)
        self.assertEqual(words[5], 99)

    def test_h5_metadata_matches_vendor_clock_units(self) -> None:
        spacing = clock_channel_spacing_m(10.0, 1.4680)
        test_output_dir = PROJECT_ROOT / "tmp" / "clock_writer_test"
        test_output_dir.mkdir(parents=True, exist_ok=True)
        path = test_output_dir / "test_clock_geometry.h5"
        try:
            writer = H5SegmentWriter(
                path,
                n_channels=98,
                sample_rate_hz=1000.0,
                dataset_name="DS",
                distance_min_m=0.0,
                distance_max_m=100.0,
                probing_length_m=100.0,
                channel_spacing_m=spacing,
                source_n_channels=98,
                impulse_duration_ns=200,
                clock_period_ns=10.0,
            )
            writer.open(datetime(2026, 7, 28, 11, 56, 33))
            writer.write_block(np.zeros((98, 500), dtype=np.int16))
            writer.close()

            with h5py.File(path, "r") as h5_file:
                dataset = next(iter(h5_file["DataStreams"].values()))
                self.assertEqual(dataset.shape, (98, 500))
                self.assertEqual(int(dataset.attrs["ImpPeriod"][0]), 100000)
                self.assertEqual(int(dataset.attrs["ImpDuration"][0]), 20)
                self.assertEqual(int(dataset.attrs["RecordStartPoint"][0]), 0)
                self.assertEqual(int(dataset.attrs["RecordEndPoint"][0]), 100)
                self.assertEqual(int(dataset.attrs["EndPoint"][0]), 99)
                self.assertAlmostEqual(
                    float(h5_file.attrs["ChannelSpacing_m"][0]),
                    spacing,
                    places=12,
                )
                self.assertEqual(float(h5_file.attrs["ClockPeriod_ns"][0]), 10.0)
        finally:
            path.unlink(missing_ok=True)

    def test_h5_metric_offset_uses_absolute_selected_distance(self) -> None:
        spacing = clock_channel_spacing_m(10.0, 1.4680)
        test_output_dir = PROJECT_ROOT / "tmp" / "clock_writer_test"
        test_output_dir.mkdir(parents=True, exist_ok=True)
        path = test_output_dir / "test_absolute_offset.h5"
        try:
            writer = H5SegmentWriter(
                path,
                n_channels=98,
                sample_rate_hz=1000.0,
                dataset_name="DS",
                distance_min_m=10.0,
                distance_max_m=100.0,
                origin_offset_m=10.0,
                probing_length_m=100.0,
                channel_spacing_m=spacing,
                source_n_channels=98,
                impulse_duration_ns=200,
                clock_period_ns=10.0,
            )
            writer.open(datetime(2026, 7, 29, 10, 0, 0))
            writer.write_block(np.zeros((98, 500), dtype=np.int16))
            writer.close()

            with h5py.File(path, "r") as h5_file:
                dataset = next(iter(h5_file["DataStreams"].values()))
                self.assertEqual(int(dataset.attrs["MetricOffset"][0]), 10)
                self.assertEqual(int(dataset.attrs["MetricLength"][0]), 90)
                self.assertEqual(int(dataset.attrs["RecordStartPoint"][0]), 10)
                self.assertEqual(int(dataset.attrs["RecordEndPoint"][0]), 100)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
