from __future__ import annotations

import argparse
from pathlib import Path

import h5py

EXPECTED_ATTRS = [
    "DecimationFactor",
    "EndPoint",
    "FirmwareVersion",
    "Frequency",
    "HardwareVersion",
    "ImpDuration",
    "ImpPeriod",
    "IsAveragedBy8",
    "IsCyclicCalcEnabled",
    "IsDetectionEnabled",
    "IsNoiseSuppressed",
    "LineOffset",
    "MetricLength",
    "MetricOffset",
    "Mode",
    "NoiseSuppressorThresholdLoweringFactor",
    "ProcessEvery",
    "RecordEndPoint",
    "RecordStartPoint",
    "RecordingTime",
    "StartPoint",
    "Version",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate KIGAM/Dunay-style H5 output structure.")
    parser.add_argument("h5_file", help="Path to recorded H5 file")
    args = parser.parse_args()

    path = Path(args.h5_file)
    if not path.exists():
        raise FileNotFoundError(path)

    with h5py.File(path, "r") as f:
        if "DataStreams" not in f:
            raise RuntimeError("Missing /DataStreams group")
        names = list(f["DataStreams"].keys())
        if len(names) != 1:
            raise RuntimeError(f"Expected one dataset under /DataStreams, found {len(names)}: {names}")
        ds_name = names[0]
        if not ds_name.startswith("DS#"):
            raise RuntimeError(f"Dataset name should start with DS#, found: {ds_name}")
        ds = f["DataStreams"][ds_name]
        attrs = list(ds.attrs.keys())
        missing = [name for name in EXPECTED_ATTRS if name not in attrs]
        extra = [name for name in attrs if name not in EXPECTED_ATTRS]

        print(f"File: {path}")
        print(f"Dataset: /DataStreams/{ds_name}")
        print(f"Shape: {ds.shape}")
        print(f"Dtype: {ds.dtype}")
        print(f"Chunks: {ds.chunks}")
        print(f"Attribute count: {len(attrs)}")
        print("Missing attributes:", missing or "None")
        print("Extra attributes:", extra or "None")

        if missing or extra or len(attrs) != 22:
            raise RuntimeError("H5 structure does not match the 22-attribute KIGAM/Dunay structure")

        print("OK: H5 file matches the expected KIGAM/Dunay structure.")


if __name__ == "__main__":
    main()
