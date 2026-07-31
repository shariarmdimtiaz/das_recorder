from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import h5py
import numpy as np

from .config import (
    DEFAULT_CLOCK_PERIOD_NS,
    DUNAY_PHASE_DECIMATION_FACTOR,
    impulse_duration_clock_ticks,
    impulse_period_clock_ticks,
    phase_output_sample_rate_hz,
)

try:
    from nptdms import ChannelObject, RootObject, GroupObject, TdmsWriter
except Exception:  # nptdms is optional until TDMS output is selected.
    ChannelObject = RootObject = GroupObject = TdmsWriter = None


# -----------------------------------------------------------------------------
# Segment/file names
# -----------------------------------------------------------------------------

def _frequency_label(sample_rate_hz: float) -> str:
    """Return a filename-safe frequency label such as 2000Hz or 1500p5Hz."""
    value = float(sample_rate_hz)
    if value.is_integer():
        return f"{int(value)}Hz"
    return f"{str(value).rstrip('0').rstrip('.').replace('.', 'p')}Hz"


def make_segment_name(start_time: datetime, sample_rate_hz: float, dataset_prefix: str = "DS") -> str:
    """Build the output file / TDMS group base name.

    Example:
        DS_20260629_172821_1500Hz

    This remains filename-safe. For H5 internal dataset naming, use
    make_kigam_h5_dataset_name(), because the KIGAM/Dunay H5 tree uses
    DS#YYYYMMDDTHH:MM:SS inside /DataStreams.
    """
    prefix = (dataset_prefix or "DS").strip() or "DS"
    safe_prefix = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in prefix)
    local_stamp = start_time.strftime("%Y%m%d_%H%M%S")
    return f"{safe_prefix}_{local_stamp}_{_frequency_label(sample_rate_hz)}"


def make_kigam_h5_dataset_name(start_time: datetime) -> str:
    """KIGAM/Dunay-compatible internal H5 dataset name.

    Required H5 tree:
        /DataStreams/DS#YYYYMMDDTHH:MM:SS

    Example:
        /DataStreams/DS#20260624T15:35:01
    """
    return f"DS#{start_time.strftime('%Y%m%dT%H:%M:%S')}"


# -----------------------------------------------------------------------------
# KIGAM/Dunay H5 attribute helpers
# -----------------------------------------------------------------------------

KIGAM_STRING_ATTR_SIZES = {
    "FirmwareVersion": 11,
    "HardwareVersion": 11,
    "Mode": 5,
    "RecordingTime": 19,
    "Version": 8,
}


def _default_kigam_attrs(start_time: datetime) -> Dict[str, object]:
    """Return the standard 22 KIGAM/Dunay dataset attributes.

    These defaults are used when the original KIGAM_40Hz_2kHz.h5 template file
    is not available. The writer still creates the same attribute names and
    array/scalar style as the reference.
    """
    return {
        "DecimationFactor": np.array([1], dtype=np.int32),
        "EndPoint": np.array([1470], dtype=np.int32),
        "FirmwareVersion": "04:00:03:0C",
        "Frequency": np.array([2000], dtype=np.int32),
        "HardwareVersion": "03:01:02:00",
        "ImpDuration": np.array([20], dtype=np.int32),
        "ImpPeriod": np.array([50000], dtype=np.int32),
        "IsAveragedBy8": np.array([0], dtype=np.uint8),
        "IsCyclicCalcEnabled": np.array([1], dtype=np.uint8),
        "IsDetectionEnabled": np.array([0], dtype=np.uint8),
        "IsNoiseSuppressed": np.array([1], dtype=np.uint8),
        "LineOffset": np.array([73], dtype=np.int32),
        "MetricLength": np.array([1500], dtype=np.int32),
        "MetricOffset": np.array([0], dtype=np.int32),
        "Mode": "Phase",
        "NoiseSuppressorThresholdLoweringFactor": np.array([1], dtype=np.int32),
        "ProcessEvery": np.array([1], dtype=np.int32),
        "RecordEndPoint": np.array([1489], dtype=np.int32),
        "RecordStartPoint": np.array([1], dtype=np.int32),
        "RecordingTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "StartPoint": np.array([0], dtype=np.int32),
        "Version": "1.0.0.98",
    }


def _as_one_value(value: object) -> object:
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return 0
        return value.reshape(-1)[0]
    return value


def _to_fixed_bytes(value: object, size: int) -> bytes:
    value = _as_one_value(value)
    if isinstance(value, np.bytes_):
        raw = bytes(value)
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8")
    # Fixed-length HDF5 string attr. Keep room for NULLTERM when possible.
    return raw[:size]


def _create_fixed_string_attr(
    dataset,
    name: str,
    value: object,
    size: int,
    cset: int = h5py.h5t.CSET_UTF8,
    strpad: int = h5py.h5t.STR_NULLTERM,
) -> None:
    """Create a fixed-length HDF5 string attribute like the Dunay template.

    h5py's high-level attr assignment usually creates NULLPAD strings. HDFView
    then shows H5T_STR_NULLPAD. The KIGAM/Dunay template uses NULLTERM + UTF8.
    This low-level writer preserves that style.
    """
    if name in dataset.attrs:
        del dataset.attrs[name]

    type_id = h5py.h5t.C_S1.copy()
    type_id.set_size(int(size))
    type_id.set_cset(int(cset))
    type_id.set_strpad(int(strpad))

    mem_type = h5py.h5t.C_S1.copy()
    mem_type.set_size(int(size))
    mem_type.set_cset(int(cset))
    mem_type.set_strpad(int(strpad))

    space = h5py.h5s.create_simple((1,))
    attr = h5py.h5a.create(dataset.id, name.encode("utf-8"), type_id, space)
    arr = np.array([_to_fixed_bytes(value, int(size))], dtype=f"S{int(size)}")
    attr.write(arr, mtype=mem_type)
    attr.close()


def _create_numeric_attr(dataset, name: str, value: object, dtype, shape=(1,)) -> None:
    if name in dataset.attrs:
        del dataset.attrs[name]
    arr = np.array(value, dtype=dtype)
    if arr.shape == ():
        arr = arr.reshape((1,))
    if shape and arr.shape != tuple(shape):
        arr = np.array([_as_one_value(arr)], dtype=dtype)
    dataset.attrs.create(name, arr, dtype=dtype)


def _read_template_attr_specs(template_path: Optional[Path]):
    """Read template attributes including low-level string type metadata."""
    if not template_path or not template_path.exists():
        return None
    try:
        with h5py.File(template_path, "r") as f:
            if "DataStreams" not in f or not list(f["DataStreams"].keys()):
                return None
            first_key = list(f["DataStreams"].keys())[0]
            template_ds = f["DataStreams"][first_key]
            specs = []
            for key in template_ds.attrs.keys():
                aid = template_ds.attrs.get_id(key)
                type_id = aid.get_type()
                klass = type_id.get_class()
                spec = {
                    "name": key,
                    "value": template_ds.attrs[key],
                    "dtype": aid.dtype,
                    "shape": aid.shape,
                    "class": klass,
                }
                if klass == h5py.h5t.STRING:
                    spec.update(
                        {
                            "size": type_id.get_size(),
                            "cset": type_id.get_cset(),
                            "strpad": type_id.get_strpad(),
                        }
                    )
                specs.append(spec)
            return specs
    except Exception:
        return None


def _write_kigam_attrs(
    dataset,
    *,
    template_path: Optional[Path],
    start_time: datetime,
    sample_rate_hz: float,
    mode: str = "Restored phase",
    impulse_duration_ns: Optional[int] = None,
    clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
    record_start_channel: int = 0,
    record_end_channel: int = 0,
    distance_min_m: float = 0.0,
    distance_max_m: float = 0.0,
    origin_offset_m: float = 0.0,
    noise_replace_by_zeros: bool = False,
    noise_suppression_factor: float = 0.0,
) -> None:
    """Write only the 22 KIGAM/Dunay dataset attributes.

    The purpose is to make HDFView show the same tree/attribute structure as:
        /DataStreams/DS#YYYYMMDDTHH:MM:SS

    Extra recorder metadata is intentionally NOT written to the H5 dataset,
    because that changes the visible attribute count and no longer matches the
    reference KIGAM file.
    """
    specs = _read_template_attr_specs(template_path)

    recording_time = start_time.strftime("%Y-%m-%dT%H:%M:%S")
    phase_mode = "Phase" if "phase" in str(mode).lower() else "Raw"
    selected_span_m = max(0, int(round(float(distance_max_m) - float(distance_min_m))))
    record_start_point = int(round(float(distance_min_m)))
    record_end_point = int(round(float(distance_max_m)))

    # Override only values that must reflect this recording.
    overrides = {
        "Frequency": int(round(float(sample_rate_hz))),
        "ImpPeriod": impulse_period_clock_ticks(sample_rate_hz, clock_period_ns),
        "RecordingTime": recording_time,
        "Mode": phase_mode,
        "IsNoiseSuppressed": 1 if (noise_replace_by_zeros or float(noise_suppression_factor) > 0) else 0,
        "NoiseSuppressorThresholdLoweringFactor": max(1, int(round(float(noise_suppression_factor))) or 1),
        "RecordStartPoint": record_start_point,
        "RecordEndPoint": record_end_point,
        "StartPoint": record_start_point,
        "EndPoint": max(record_start_point, record_end_point - 1),
        "MetricOffset": int(round(float(distance_min_m))),
        "MetricLength": selected_span_m,
    }
    if impulse_duration_ns is not None:
        overrides["ImpDuration"] = impulse_duration_clock_ticks(
            impulse_duration_ns,
            clock_period_ns,
        )

    if specs:
        for spec in specs:
            name = spec["name"]
            value = overrides.get(name, spec["value"])
            if spec["class"] == h5py.h5t.STRING:
                _create_fixed_string_attr(
                    dataset,
                    name,
                    value,
                    size=int(spec["size"]),
                    cset=int(spec["cset"]),
                    strpad=int(spec["strpad"]),
                )
            else:
                _create_numeric_attr(dataset, name, value, spec["dtype"], spec["shape"])
        return

    # Fallback: no template file available. Create the same 22 attributes.
    attrs = _default_kigam_attrs(start_time)
    for key, value in overrides.items():
        if key in attrs:
            current = attrs[key]
            if isinstance(current, np.ndarray):
                attrs[key] = np.array([value], dtype=current.dtype)
            else:
                attrs[key] = value

    for key, value in attrs.items():
        if key in KIGAM_STRING_ATTR_SIZES:
            _create_fixed_string_attr(dataset, key, value, KIGAM_STRING_ATTR_SIZES[key])
        else:
            arr = value if isinstance(value, np.ndarray) else np.array([value], dtype=np.int32)
            _create_numeric_attr(dataset, key, arr, arr.dtype, arr.shape)


# -----------------------------------------------------------------------------
# H5 writer
# -----------------------------------------------------------------------------

class H5SegmentWriter:
    """Streaming H5 writer compatible with the KIGAM/Dunay H5 tree.

    Output file name remains filename-safe:
        DS_YYYYMMDD_HHMMSS_FrequencyHz.h5

    Internal H5 tree now matches the KIGAM reference:
        /DataStreams/DS#YYYYMMDDTHH:MM:SS

    Dataset attributes now follow the KIGAM reference dataset:
        exactly 22 standard attributes, with fixed UTF8 NULLTERM strings when
        a template file is available.
    """

    def __init__(
        self,
        output_path: Path,
        n_channels: int,
        sample_rate_hz: float,
        dataset_name: str,
        template_path: Optional[str] = None,
        chunk_samples: int = 500,
        record_start_channel: int = 0,
        record_end_channel: Optional[int] = None,
        distance_min_m: float = 0.0,
        distance_max_m: float = 0.0,
        origin_offset_m: float = 0.0,
        probing_length_m: float = 0.0,
        channel_spacing_m: float = 0.0,
        index_of_refraction: float = 1.4680,
        source_n_channels: Optional[int] = None,
        noise_replace_by_zeros: bool = False,
        noise_suppression_factor: float = 0.0,
        mode: str = "Restored phase",
        impulse_duration_ns: Optional[int] = None,
        clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
        phase_decimation_factor: int = DUNAY_PHASE_DECIMATION_FACTOR,
    ):
        self.output_path = Path(output_path)
        self.n_channels = int(n_channels)
        self.sample_rate_hz = float(sample_rate_hz)
        self.dataset_name = dataset_name or "DS"
        self.template_path = Path(template_path) if template_path else None
        self.chunk_samples = int(chunk_samples)
        self.record_start_channel = int(record_start_channel)
        self.record_end_channel = int(record_end_channel if record_end_channel is not None else self.n_channels - 1)
        self.distance_min_m = float(distance_min_m)
        self.distance_max_m = float(distance_max_m)
        self.origin_offset_m = float(origin_offset_m)
        self.probing_length_m = float(probing_length_m)
        self.channel_spacing_m = float(channel_spacing_m)
        self.index_of_refraction = float(index_of_refraction)
        self.source_n_channels = int(source_n_channels if source_n_channels is not None else self.n_channels)
        self.noise_replace_by_zeros = bool(noise_replace_by_zeros)
        self.noise_suppression_factor = float(noise_suppression_factor)
        self.mode = mode
        self.impulse_duration_ns = impulse_duration_ns
        self.clock_period_ns = float(clock_period_ns)
        self.phase_decimation_factor = int(phase_decimation_factor)
        self.phase_sample_rate_hz = phase_output_sample_rate_hz(
            self.sample_rate_hz,
            self.phase_decimation_factor,
        )
        self.samples_written = 0
        self.received_channel_count = 0
        self.file_segment_name = ""
        self.h5_dataset_name = ""
        self._file: Optional[h5py.File] = None
        self._dataset = None

    def open(self, start_time: datetime) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_segment_name = make_segment_name(start_time, self.sample_rate_hz, self.dataset_name)
        self.h5_dataset_name = make_kigam_h5_dataset_name(start_time)

        self._file = h5py.File(self.output_path, "w")
        _create_numeric_attr(
            self._file,
            "CalculatedChannelCount",
            [self.source_n_channels],
            np.int32,
        )
        _create_numeric_attr(
            self._file,
            "ReceivedChannelCount",
            [0],
            np.int32,
        )
        _create_numeric_attr(
            self._file,
            "SavedChannelCount",
            [self.n_channels],
            np.int32,
        )
        _create_numeric_attr(
            self._file,
            "ChannelSpacing_m",
            [self.channel_spacing_m],
            np.float64,
        )
        _create_numeric_attr(
            self._file,
            "ClockPeriod_ns",
            [self.clock_period_ns],
            np.float64,
        )
        _create_numeric_attr(
            self._file,
            "PhaseSamplingFrequency_Hz",
            [self.phase_sample_rate_hz],
            np.float64,
        )
        _create_numeric_attr(
            self._file,
            "PhaseDecimationFactor",
            [self.phase_decimation_factor],
            np.int32,
        )
        group = self._file.create_group("DataStreams")
        self._dataset = group.create_dataset(
            self.h5_dataset_name,
            shape=(self.n_channels, 0),
            maxshape=(self.n_channels, None),
            dtype=np.int16,
            chunks=(self.n_channels, max(1, self.chunk_samples)),
            compression=None,
        )

        _write_kigam_attrs(
            self._dataset,
            template_path=self.template_path,
            start_time=start_time,
            sample_rate_hz=self.sample_rate_hz,
            mode=self.mode,
            impulse_duration_ns=self.impulse_duration_ns,
            clock_period_ns=self.clock_period_ns,
            record_start_channel=self.record_start_channel,
            record_end_channel=self.record_end_channel,
            distance_min_m=self.distance_min_m,
            distance_max_m=self.distance_max_m,
            origin_offset_m=self.origin_offset_m,
            noise_replace_by_zeros=self.noise_replace_by_zeros,
            noise_suppression_factor=self.noise_suppression_factor,
        )

    def write_block(
        self,
        data: np.ndarray,
        received_channel_count: Optional[int] = None,
    ) -> None:
        if self._dataset is None:
            raise RuntimeError("H5 writer is not open")
        if data.shape[0] != self.n_channels:
            raise ValueError(f"Expected {self.n_channels} channels, got {data.shape[0]}")
        if received_channel_count is not None:
            self.received_channel_count = max(
                self.received_channel_count,
                max(0, int(received_channel_count)),
            )
            self._file.attrs.modify(
                "ReceivedChannelCount",
                np.array([self.received_channel_count], dtype=np.int32),
            )
        n_new = data.shape[1]
        old = self.samples_written
        new = old + n_new
        self._dataset.resize((self.n_channels, new))
        self._dataset[:, old:new] = data.astype(np.int16, copy=False)
        self.samples_written = new

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
        self._file = None
        self._dataset = None


# -----------------------------------------------------------------------------
# TDMS writer
# -----------------------------------------------------------------------------

class TdmsSegmentWriter:
    """TDMS segment writer using the DS timestamp group structure.

    Output file name:
        DS_YYYYMMDD_HHMMSS_FrequencyHz.tdms

    Output TDMS structure:
        Root properties
        Group: DS_YYYYMMDD_HHMMSS_FrequencyHz
            Channels: 0, 1, 2, ...
    """

    def __init__(
        self,
        output_path: Path,
        n_channels: int,
        sample_rate_hz: float,
        dataset_name: str = "DS",
        template_path: Optional[str] = None,
        record_start_channel: int = 0,
        record_end_channel: Optional[int] = None,
        distance_min_m: float = 0.0,
        distance_max_m: float = 0.0,
        origin_offset_m: float = 0.0,
        probing_length_m: float = 0.0,
        channel_spacing_m: float = 0.0,
        index_of_refraction: float = 1.4680,
        source_n_channels: Optional[int] = None,
        noise_replace_by_zeros: bool = False,
        noise_suppression_factor: float = 0.0,
        impulse_duration_ns: int = 200,
        clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
        phase_decimation_factor: int = DUNAY_PHASE_DECIMATION_FACTOR,
    ):
        if TdmsWriter is None:
            raise RuntimeError("TDMS output needs nptdms. Install it with: pip install nptdms")
        self.output_path = Path(output_path)
        self.n_channels = int(n_channels)
        self.sample_rate_hz = float(sample_rate_hz)
        self.dataset_name = dataset_name or "DS"
        self.template_path = Path(template_path) if template_path else None
        self.record_start_channel = int(record_start_channel)
        self.record_end_channel = int(record_end_channel if record_end_channel is not None else self.n_channels - 1)
        self.distance_min_m = float(distance_min_m)
        self.distance_max_m = float(distance_max_m)
        self.origin_offset_m = float(origin_offset_m)
        self.probing_length_m = float(probing_length_m)
        self.channel_spacing_m = float(channel_spacing_m)
        self.index_of_refraction = float(index_of_refraction)
        self.source_n_channels = int(source_n_channels if source_n_channels is not None else self.n_channels)
        self.noise_replace_by_zeros = bool(noise_replace_by_zeros)
        self.noise_suppression_factor = float(noise_suppression_factor)
        self.impulse_duration_ns = int(impulse_duration_ns)
        self.clock_period_ns = float(clock_period_ns)
        self.phase_decimation_factor = int(phase_decimation_factor)
        self.phase_sample_rate_hz = phase_output_sample_rate_hz(
            self.sample_rate_hz,
            self.phase_decimation_factor,
        )
        self.start_time: Optional[datetime] = None
        self.segment_name = ""
        self._writer: Optional[TdmsWriter] = None
        self._samples_written = 0
        self.received_channel_count = 0

    def open(self, start_time: datetime) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.start_time = start_time
        self.segment_name = make_segment_name(start_time, self.sample_rate_hz, self.dataset_name)
        self._writer = None
        self._samples_written = 0

    def _root_properties(self, samples_per_channel: int) -> Dict[str, object]:
        return {
            "SegmentName": self.segment_name,
            "SamplingFrequency[Hz]": self.phase_sample_rate_hz,
            "FrequencyHz": self.sample_rate_hz,
            "PhaseDecimationFactor": self.phase_decimation_factor,
            "ClockPeriod_ns": self.clock_period_ns,
            "ImpPeriod": impulse_period_clock_ticks(
                self.sample_rate_hz,
                self.clock_period_ns,
            ),
            "ImpDuration": impulse_duration_clock_ticks(
                self.impulse_duration_ns,
                self.clock_period_ns,
            ),
            "StartTime": self.start_time.isoformat() if self.start_time else "",
            "DataType": "int16",
            "ChannelCount": self.n_channels,
            "SamplesPerChannel": int(samples_per_channel),
            "RecordStartPoint": self.record_start_channel,
            "RecordEndPoint": self.record_end_channel,
            "SelectedDistanceMin_m": self.distance_min_m,
            "SelectedDistanceMax_m": self.distance_max_m,
            "OriginOffset_m": self.origin_offset_m,
            "ProbingLength_m": self.probing_length_m,
            "ChannelSpacing_m": self.channel_spacing_m,
            "IndexOfRefraction": self.index_of_refraction,
            "SourceChannelCount": self.source_n_channels,
            "CalculatedChannelCount": self.source_n_channels,
            "ReceivedChannelCount": self.received_channel_count,
            "SavedChannelCount": self.n_channels,
            "NoiseReplaceByZeros": int(self.noise_replace_by_zeros),
            "NoiseSuppressionFactor_percent": self.noise_suppression_factor,
        }

    def write_block(
        self,
        data: np.ndarray,
        received_channel_count: Optional[int] = None,
    ) -> None:
        if data.shape[0] != self.n_channels:
            raise ValueError(f"Expected {self.n_channels} channels, got {data.shape[0]}")
        if received_channel_count is not None:
            self.received_channel_count = max(
                self.received_channel_count,
                max(0, int(received_channel_count)),
            )
        block = data.astype(np.int16, copy=False)
        first_block = self._writer is None
        if first_block:
            self._writer = TdmsWriter(str(self.output_path))
            self._writer.open()

        objects = []
        if first_block:
            objects.extend(
                [
                    RootObject(self._root_properties(block.shape[1])),
                    GroupObject(self.segment_name),
                ]
            )

        for channel_idx in range(self.n_channels):
            properties = None
            if first_block:
                properties = {
                    "unit_string": "phase",
                    "SourceChannelIndex": self.record_start_channel + channel_idx,
                }
            objects.append(
                ChannelObject(
                    self.segment_name,
                    str(channel_idx),
                    block[channel_idx, :],
                    properties=properties,
                )
            )

        self._writer.write_segment(objects)
        self._samples_written += int(block.shape[1])

    def close(self) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write_segment(
                [
                    RootObject(
                        {
                            "SamplesPerChannel": self._samples_written,
                            "ReceivedChannelCount": self.received_channel_count,
                        }
                    )
                ]
            )
        finally:
            self._writer.close()
            self._writer = None


# -----------------------------------------------------------------------------
# Segment manager
# -----------------------------------------------------------------------------

class SegmentManager:
    """Splits continuous DAS data into fixed-duration H5/TDMS files."""

    def __init__(
        self,
        output_dir: str,
        formats: Iterable[str],
        segment_duration_s: int,
        n_channels: int,
        sample_rate_hz: float,
        dataset_name: str,
        h5_template_path: str = "",
        tdms_template_path: str = "",
        chunk_samples: int = 500,
        record_start_channel: int = 0,
        record_end_channel: Optional[int] = None,
        distance_min_m: float = 0.0,
        distance_max_m: float = 0.0,
        origin_offset_m: float = 0.0,
        probing_length_m: float = 0.0,
        channel_spacing_m: float = 0.0,
        index_of_refraction: float = 1.4680,
        source_n_channels: Optional[int] = None,
        noise_replace_by_zeros: bool = False,
        noise_suppression_factor: float = 0.0,
        mode: str = "Restored phase",
        impulse_duration_ns: Optional[int] = None,
        clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
        phase_decimation_factor: int = DUNAY_PHASE_DECIMATION_FACTOR,
    ):
        self.output_dir = Path(output_dir)
        self.formats = [fmt.lower() for fmt in formats]
        self.segment_duration_s = int(segment_duration_s)
        self.n_channels = int(n_channels)
        self.sample_rate_hz = float(sample_rate_hz)
        self.dataset_name = dataset_name or "DS"
        self.h5_template_path = h5_template_path
        self.tdms_template_path = tdms_template_path
        self.chunk_samples = int(chunk_samples)
        self.record_start_channel = int(record_start_channel)
        self.record_end_channel = int(record_end_channel if record_end_channel is not None else self.n_channels - 1)
        self.distance_min_m = float(distance_min_m)
        self.distance_max_m = float(distance_max_m)
        self.origin_offset_m = float(origin_offset_m)
        self.probing_length_m = float(probing_length_m)
        self.channel_spacing_m = float(channel_spacing_m)
        self.index_of_refraction = float(index_of_refraction)
        self.source_n_channels = int(source_n_channels if source_n_channels is not None else self.n_channels)
        self.noise_replace_by_zeros = bool(noise_replace_by_zeros)
        self.noise_suppression_factor = float(noise_suppression_factor)
        self.mode = mode
        self.impulse_duration_ns = impulse_duration_ns
        self.clock_period_ns = float(clock_period_ns)
        self.phase_decimation_factor = int(phase_decimation_factor)
        self.phase_sample_rate_hz = phase_output_sample_rate_hz(
            self.sample_rate_hz,
            self.phase_decimation_factor,
        )
        self.segment_start: Optional[datetime] = None
        self.samples_in_segment = 0
        self.writers = []
        self.finished_files: List[Path] = []

    @property
    def samples_per_segment(self) -> int:
        return int(round(self.phase_sample_rate_hz * self.segment_duration_s))

    def _make_paths(self, start_time: datetime) -> Dict[str, Path]:
        base_name = make_segment_name(start_time, self.sample_rate_hz, self.dataset_name)
        paths = {}
        if "h5" in self.formats:
            paths["h5"] = self.output_dir / f"{base_name}.h5"
        if "tdms" in self.formats:
            paths["tdms"] = self.output_dir / f"{base_name}.tdms"
        return paths

    def _open_new_segment(self, start_time: datetime) -> None:
        self.close()
        self.segment_start = start_time
        self.samples_in_segment = 0
        paths = self._make_paths(start_time)
        self.writers = []
        if "h5" in paths:
            writer = H5SegmentWriter(
                paths["h5"],
                self.n_channels,
                self.sample_rate_hz,
                self.dataset_name,
                self.h5_template_path,
                self.chunk_samples,
                record_start_channel=self.record_start_channel,
                record_end_channel=self.record_end_channel,
                distance_min_m=self.distance_min_m,
                distance_max_m=self.distance_max_m,
                origin_offset_m=self.origin_offset_m,
                probing_length_m=self.probing_length_m,
                channel_spacing_m=self.channel_spacing_m,
                index_of_refraction=self.index_of_refraction,
                source_n_channels=self.source_n_channels,
                noise_replace_by_zeros=self.noise_replace_by_zeros,
                noise_suppression_factor=self.noise_suppression_factor,
                mode=self.mode,
                impulse_duration_ns=self.impulse_duration_ns,
                clock_period_ns=self.clock_period_ns,
                phase_decimation_factor=self.phase_decimation_factor,
            )
            writer.open(start_time)
            self.writers.append(writer)
        if "tdms" in paths:
            writer = TdmsSegmentWriter(
                paths["tdms"],
                self.n_channels,
                self.sample_rate_hz,
                self.dataset_name,
                self.tdms_template_path,
                record_start_channel=self.record_start_channel,
                record_end_channel=self.record_end_channel,
                distance_min_m=self.distance_min_m,
                distance_max_m=self.distance_max_m,
                origin_offset_m=self.origin_offset_m,
                probing_length_m=self.probing_length_m,
                channel_spacing_m=self.channel_spacing_m,
                index_of_refraction=self.index_of_refraction,
                source_n_channels=self.source_n_channels,
                noise_replace_by_zeros=self.noise_replace_by_zeros,
                noise_suppression_factor=self.noise_suppression_factor,
                impulse_duration_ns=(
                    self.impulse_duration_ns
                    if self.impulse_duration_ns is not None
                    else 200
                ),
                clock_period_ns=self.clock_period_ns,
                phase_decimation_factor=self.phase_decimation_factor,
            )
            writer.open(start_time)
            self.writers.append(writer)

    def write_block(
        self,
        data: np.ndarray,
        timestamp: float,
        received_channel_count: Optional[int] = None,
    ) -> None:
        block = data
        cursor = 0
        while cursor < block.shape[1]:
            if self.segment_start is None:
                self._open_new_segment(datetime.fromtimestamp(timestamp))
            remain_segment = self.samples_per_segment - self.samples_in_segment
            take = min(remain_segment, block.shape[1] - cursor)
            part = block[:, cursor : cursor + take]
            for writer in self.writers:
                writer.write_block(
                    part,
                    received_channel_count=received_channel_count,
                )
            self.samples_in_segment += take
            cursor += take
            if self.samples_in_segment >= self.samples_per_segment:
                self.close()
                if cursor < block.shape[1]:
                    next_offset_s = cursor / self.phase_sample_rate_hz
                    self._open_new_segment(datetime.fromtimestamp(timestamp + next_offset_s))

    def close(self) -> None:
        for writer in self.writers:
            try:
                writer.close()
                self.finished_files.append(writer.output_path)
            except Exception:
                raise
        self.writers = []
        self.segment_start = None
        self.samples_in_segment = 0
