from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from das_recorder_ui.data_source import DunayCommandClient
from das_recorder_ui.config import RecorderConfig


def parse_phase_packet(packet: bytes, n_channels: int = 1457):
    if len(packet) < 1012:
        return None

    max_reasonable_line = max(int(n_channels) * 20, 100000)
    candidates = []
    for endian in ("<", ">"):
        line, block, radius = struct.unpack_from(f"{endian}III", packet, 0)
        compact_index = (0 <= line < n_channels) or (1 <= line <= n_channels)
        reasonable_coordinate = 0 <= line <= max_reasonable_line
        if compact_index or reasonable_coordinate:
            samples = np.frombuffer(packet, dtype=np.dtype(f"{endian}i2"), count=500, offset=12)
            score = 0 if compact_index else 1
            if endian == ">":
                score += 1
            candidates.append((score, endian, int(line), int(block), int(radius), samples))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    _score, endian, line, block, radius, samples = candidates[0]
    return endian, line, block, radius, samples


def main():
    parser = argparse.ArgumentParser(description="Test Dunay UDP phase packet reception on port 8227.")
    parser.add_argument("--local-ip", default="0.0.0.0", help="Local IP to bind. Use 0.0.0.0 to listen on all adapters.")
    parser.add_argument("--device-ip", default="", help="Optional device IP filter.")
    parser.add_argument("--port", type=int, default=8227, help="Dunay phase data UDP port.")
    parser.add_argument("--seconds", type=float, default=10.0, help="Listening duration.")
    parser.add_argument("--n-channels", type=int, default=None, help="Override calculated channel count.")
    parser.add_argument("--origin-offset", type=float, default=0.0)
    parser.add_argument("--probing-length", type=float, default=50.0)
    parser.add_argument("--channel-spacing", type=float, default=None)
    parser.add_argument("--frequency", type=float, default=2000.0)
    parser.add_argument("--duration-ns", type=int, default=200)
    parser.add_argument("--clock-period-ns", type=float, default=10.0)
    parser.add_argument("--index-of-refraction", type=float, default=1.4680)
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Send the captured vendor initialization sequence after opening the UDP receiver.",
    )
    args = parser.parse_args()
    cfg = RecorderConfig(
        origin_offset_m=args.origin_offset,
        probing_length_m=args.probing_length,
        channel_spacing_m=args.channel_spacing or 1.0,
        auto_channel_spacing=args.channel_spacing is None,
        clock_period_ns=args.clock_period_ns,
        index_of_refraction=args.index_of_refraction,
        sample_rate_hz=args.frequency,
        impulse_duration_ns=args.duration_ns,
        n_channels=args.n_channels or 1,
    )
    n_channels = int(args.n_channels) if args.n_channels is not None else cfg.calculated_n_channels()
    cfg.n_channels = n_channels

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)

    try:
        sock.bind((args.local_ip, args.port))
    except OSError as exc:
        print(f"Bind failed on {args.local_ip}:{args.port}: {exc}")
        print("Trying 0.0.0.0 ...")
        sock.bind(("0.0.0.0", args.port))

    print(f"Listening on UDP {args.port} for {args.seconds:g} seconds ...")
    print("Press Ctrl+C to stop earlier.")

    if args.connect:
        print("Sending the captured Dunay initialization after opening the receive socket ...")
        client = DunayCommandClient(
            args.device_ip,
            args.local_ip,
            n_channels,
            sample_rate_hz=cfg.sample_rate_hz,
            impulse_duration_ns=cfg.impulse_duration_ns,
            probing_length_m=cfg.probing_length_m,
            clock_period_ns=cfg.clock_period_ns,
            phase_timeout_s=max(args.seconds, 1.0),
        )
        try:
            print(client.initialize_and_request_phase_stream(mode="phase"))
        except Exception as exc:
            print(f"Initialization/start sequence failed; continuing to listen: {exc}")

    t0 = time.time()
    count = 0
    parsed_count = 0

    try:
        while time.time() - t0 < args.seconds:
            try:
                packet, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue

            if args.device_ip and addr[0] != args.device_ip:
                continue

            count += 1
            parsed = parse_phase_packet(packet, n_channels)
            print(f"Packet {count}: {len(packet)} bytes from {addr}")

            if parsed:
                endian, line, block, radius, samples = parsed
                parsed_count += 1
                print(
                    f"  parsed phase: endian={endian}, line_point_no={line}, "
                    f"block_no={block}, first_samples={samples[:8].tolist()}"
                )
            else:
                print("  not parsed as phase packet")
                print("  first 32 bytes:", packet[:32].hex(" "))

    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

    print(f"\nReceived packets: {count}")
    print(f"Parsed phase packets: {parsed_count}")


if __name__ == "__main__":
    main()
