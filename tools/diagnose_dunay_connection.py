from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from das_recorder_ui.data_source import DunayCommandClient
from das_recorder_ui.config import RecorderConfig


def print_step(name: str, ok: bool, detail: str) -> None:
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")


def list_ipconfig_ipv4() -> list[str]:
    try:
        result = subprocess.run(
            ["ipconfig"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []

    ips: list[str] = []
    for line in result.stdout.splitlines():
        if "IPv4" not in line:
            continue
        if ":" not in line:
            continue
        ip = line.split(":", 1)[1].strip()
        if ip:
            ips.append(ip)
    return ips


def can_bind(ip: str, port: int) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((ip, port))
        return True, f"{ip}:{port} can be opened"
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def route_to_device(local_ip: str, device_ip: str) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if local_ip and local_ip != "0.0.0.0":
            sock.bind((local_ip, 0))
        sock.connect((device_ip, DunayCommandClient.COMMAND_PORT))
        selected_ip, selected_port = sock.getsockname()
        return True, f"Windows routes commands from {selected_ip}:{selected_port} to {device_ip}:8201"
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def run_connect_test(
    local_ip: str,
    device_ip: str,
    config: RecorderConfig,
    seconds: float,
) -> tuple[bool, str]:
    try:
        client = DunayCommandClient(
            device_ip=device_ip,
            local_ip=local_ip,
            n_channels=config.n_channels,
            sample_rate_hz=config.sample_rate_hz,
            impulse_duration_ns=config.impulse_duration_ns,
            probing_length_m=config.probing_length_m,
            clock_period_ns=config.clock_period_ns,
            command_timeout_s=1.0,
            phase_timeout_s=seconds,
        )
        return True, client.initialize_and_request_phase_stream(mode="phase")
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Dunay DAS UDP connection settings.")
    parser.add_argument("--local-ip", default="192.168.180.11")
    parser.add_argument("--device-ip", default="192.168.180.10")
    parser.add_argument("--n-channels", type=int, default=None, help="Override calculated channel count.")
    parser.add_argument("--origin-offset", type=float, default=0.0)
    parser.add_argument("--probing-length", type=float, default=50.0)
    parser.add_argument("--channel-spacing", type=float, default=None)
    parser.add_argument("--frequency", type=float, default=2000.0)
    parser.add_argument("--duration-ns", type=int, default=200)
    parser.add_argument("--clock-period-ns", type=float, default=10.0)
    parser.add_argument("--index-of-refraction", type=float, default=1.4680)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument(
        "--send-commands",
        action="store_true",
        help="Send the captured vendor initialization sequence and phase-start command.",
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

    print("Dunay DAS connection diagnosis")
    print(f"  Local IP : {args.local_ip}")
    print(f"  Device IP: {args.device_ip}")
    print(
        f"  Geometry : offset={args.origin_offset:g} m, "
        f"probing={args.probing_length:g} m, spacing={cfg.channel_spacing_m:.9f} m"
    )
    print(
        f"  Clock    : {cfg.clock_period_ns:g} ns, "
        f"period={cfg.impulse_period_ticks()} ticks, "
        f"duration={cfg.impulse_duration_ticks()} ticks"
    )
    print(f"  Channels : {n_channels}")
    print()

    adapter_ips = list_ipconfig_ipv4()
    if adapter_ips:
        print_step("PC IPv4 adapters", True, ", ".join(adapter_ips))
    else:
        print_step("PC IPv4 adapters", False, "could not read ipconfig output")

    local_assigned = args.local_ip in adapter_ips or args.local_ip == "0.0.0.0"
    if local_assigned:
        print_step("Local IP selected", True, f"{args.local_ip} is available on this PC")
    else:
        print_step(
            "Local IP selected",
            False,
            f"{args.local_ip} is not in the PC IPv4 list. Set the adapter to this IP or update the app Local IP.",
        )

    for port in (DunayCommandClient.RESPONSE_PORT, DunayCommandClient.PHASE_PORT):
        ok, detail = can_bind(args.local_ip, port)
        print_step(f"UDP bind {port}", ok, detail)

    ok, detail = route_to_device(args.local_ip, args.device_ip)
    print_step("Route to device", ok, detail)

    if args.send_commands:
        print()
        print("Sending the captured Dunay initialization and phase-start sequence...")
        ok, detail = run_connect_test(
            args.local_ip,
            args.device_ip,
            cfg,
            args.seconds,
        )
        print_step("Dunay initialization/start test", ok, detail)
    else:
        print()
        print("Real commands were not sent. Add --send-commands to test the device response and phase stream.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
