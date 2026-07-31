#!/usr/bin/env python
"""
Inspect an HDF5 DAS file, write a text summary, and create visualizations.

Example:
    python analyze_kigam_h5.py KIGAM_50Hz_2kHz.h5 --output-dir h5_analysis

Optional:
    python analyze_kigam_h5.py input.h5 --dataset "DataStreams/DS#..." --channel 700
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import h5py
import matplotlib.pyplot as plt
import numpy as np


def decode_value(value: Any) -> Any:
    """Convert HDF5/NumPy values into readable Python values."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, np.ndarray):
        if value.size == 1:
            return decode_value(value.reshape(-1)[0])
        return [decode_value(item) for item in value.tolist()]

    if isinstance(value, np.generic):
        return decode_value(value.item())

    return value


def collect_structure(h5_file: h5py.File) -> list[str]:
    """Return readable lines describing every group and dataset."""
    lines: list[str] = []

    def visitor(name: str, obj: h5py.Group | h5py.Dataset) -> None:
        if isinstance(obj, h5py.Group):
            lines.append(f"[GROUP]   /{name}")
        elif isinstance(obj, h5py.Dataset):
            lines.append(
                f"[DATASET] /{name} | shape={obj.shape} | dtype={obj.dtype} "
                f"| chunks={obj.chunks} | compression={obj.compression}"
            )

    h5_file.visititems(visitor)
    return lines


def find_numeric_2d_datasets(h5_file: h5py.File) -> list[str]:
    """Find numeric two-dimensional datasets."""
    candidates: list[tuple[int, str]] = []

    def visitor(name: str, obj: h5py.Group | h5py.Dataset) -> None:
        if (
            isinstance(obj, h5py.Dataset)
            and obj.ndim == 2
            and np.issubdtype(obj.dtype, np.number)
        ):
            candidates.append((int(np.prod(obj.shape)), name))

    h5_file.visititems(visitor)
    candidates.sort(reverse=True)
    return [name for _, name in candidates]


def get_attribute(dataset: h5py.Dataset, name: str, default: Any = None) -> Any:
    """Read and decode an HDF5 dataset attribute."""
    if name not in dataset.attrs:
        return default
    return decode_value(dataset.attrs[name])


def chunked_statistics(dataset: h5py.Dataset) -> dict[str, float | int]:
    """
    Compute exact min, max, mean, standard deviation, zero count, and
    non-finite count without loading the entire dataset at once.
    """
    rows, cols = dataset.shape
    block_cols = dataset.chunks[1] if dataset.chunks else min(cols, 2000)
    block_cols = max(1, int(block_cols))

    total_count = 0
    finite_count = 0
    zero_count = 0
    nonfinite_count = 0
    global_min = np.inf
    global_max = -np.inf
    total_sum = 0.0
    total_sum_sq = 0.0

    for start in range(0, cols, block_cols):
        stop = min(start + block_cols, cols)
        block = np.asarray(dataset[:, start:stop], dtype=np.float64)

        total_count += block.size
        finite_mask = np.isfinite(block)
        finite = block[finite_mask]
        nonfinite_count += block.size - finite.size

        if finite.size:
            finite_count += finite.size
            zero_count += int(np.count_nonzero(finite == 0))
            global_min = min(global_min, float(np.min(finite)))
            global_max = max(global_max, float(np.max(finite)))
            total_sum += float(np.sum(finite, dtype=np.float64))
            total_sum_sq += float(np.sum(finite * finite, dtype=np.float64))

    if finite_count == 0:
        return {
            "total_count": total_count,
            "finite_count": 0,
            "nonfinite_count": nonfinite_count,
            "zero_count": 0,
            "minimum": float("nan"),
            "maximum": float("nan"),
            "mean": float("nan"),
            "standard_deviation": float("nan"),
        }

    mean = total_sum / finite_count
    variance = max(0.0, total_sum_sq / finite_count - mean * mean)

    return {
        "total_count": total_count,
        "finite_count": finite_count,
        "nonfinite_count": nonfinite_count,
        "zero_count": zero_count,
        "minimum": global_min,
        "maximum": global_max,
        "mean": mean,
        "standard_deviation": variance**0.5,
    }


def sampled_percentiles(
    dataset: h5py.Dataset,
    percentiles: tuple[float, ...] = (1, 5, 50, 95, 99),
    max_values: int = 2_000_000,
) -> dict[float, float]:
    """Estimate percentiles using a regularly decimated sample."""
    total = int(np.prod(dataset.shape))
    stride = max(1, int(np.ceil(np.sqrt(total / max_values))))

    sample = np.asarray(dataset[::stride, ::stride], dtype=np.float64)
    sample = sample[np.isfinite(sample)]

    if sample.size == 0:
        return {p: float("nan") for p in percentiles}

    values = np.percentile(sample, percentiles)
    return {p: float(v) for p, v in zip(percentiles, values)}


def reduce_for_display(
    dataset: h5py.Dataset,
    max_channels: int = 900,
    max_time_samples: int = 1800,
) -> tuple[np.ndarray, int, int]:
    """Decimate a large 2-D dataset for efficient heatmap rendering."""
    n_channels, n_samples = dataset.shape
    channel_step = max(1, int(np.ceil(n_channels / max_channels)))
    time_step = max(1, int(np.ceil(n_samples / max_time_samples)))
    display_data = np.asarray(
        dataset[::channel_step, ::time_step],
        dtype=np.float32,
    )
    return display_data, channel_step, time_step


def save_waterfall(
    dataset: h5py.Dataset,
    output_path: Path,
    sample_rate_hz: float | None,
    metric_length_m: float | None,
) -> None:
    data, channel_step, time_step = reduce_for_display(dataset)

    finite = data[np.isfinite(data)]
    if finite.size:
        robust_limit = float(np.percentile(np.abs(finite), 99))
        robust_limit = robust_limit if robust_limit > 0 else None
    else:
        robust_limit = None

    n_channels, n_samples = dataset.shape
    time_end = n_samples / sample_rate_hz if sample_rate_hz else n_samples

    if metric_length_m and metric_length_m > 0:
        y_end = metric_length_m
        y_label = "Distance along fiber (m)"
    else:
        y_end = n_channels - 1
        y_label = "Channel index"

    plt.figure(figsize=(12, 7))
    plt.imshow(
        data,
        aspect="auto",
        origin="lower",
        extent=[0, time_end, 0, y_end],
        vmin=-robust_limit if robust_limit else None,
        vmax=robust_limit,
    )
    plt.colorbar(label="Amplitude")
    plt.xlabel("Time (s)" if sample_rate_hz else "Sample index")
    plt.ylabel(y_label)
    plt.title(
        "DAS waterfall / strain-rate image\n"
        f"display decimation: channel ×{channel_step}, time ×{time_step}"
    )
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def save_channel_trace(
    dataset: h5py.Dataset,
    channel: int,
    output_path: Path,
    sample_rate_hz: float | None,
) -> None:
    trace = np.asarray(dataset[channel, :], dtype=np.float64)

    if sample_rate_hz:
        x = np.arange(trace.size) / sample_rate_hz
        x_label = "Time (s)"
    else:
        x = np.arange(trace.size)
        x_label = "Sample index"

    plt.figure(figsize=(12, 5))
    plt.plot(x, trace, linewidth=0.8)
    plt.xlabel(x_label)
    plt.ylabel("Amplitude")
    plt.title(f"Time-series trace: channel {channel}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def save_rms_profile(
    dataset: h5py.Dataset,
    output_path: Path,
    metric_length_m: float | None,
) -> None:
    n_channels, n_samples = dataset.shape
    rms = np.empty(n_channels, dtype=np.float64)

    row_block = 128
    for start in range(0, n_channels, row_block):
        stop = min(start + row_block, n_channels)
        block = np.asarray(dataset[start:stop, :], dtype=np.float64)
        rms[start:stop] = np.sqrt(np.nanmean(block * block, axis=1))

    if metric_length_m and metric_length_m > 0:
        x = np.linspace(0, metric_length_m, n_channels)
        x_label = "Distance along fiber (m)"
    else:
        x = np.arange(n_channels)
        x_label = "Channel index"

    plt.figure(figsize=(12, 5))
    plt.plot(x, rms, linewidth=0.9)
    plt.xlabel(x_label)
    plt.ylabel("RMS amplitude")
    plt.title("RMS amplitude by channel")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def save_frequency_spectrum(
    dataset: h5py.Dataset,
    channel: int,
    output_path: Path,
    sample_rate_hz: float | None,
) -> None:
    if not sample_rate_hz or sample_rate_hz <= 0:
        return

    trace = np.asarray(dataset[channel, :], dtype=np.float64)
    trace = np.nan_to_num(trace - np.nanmean(trace))
    window = np.hanning(trace.size)
    spectrum = np.abs(np.fft.rfft(trace * window))
    frequency = np.fft.rfftfreq(trace.size, d=1.0 / sample_rate_hz)

    plt.figure(figsize=(12, 5))
    plt.semilogy(frequency, spectrum + np.finfo(float).eps, linewidth=0.9)
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude")
    plt.title(f"Frequency spectrum: channel {channel}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def write_summary(
    input_path: Path,
    output_path: Path,
    structure: list[str],
    dataset_name: str,
    dataset: h5py.Dataset,
    sample_rate_hz: float | None,
    metric_length_m: float | None,
    stats: dict[str, float | int],
    percentiles: dict[float, float],
    selected_channel: int,
    plot_names: list[str],
) -> None:
    n_channels, n_samples = dataset.shape
    duration_seconds = n_samples / sample_rate_hz if sample_rate_hz else None
    estimated_spacing = (
        metric_length_m / (n_channels - 1)
        if metric_length_m and n_channels > 1
        else None
    )

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("HDF5 DAS FILE SUMMARY")
    lines.append("=" * 78)
    lines.append(f"Input file              : {input_path.resolve()}")
    lines.append(f"File size               : {input_path.stat().st_size:,} bytes")
    lines.append("")
    lines.append("HDF5 STRUCTURE")
    lines.append("-" * 78)
    lines.extend(structure)
    lines.append("")
    lines.append("SELECTED DATASET")
    lines.append("-" * 78)
    lines.append(f"Dataset path            : /{dataset_name}")
    lines.append(f"Shape                    : {dataset.shape}")
    lines.append(f"Data type                : {dataset.dtype}")
    lines.append(f"Number of dimensions     : {dataset.ndim}")
    lines.append(f"Chunk shape              : {dataset.chunks}")
    lines.append(f"Compression              : {dataset.compression}")
    lines.append(f"Assumed channel axis     : axis 0 ({n_channels} channels)")
    lines.append(f"Assumed time axis        : axis 1 ({n_samples} samples)")
    lines.append(f"Sampling frequency       : {sample_rate_hz} Hz")
    lines.append(
        f"Sampling interval        : {1.0 / sample_rate_hz:.9f} s"
        if sample_rate_hz
        else "Sampling interval        : unavailable"
    )
    lines.append(
        f"Nyquist frequency        : {sample_rate_hz / 2.0:.3f} Hz"
        if sample_rate_hz
        else "Nyquist frequency        : unavailable"
    )
    lines.append(
        f"Estimated duration       : {duration_seconds:.6f} s"
        if duration_seconds is not None
        else "Estimated duration       : unavailable"
    )
    lines.append(f"Metric fiber length      : {metric_length_m} m")
    lines.append(
        f"Estimated channel spacing: {estimated_spacing:.6f} m/channel"
        if estimated_spacing is not None
        else "Estimated channel spacing: unavailable"
    )
    lines.append(f"Selected trace channel   : {selected_channel}")
    lines.append("")
    lines.append("DATA STATISTICS")
    lines.append("-" * 78)
    lines.append(f"Total values             : {stats['total_count']:,}")
    lines.append(f"Finite values            : {stats['finite_count']:,}")
    lines.append(f"Non-finite values        : {stats['nonfinite_count']:,}")
    lines.append(f"Zero values              : {stats['zero_count']:,}")
    lines.append(f"Minimum                  : {stats['minimum']:.6f}")
    lines.append(f"Maximum                  : {stats['maximum']:.6f}")
    lines.append(f"Mean                     : {stats['mean']:.6f}")
    lines.append(f"Standard deviation       : {stats['standard_deviation']:.6f}")
    lines.append("")
    lines.append("SAMPLED PERCENTILES")
    lines.append("-" * 78)
    for percentile, value in percentiles.items():
        lines.append(f"{percentile:>6.1f} percentile         : {value:.6f}")
    lines.append("")
    lines.append("DATASET ATTRIBUTES")
    lines.append("-" * 78)
    if dataset.attrs:
        for key in sorted(dataset.attrs.keys()):
            lines.append(f"{key:<25}: {decode_value(dataset.attrs[key])}")
    else:
        lines.append("No dataset attributes found.")
    lines.append("")
    lines.append("GENERATED VISUALIZATIONS")
    lines.append("-" * 78)
    for name in plot_names:
        lines.append(name)
    lines.append("")
    lines.append("INTERPRETATION NOTE")
    lines.append("-" * 78)
    lines.append(
        "The script assumes the dataset layout is [channel, time]. "
        "Verify this convention against the acquisition software documentation. "
        "MetricLength/(channels-1) is only an estimated average channel spacing."
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize and visualize a numeric 2-D DAS HDF5 dataset."
    )
    parser.add_argument("input_file", type=Path, help="Input .h5 or .hdf5 file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("h5_analysis"),
        help="Directory for summary and figures",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Dataset path. If omitted, the largest numeric 2-D dataset is used.",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=None,
        help="Channel index for trace and spectrum. Default: middle channel.",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=None,
        help="Override sampling frequency in Hz.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input_file.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with h5py.File(input_path, "r") as h5_file:
        structure = collect_structure(h5_file)

        if args.dataset:
            dataset_name = args.dataset.lstrip("/")
            if dataset_name not in h5_file:
                raise KeyError(f"Dataset not found: /{dataset_name}")
        else:
            candidates = find_numeric_2d_datasets(h5_file)
            if not candidates:
                raise RuntimeError("No numeric two-dimensional dataset was found.")
            dataset_name = candidates[0]

        dataset = h5_file[dataset_name]
        if dataset.ndim != 2:
            raise ValueError(f"Selected dataset must be 2-D; got shape {dataset.shape}")

        sample_rate_hz = (
            float(args.sample_rate)
            if args.sample_rate is not None
            else get_attribute(dataset, "Frequency", None)
        )
        sample_rate_hz = float(sample_rate_hz) if sample_rate_hz else None

        metric_length_m = get_attribute(dataset, "MetricLength", None)
        metric_length_m = float(metric_length_m) if metric_length_m else None

        n_channels = dataset.shape[0]
        selected_channel = (
            n_channels // 2 if args.channel is None else int(args.channel)
        )
        if not 0 <= selected_channel < n_channels:
            raise ValueError(
                f"--channel must be between 0 and {n_channels - 1}; "
                f"received {selected_channel}"
            )

        print(f"Selected dataset: /{dataset_name}")
        print(f"Shape: {dataset.shape}; dtype: {dataset.dtype}")
        print("Computing statistics...")
        stats = chunked_statistics(dataset)
        percentiles = sampled_percentiles(dataset)

        waterfall_path = args.output_dir / "01_waterfall.png"
        trace_path = args.output_dir / f"02_channel_{selected_channel}_trace.png"
        rms_path = args.output_dir / "03_rms_by_channel.png"
        spectrum_path = args.output_dir / f"04_channel_{selected_channel}_spectrum.png"
        summary_path = args.output_dir / "h5_summary.txt"

        print("Creating waterfall image...")
        save_waterfall(
            dataset,
            waterfall_path,
            sample_rate_hz,
            metric_length_m,
        )

        print("Creating channel trace...")
        save_channel_trace(
            dataset,
            selected_channel,
            trace_path,
            sample_rate_hz,
        )

        print("Creating RMS profile...")
        save_rms_profile(
            dataset,
            rms_path,
            metric_length_m,
        )

        plot_names = [
            waterfall_path.name,
            trace_path.name,
            rms_path.name,
        ]

        if sample_rate_hz:
            print("Creating frequency spectrum...")
            save_frequency_spectrum(
                dataset,
                selected_channel,
                spectrum_path,
                sample_rate_hz,
            )
            plot_names.append(spectrum_path.name)

        write_summary(
            input_path=input_path,
            output_path=summary_path,
            structure=structure,
            dataset_name=dataset_name,
            dataset=dataset,
            sample_rate_hz=sample_rate_hz,
            metric_length_m=metric_length_m,
            stats=stats,
            percentiles=percentiles,
            selected_channel=selected_channel,
            plot_names=plot_names,
        )

    print(f"Summary written to: {summary_path.resolve()}")
    print(f"Figures written to: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
