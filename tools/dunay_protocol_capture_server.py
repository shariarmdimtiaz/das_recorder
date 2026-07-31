from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path


COMMAND_PORT = 8201
RESPONSE_PORT = 8211


COMMAND_NAMES = {
    0x00000003: "ReqDeviceStop",
    0x00000006: "ReqDeviceSetParams/commit?",
    0x00000007: "ReqDeviceSetParams",
    0x00000008: "LegacySetReceiverIP",
    0x00000017: "ReqDeviceVersion",
    0x00000100: "ReqDeviceSetup",
    0x00000308: "ReqDeviceModePhase",
    0x00000309: "ReqDeviceModePhaseFiltered",
    0x00000011: "ReqDeviceModeRaw",
}


def _hex(data: bytes) -> str:
    return data.hex(" ")


def _decode_command(packet: bytes) -> dict:
    if len(packet) < 4:
        return {
            "command_be": None,
            "command_le": None,
            "name_be": "short-packet",
            "name_le": "short-packet",
        }

    command_be = int.from_bytes(packet[:4], "big", signed=False)
    command_le = int.from_bytes(packet[:4], "little", signed=False)
    return {
        "command_be": f"0x{command_be:08X}",
        "command_le": f"0x{command_le:08X}",
        "name_be": COMMAND_NAMES.get(command_be, "unknown"),
        "name_le": COMMAND_NAMES.get(command_le, "unknown"),
    }


def _ack_for_packet(packet: bytes) -> list[bytes]:
    """Return plausible Dunay supported-command ACKs for protocol capture only."""
    if len(packet) < 4:
        return []

    command_be = int.from_bytes(packet[:4], "big", signed=False)
    command_le = int.from_bytes(packet[:4], "little", signed=False)
    responses = []

    # Big-endian wire order observed in the vendor executable:
    # command 00 00 03 08 -> ack F0 01 03 08.
    responses.append((0xF0010000 | (command_be & 0xFFFF)).to_bytes(4, "big"))

    # Legacy/little-endian alternative, useful if the vendor sends little-endian
    # command bytes in a different build.
    responses.append((0xF0010000 | (command_le & 0xFFFF)).to_bytes(4, "little"))

    # Preserve order but remove duplicates.
    unique = []
    for response in responses:
        if response not in unique:
            unique.append(response)
    return unique


def run_capture(bind_ip: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_ip, COMMAND_PORT))
    sock.settimeout(0.5)

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"Dunay protocol capture server listening on {bind_ip}:{COMMAND_PORT}")
    print(f"Writing JSONL capture to {output_path}")
    print("Use only with vendor UI Device IP pointed to this PC/localhost, not to the real DAS.")

    event_count = 0
    with output_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps({"event": "start", "time": started_at, "bind_ip": bind_ip}) + "\n")
        while True:
            try:
                packet, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                break

            event_count += 1
            decoded = _decode_command(packet)
            event = {
                "event": "udp_command",
                "index": event_count,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "from": f"{addr[0]}:{addr[1]}",
                "length": len(packet),
                "hex": _hex(packet),
                **decoded,
            }
            fp.write(json.dumps(event, ensure_ascii=False) + "\n")
            fp.flush()
            print(
                f"#{event_count} from {addr[0]}:{addr[1]} len={len(packet)} "
                f"be={decoded['command_be']} {decoded['name_be']} "
                f"le={decoded['command_le']} {decoded['name_le']}"
            )

            for response in _ack_for_packet(packet):
                for target_port in {addr[1], RESPONSE_PORT}:
                    sock.sendto(response, (addr[0], target_port))
                fp.write(
                    json.dumps(
                        {
                            "event": "udp_ack_sent",
                            "for_index": event_count,
                            "to": f"{addr[0]}:{RESPONSE_PORT}",
                            "hex": _hex(response),
                        }
                    )
                    + "\n"
                )

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture the vendor Dunay recorder Connect protocol without sending "
            "anything to the real DAS device."
        )
    )
    parser.add_argument("--bind-ip", default="127.0.0.1", help="IP to listen on, default: 127.0.0.1")
    parser.add_argument(
        "--output",
        default="recordings/dunay_vendor_protocol_capture.jsonl",
        help="JSONL output file",
    )
    args = parser.parse_args()
    run_capture(args.bind_ip, Path(args.output))


if __name__ == "__main__":
    main()
