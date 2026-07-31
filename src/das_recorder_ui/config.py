from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


SPEED_OF_LIGHT_M_S = 299_792_458.0
DEFAULT_CLOCK_PERIOD_NS = 10.0
DEFAULT_APP_VERSION = "1.3.2"
DUNAY_PHASE_DECIMATION_FACTOR = 4


def clock_channel_spacing_m(
    clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
    index_of_refraction: float = 1.4680,
) -> float:
    """Return round-trip optical distance represented by one hardware tick."""
    clock_period_ns = float(clock_period_ns)
    index_of_refraction = float(index_of_refraction)
    if clock_period_ns <= 0.0:
        raise ValueError("Clock period must be greater than zero")
    if index_of_refraction <= 0.0:
        raise ValueError("Index of refraction must be greater than zero")
    return (
        SPEED_OF_LIGHT_M_S
        * clock_period_ns
        * 1e-9
        / (2.0 * index_of_refraction)
    )


def impulse_period_clock_ticks(
    sample_rate_hz: float,
    clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
) -> int:
    """Convert pulse repetition frequency to 10 ns hardware-clock ticks."""
    sample_rate_hz = float(sample_rate_hz)
    clock_period_ns = float(clock_period_ns)
    if sample_rate_hz <= 0.0:
        raise ValueError("Sample rate must be greater than zero")
    if clock_period_ns <= 0.0:
        raise ValueError("Clock period must be greater than zero")
    return max(1, int(round((1e9 / clock_period_ns) / sample_rate_hz)))


def phase_output_sample_rate_hz(
    pulse_frequency_hz: float,
    decimation_factor: int = DUNAY_PHASE_DECIMATION_FACTOR,
) -> float:
    """Return the stored phase-sample rate defined by device API Table 6."""
    pulse_frequency_hz = float(pulse_frequency_hz)
    decimation_factor = int(decimation_factor)
    if pulse_frequency_hz <= 0.0:
        raise ValueError("Pulse frequency must be greater than zero")
    if decimation_factor <= 0:
        raise ValueError("Phase decimation factor must be greater than zero")
    return pulse_frequency_hz / decimation_factor


def impulse_duration_clock_ticks(
    impulse_duration_ns: float,
    clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
) -> int:
    """Convert an optical pulse duration from nanoseconds to clock ticks."""
    impulse_duration_ns = float(impulse_duration_ns)
    clock_period_ns = float(clock_period_ns)
    if impulse_duration_ns <= 0.0:
        raise ValueError("Impulse duration must be greater than zero")
    if clock_period_ns <= 0.0:
        raise ValueError("Clock period must be greater than zero")
    return max(1, int(round(impulse_duration_ns / clock_period_ns)))


@dataclass
class RecorderConfig:
    app_version: str = DEFAULT_APP_VERSION
    source: str = "dunay_network"
    output_dir: str = "recordings"
    formats: List[str] = field(default_factory=lambda: ["h5"])
    segment_duration_s: int = 60
    sample_rate_hz: float = 2000.0
    n_channels: int = 19578
    chunk_samples: int = 500
    dataset_name: str = "DS"
    h5_template_path: str = ""
    tdms_template_path: str = ""
    device_ip: str = "192.168.180.10"
    local_ip: str = "192.168.180.11"
    device_control_enabled: bool = True
    device_command_mode: str = "phase"
    origin_offset_m: float = 10.0
    probing_length_m: float = 20000.0
    channel_spacing_m: float = 1.021091478201635
    auto_channel_spacing: bool = True
    clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS
    index_of_refraction: float = 1.4680
    line_point_start: int = 73
    impulse_duration_ns: int = 200
    mode: str = "Restored phase"
    waterfall_window_s: int = 10
    max_display_channels: int = 900
    display_columns: int = 1200
    waterfall_low_position: int = 0
    waterfall_high_position: int = 2500
    waterfall_gamma_position: int = 50

    # Distance window selected by the user.
    # Only channels inside this distance interval are shown and saved.
    distance_min_m: float = 10.0
    distance_max_m: float = 20000.0

    # Real-time noise suppression.
    # noise_suppression_factor: 0 = off/low, 100 = strongest.
    noise_replace_by_zeros: bool = False
    noise_suppression_factor: float = 0.0

    def __post_init__(self) -> None:
        self.app_version = str(self.app_version).strip() or DEFAULT_APP_VERSION
        self.origin_offset_m = float(self.origin_offset_m)
        self.probing_length_m = max(
            float(self.probing_length_m),
            self.origin_offset_m + 1.0,
        )
        self.distance_min_m = max(
            self.origin_offset_m,
            min(float(self.distance_min_m), self.probing_length_m),
        )
        self.distance_max_m = max(
            self.origin_offset_m,
            min(float(self.distance_max_m), self.probing_length_m),
        )
        if self.distance_min_m >= self.distance_max_m:
            self.distance_min_m = self.origin_offset_m
            self.distance_max_m = self.probing_length_m
        self.waterfall_low_position = max(
            -5000,
            min(5000, int(self.waterfall_low_position)),
        )
        self.waterfall_high_position = max(
            -5000,
            min(5000, int(self.waterfall_high_position)),
        )
        self.waterfall_gamma_position = max(
            1,
            min(100, int(self.waterfall_gamma_position)),
        )
        if float(self.clock_period_ns) <= 0.0:
            self.clock_period_ns = DEFAULT_CLOCK_PERIOD_NS
        if self.auto_channel_spacing:
            self.channel_spacing_m = self.clock_derived_channel_spacing_m()
        elif float(self.channel_spacing_m) <= 0.0:
            self.channel_spacing_m = self._legacy_channel_spacing()
        self.n_channels = self.calculated_n_channels()

    def clock_derived_channel_spacing_m(self) -> float:
        return clock_channel_spacing_m(
            self.clock_period_ns,
            self.index_of_refraction,
        )

    def impulse_period_ticks(self) -> int:
        return impulse_period_clock_ticks(
            self.sample_rate_hz,
            self.clock_period_ns,
        )

    def impulse_duration_ticks(self) -> int:
        return impulse_duration_clock_ticks(
            self.impulse_duration_ns,
            self.clock_period_ns,
        )

    def phase_sample_rate_hz(self) -> float:
        return phase_output_sample_rate_hz(self.sample_rate_hz)

    def _legacy_channel_spacing(self) -> float:
        span_m = float(self.probing_length_m) - float(self.origin_offset_m)
        if self.n_channels > 1 and span_m > 0:
            return span_m / float(self.n_channels - 1)
        return 1.0

    def calculated_n_channels(self) -> int:
        """Calculate channel count from acquisition geometry.

        Channels include both endpoints: distance positions are
        origin_offset_m + i * channel_spacing_m, for i = 0..n_channels-1,
        while probing_length_m is the absolute maximum distance.
        """
        spacing = float(self.channel_spacing_m)
        if spacing <= 0.0:
            return max(1, int(self.n_channels))
        span_m = max(
            0.0,
            float(self.probing_length_m) - float(self.origin_offset_m),
        )
        return max(1, int(math.floor((span_m / spacing) + 1e-9)) + 1)

    def distance_for_channel(self, channel_index: int) -> float:
        return float(self.origin_offset_m) + int(channel_index) * float(self.channel_spacing_m)

    @classmethod
    def load(cls, path: str | Path) -> "RecorderConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))

        # Keep old config files compatible if new fields are missing.
        valid_keys = set(cls.__dataclass_fields__.keys())
        data = {key: value for key, value in data.items() if key in valid_keys}
        return cls(**data)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")

    def selected_channel_slice(self) -> Tuple[int, int]:
        """Return the selected channel slice as [start, end_exclusive).

        Distance-to-channel mapping:
            distance_m = origin_offset_m + channel_index * channel_spacing_m
        """
        n_channels = self.calculated_n_channels()
        if n_channels <= 0:
            return 0, 0

        if self.probing_length_m <= 0:
            return 0, n_channels

        d_min = min(float(self.distance_min_m), float(self.distance_max_m))
        d_max = max(float(self.distance_min_m), float(self.distance_max_m))

        channel_spacing_m = float(self.channel_spacing_m)
        if channel_spacing_m <= 0:
            return 0, n_channels

        start = int((d_min - float(self.origin_offset_m)) // channel_spacing_m)
        end = int(((d_max - float(self.origin_offset_m)) / channel_spacing_m)) + 1

        start = max(0, min(start, n_channels - 1))
        end = max(start + 1, min(end, n_channels))
        return start, end

    def selected_n_channels(self) -> int:
        start, end = self.selected_channel_slice()
        return max(0, end - start)
