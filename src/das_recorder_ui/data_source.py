from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generator, Optional, Tuple

import numpy as np

from .config import (
    DEFAULT_CLOCK_PERIOD_NS,
    impulse_duration_clock_ticks,
    impulse_period_clock_ticks,
    phase_output_sample_rate_hz,
)


@dataclass
class DataBlock:
    """One DAS data block.

    data shape must be (n_channels, n_samples).
    timestamp is Unix time at the beginning of this block.
    """

    data: np.ndarray
    timestamp: float
    sample_rate_hz: float
    source_line_start: int = 0
    source_line_end: int = 0
    compact_stream: bool = False
    received_channel_count: int = 0
    active_channel_count: int = 0


class DunayConnectionError(RuntimeError):
    """Raised when the Dunay device does not accept or produce network data."""


class DunayCommandRejected(DunayConnectionError):
    """Raised when the device explicitly rejects a supported command packet."""

    def __init__(self, command_code: int):
        self.command_code = int(command_code)
        super().__init__(f"Device rejected command 0x{self.command_code:08X}")


class DunayCommandClient:
    """Small UDP command client for the Dunay/Sava device control protocol.

    According to the device API PDF:
    * PC sends control commands to device UDP port 8201.
    * PC receives command responses on UDP port 8211.
    * PC receives phase data on UDP port 8227.
    """

    COMMAND_PORT = 8201
    RESPONSE_PORT = 8211
    PHASE_PORT = 8227

    STOP_TRANSFER = 0x00000003
    READ_ACQUISITION_SETUP = 0x00000006
    WRITE_ACQUISITION_SETUP = 0x00000007
    SET_RECEIVER_IP = 0x00000008
    DEVICE_STATUS = 0x00000017
    INITIALIZE_DEVICE = 0x00000100
    TRANSFER_PHASE = 0x00000308
    TRANSFER_PHASE_FILTERS = 0x00000309

    MODE_TO_COMMAND = {
        "phase": TRANSFER_PHASE,
        "phase_filters": TRANSFER_PHASE_FILTERS,
    }

    RESPONSE_SUPPORTED_PREFIX = 0xF0010000
    RESPONSE_UNSUPPORTED_PREFIX = 0xF0020000
    ACQUISITION_SETUP_RESPONSE = 0x00010006

    # Captured from the vendor application on 2026-07-24. The device accepts
    # this 66-byte profile before it accepts a phase-transfer command.
    VENDOR_ACQUISITION_SETUP = bytes.fromhex(
        "00 01 04 6b 00 00 00 14 00 00 27 10 00 00 00 00 "
        "00 00 00 00 00 00 00 63 00 00 00 01 00 00 00 01 "
        "00 00 00 04 00 00 00 00 00 00 00 00 00 80 00 00 "
        "00 00 00 01 00 00 00 53 00 00 00 01 00 00 00 01 "
        "00 00"
    )

    def __init__(
        self,
        device_ip: str,
        local_ip: str,
        n_channels: int,
        sample_rate_hz: float = 2000.0,
        impulse_duration_ns: int = 200,
        probing_length_m: float = 50.0,
        clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
        command_timeout_s: float = 1.0,
        phase_timeout_s: float = 8.0,
    ):
        self.device_ip = str(device_ip).strip()
        self.local_ip = str(local_ip).strip() or "0.0.0.0"
        self.n_channels = int(n_channels)
        self.sample_rate_hz = float(sample_rate_hz)
        self.impulse_duration_ns = int(impulse_duration_ns)
        self.probing_length_m = float(probing_length_m)
        self.clock_period_ns = float(clock_period_ns)
        self.command_timeout_s = float(command_timeout_s)
        self.phase_timeout_s = float(phase_timeout_s)

    def acquisition_setup_payload(self) -> bytes:
        """Build the vendor setup with clock-derived timing and probing length."""
        payload = bytearray(self.VENDOR_ACQUISITION_SETUP)
        struct.pack_into(
            ">I",
            payload,
            0,
            impulse_period_clock_ticks(
                self.sample_rate_hz,
                self.clock_period_ns,
            ),
        )
        struct.pack_into(
            ">I",
            payload,
            4,
            impulse_duration_clock_ticks(
                self.impulse_duration_ns,
                self.clock_period_ns,
            ),
        )
        # The captured 100 m vendor setup stores 99 in word 5.
        endpoint_m = max(0, int(round(self.probing_length_m)) - 1)
        struct.pack_into(">I", payload, 20, endpoint_m)
        return bytes(payload)

    def acquisition_setup_summary(self) -> str:
        return (
            f"clock={self.clock_period_ns:g} ns, "
            f"period={impulse_period_clock_ticks(self.sample_rate_hz, self.clock_period_ns)} ticks, "
            f"duration={impulse_duration_clock_ticks(self.impulse_duration_ns, self.clock_period_ns)} ticks, "
            f"endpoint={max(0, int(round(self.probing_length_m)) - 1)} m"
        )

    def _effective_local_ip(self) -> str:
        if self.local_ip and self.local_ip != "0.0.0.0":
            return self.local_ip

        if not self.device_ip:
            raise DunayConnectionError("Device IP is empty")

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((self.device_ip, self.COMMAND_PORT))
            return probe.getsockname()[0]
        finally:
            probe.close()

    def _open_socket(self, port: int) -> socket.socket:
        candidates = ["0.0.0.0"]
        if self.local_ip and self.local_ip not in candidates:
            candidates.append(self.local_ip)

        last_error: Optional[OSError] = None
        for bind_ip in candidates:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(self.command_timeout_s)
            try:
                sock.bind((bind_ip, port))
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()

        raise DunayConnectionError(f"Could not bind UDP {port}: {last_error}")

    @staticmethod
    def _pack_command(command_code: int, payload: bytes = b"", endian: str = "<") -> bytes:
        return struct.pack(f"{endian}I", int(command_code)) + payload

    @staticmethod
    def _parse_response_code(packet: bytes) -> Optional[int]:
        if len(packet) < 4:
            return None

        little = struct.unpack_from("<I", packet, 0)[0]
        if (little & 0xFFFF0000) in (
            DunayCommandClient.RESPONSE_SUPPORTED_PREFIX,
            DunayCommandClient.RESPONSE_UNSUPPORTED_PREFIX,
        ):
            return little

        big = struct.unpack_from(">I", packet, 0)[0]
        if (big & 0xFFFF0000) in (
            DunayCommandClient.RESPONSE_SUPPORTED_PREFIX,
            DunayCommandClient.RESPONSE_UNSUPPORTED_PREFIX,
        ):
            return big

        return None

    def _send_command(
        self,
        sock: socket.socket,
        command_code: int,
        payload: bytes = b"",
        require_ack: bool = True,
        endian: str = "<",
    ) -> Optional[int]:
        if not self.device_ip:
            raise DunayConnectionError("Device IP is empty")

        packet = self._pack_command(command_code, payload, endian)
        sock.sendto(packet, (self.device_ip, self.COMMAND_PORT))

        expected_ok = self.RESPONSE_SUPPORTED_PREFIX | (int(command_code) & 0xFFFF)
        expected_bad = self.RESPONSE_UNSUPPORTED_PREFIX | (int(command_code) & 0xFFFF)
        deadline = time.time() + self.command_timeout_s

        while time.time() < deadline:
            try:
                response, addr = sock.recvfrom(2048)
            except socket.timeout:
                break

            if self.device_ip and addr[0] != self.device_ip:
                continue
            code = self._parse_response_code(response)
            if code is None:
                continue
            if code == expected_ok:
                return code
            if code == expected_bad:
                raise DunayCommandRejected(command_code)

        if require_ack:
            raise DunayConnectionError(f"No response for command 0x{command_code:08X} on UDP {self.RESPONSE_PORT}")
        return None

    def _read_acquisition_setup(self, sock: socket.socket, endian: str = ">") -> int:
        """Request the vendor setup readback and return its response size."""
        packet = self._pack_command(self.READ_ACQUISITION_SETUP, endian=endian)
        sock.sendto(packet, (self.device_ip, self.COMMAND_PORT))
        deadline = time.time() + self.command_timeout_s

        while time.time() < deadline:
            try:
                response, addr = sock.recvfrom(65535)
            except socket.timeout:
                break

            if self.device_ip and addr[0] != self.device_ip:
                continue
            if len(response) < 4:
                continue

            response_code = int.from_bytes(response[:4], "big")
            if response_code == self.ACQUISITION_SETUP_RESPONSE:
                return len(response)

            parsed_code = self._parse_response_code(response)
            expected_bad = self.RESPONSE_UNSUPPORTED_PREFIX | (self.READ_ACQUISITION_SETUP & 0xFFFF)
            if parsed_code == expected_bad:
                raise DunayCommandRejected(self.READ_ACQUISITION_SETUP)

        raise DunayConnectionError(
            f"No setup readback for command 0x{self.READ_ACQUISITION_SETUP:08X} "
            f"on UDP {self.RESPONSE_PORT}"
        )

    def initialize_and_request_phase_stream(self, mode: str = "phase") -> str:
        """Replay the vendor startup handshake captured from the old application."""
        command_mode = str(mode or "phase").strip().lower()
        transfer_command = self.MODE_TO_COMMAND.get(command_mode, self.TRANSFER_PHASE)
        response_sock = self._open_socket(self.RESPONSE_PORT)
        try:
            self._send_command(response_sock, self.STOP_TRANSFER, endian=">")
            self._send_command(response_sock, self.DEVICE_STATUS, endian=">")
            initialization_status = "accepted"
            try:
                self._send_command(response_sock, self.INITIALIZE_DEVICE, endian=">")
            except DunayCommandRejected:
                # Firmware rejects this one-time initialization command after a
                # vendor setup has already been applied. The parameter write and
                # transfer commands remain valid in that state.
                initialization_status = "already initialized"

            # The vendor application waits for device initialization, then polls
            # status again before writing and reading back acquisition parameters.
            time.sleep(2.0)
            self._send_command(response_sock, self.DEVICE_STATUS, endian=">")
            self._send_command(
                response_sock,
                self.WRITE_ACQUISITION_SETUP,
                payload=self.acquisition_setup_payload(),
                endian=">",
            )
            readback_size = self._read_acquisition_setup(response_sock, endian=">")
            response = self._send_command(response_sock, transfer_command, endian=">")
            return (
                f"Vendor initialization {initialization_status}; setup readback={readback_size} bytes; "
                f"{self.acquisition_setup_summary()}; "
                f"stream-start=0x{transfer_command:08X}, response=0x{int(response):08X}"
            )
        finally:
            response_sock.close()

    def apply_acquisition_setup_and_restart(self, mode: str = "phase") -> str:
        """Apply changed acquisition timing/length while the receiver stays open."""
        command_mode = str(mode or "phase").strip().lower()
        transfer_command = self.MODE_TO_COMMAND.get(command_mode, self.TRANSFER_PHASE)
        response_sock = self._open_socket(self.RESPONSE_PORT)
        try:
            self._send_command(response_sock, self.STOP_TRANSFER, endian=">")
            self._send_command(
                response_sock,
                self.WRITE_ACQUISITION_SETUP,
                payload=self.acquisition_setup_payload(),
                endian=">",
            )
            readback_size = self._read_acquisition_setup(response_sock, endian=">")
            response = self._send_command(response_sock, transfer_command, endian=">")
            return (
                f"Acquisition setup updated; setup readback={readback_size} bytes; "
                f"{self.acquisition_setup_summary()}; "
                f"stream-start=0x{transfer_command:08X}, response=0x{int(response):08X}"
            )
        finally:
            response_sock.close()

    def connect_phase_stream(self, wait_for_phase: bool = True) -> str:
        """Configure the device to send phase packets to this PC and verify data."""
        last_error: Optional[Exception] = None
        attempts = (
            ("<", False),
            ("<", True),
            (">", False),
            (">", True),
        )
        for endian, reverse_receiver_ip in attempts:
            try:
                return self._connect_phase_stream_once(endian, reverse_receiver_ip, wait_for_phase)
            except DunayConnectionError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise DunayConnectionError("Dunay connection failed")

    def _connect_phase_stream_once(self, endian: str, reverse_receiver_ip: bool, wait_for_phase: bool) -> str:
        local_ip = self._effective_local_ip()
        receiver_ip = socket.inet_aton(local_ip)
        if reverse_receiver_ip:
            receiver_ip = receiver_ip[::-1]

        response_sock = self._open_socket(self.RESPONSE_PORT)
        phase_sock: Optional[socket.socket] = None
        try:
            if wait_for_phase:
                phase_sock = self._open_socket(self.PHASE_PORT)
                phase_sock.settimeout(0.25)

            # The PDF requires a stop command before changing control modes.
            self._send_command(response_sock, self.STOP_TRANSFER, require_ack=False, endian=endian)
            self._send_command(response_sock, self.SET_RECEIVER_IP, receiver_ip, require_ack=True, endian=endian)
            self._send_command(response_sock, self.TRANSFER_PHASE, require_ack=True, endian=endian)

            if not wait_for_phase:
                return f"Commands accepted; phase stream requested for {local_ip}:{self.PHASE_PORT}"

            assert phase_sock is not None
            deadline = time.time() + self.phase_timeout_s
            packet_count = 0
            last_packet_summary = ""
            while time.time() < deadline:
                try:
                    packet, addr = phase_sock.recvfrom(4096)
                except socket.timeout:
                    continue

                if self.device_ip and addr[0] != self.device_ip:
                    continue
                packet_count += 1
                last_packet_summary = f"{len(packet)} bytes from {addr[0]}:{addr[1]}"
                if DunayNetworkSource.parse_phase_packet(packet, self.n_channels) is not None:
                    ip_order = "reversed IP bytes" if reverse_receiver_ip else "normal IP bytes"
                    cmd_order = "little-endian" if endian == "<" else "big-endian"
                    return (
                        f"Connected: phase packets receiving from {addr[0]}:{addr[1]} "
                        f"({cmd_order}, {ip_order})"
                    )

            if packet_count:
                raise DunayConnectionError(
                    f"UDP {self.PHASE_PORT} received {packet_count} packet(s), but none matched the Dunay phase format. "
                    f"Last packet: {last_packet_summary}"
                )

            ip_order = "reversed IP bytes" if reverse_receiver_ip else "normal IP bytes"
            cmd_order = "little-endian" if endian == "<" else "big-endian"
            raise DunayConnectionError(
                f"No phase packet received on UDP {self.PHASE_PORT} after accepted commands "
                f"({cmd_order}, {ip_order})"
            )
        finally:
            if phase_sock is not None:
                phase_sock.close()
            response_sock.close()

    def request_phase_stream_once(self, mode: str = "phase", endian: str = ">") -> str:
        """Send one vendor-style stream-start request.

        This is intentionally narrower than connect_phase_stream(): it does not
        send STOP, receiver-IP setup, parameter writes, or endian/IP retries.
        Use it only after the receive socket is already listening.
        """
        command_mode = str(mode or "phase").strip().lower()
        command_code = self.MODE_TO_COMMAND.get(command_mode, self.TRANSFER_PHASE)
        response_sock = self._open_socket(self.RESPONSE_PORT)
        try:
            response = self._send_command(
                response_sock,
                command_code,
                payload=b"",
                require_ack=True,
                endian=endian,
            )
            return f"Stream-start command accepted: 0x{command_code:08X}, response=0x{int(response):08X}"
        finally:
            response_sock.close()


class DunayNetworkSource:
    """UDP phase-packet receiver for Dunay DAS.

    The earlier project raised a placeholder error here. This implementation
    listens for Dunay phase packets on UDP port 8227 and reconstructs the normal
    recorder matrix:

        data shape = (n_channels, chunk_samples)

    Packet layout used here follows the Dunay API document for "Transfer Phase"
    / "Phase and Filters" packets:

        uint32 line_point_number
        uint32 block_number
        uint32 radius_vector_component
        int16[500] differential phase samples
        uint16[24] envelope 60-150 Hz   optional / ignored here
        uint16[24] envelope 20-40 Hz    optional / ignored here
        uint16[24] envelope 4-10 Hz     optional / ignored here

    Notes
    -----
    * The receive socket opens before the captured vendor initialization
      sequence requests phase streaming to this PC.
    * If binding to the selected Local IP fails, the class falls back to 0.0.0.0.
    * Both little- and big-endian headers are tested; the plausible one is used.
    """

    PHASE_PORT = 8227
    HEADER_BYTES = 12
    PHASE_SAMPLES = 500
    MIN_PHASE_PACKET_BYTES = HEADER_BYTES + PHASE_SAMPLES * 2

    def __init__(
        self,
        device_ip: str,
        local_ip: str,
        n_channels: int,
        sample_rate_hz: float,
        chunk_samples: int,
        line_point_start: Optional[int] = 73,
        device_control_enabled: bool = False,
        device_command_mode: str = "phase",
        status_callback: Optional[Callable[[str], None]] = None,
        diagnostics_path: str = "",
        impulse_duration_ns: int = 200,
        probing_length_m: float = 50.0,
        clock_period_ns: float = DEFAULT_CLOCK_PERIOD_NS,
    ):
        self.device_ip = str(device_ip).strip()
        self.local_ip = str(local_ip).strip() or "0.0.0.0"
        self.n_channels = int(n_channels)
        self.sample_rate_hz = float(sample_rate_hz)
        self.chunk_samples = int(chunk_samples)
        self.line_point_start = None if line_point_start is None else int(line_point_start)
        self.device_control_enabled = bool(device_control_enabled)
        self.device_command_mode = str(device_command_mode or "phase")
        self.status_callback = status_callback
        self.diagnostics_path = Path(diagnostics_path) if diagnostics_path else None
        self.impulse_duration_ns = int(impulse_duration_ns)
        self.probing_length_m = float(probing_length_m)
        self.clock_period_ns = float(clock_period_ns)
        self._running = False
        self._acquisition_update_requested = False
        self._line_number_base: Optional[int] = 1  # Dunay/KIGAM files often use RecordStartPoint=1.
        self._compact_zero_based_stream = False
        self.raw_packet_count = 0
        self.phase_packet_count = 0
        self.parse_error_count = 0
        self.unmapped_packet_count = 0
        self.source_mismatch_count = 0
        self.block_emit_count = 0
        self.max_received_channel_count = 0
        self.last_packet_summary = "no packets"
        self.last_phase_summary = "no phase packets"
        self.stream_request_status = ""
        self._last_status_time = 0.0
        self._diag_file = None

    def stop(self) -> None:
        self._running = False

    def request_acquisition_update(
        self,
        *,
        n_channels: int,
        probing_length_m: float,
        sample_rate_hz: float,
        impulse_duration_ns: int,
        clock_period_ns: float,
    ) -> None:
        """Queue a setup refresh for the receive loop's worker thread."""
        self.n_channels = max(1, int(n_channels))
        self.probing_length_m = float(probing_length_m)
        self.sample_rate_hz = float(sample_rate_hz)
        self.impulse_duration_ns = int(impulse_duration_ns)
        self.clock_period_ns = float(clock_period_ns)
        self._acquisition_update_requested = bool(self.device_control_enabled)

    def diagnostics_summary(self) -> dict:
        return {
            "udp8227_raw_packets": self.raw_packet_count,
            "phase_packets_parsed": self.phase_packet_count,
            "parse_failures": self.parse_error_count,
            "unmapped_phase_packets": self.unmapped_packet_count,
            "source_mismatch_packets": self.source_mismatch_count,
            "blocks_emitted": self.block_emit_count,
            "calculated_channels": self.n_channels,
            "max_received_channels": self.max_received_channel_count,
            "last_packet": self.last_packet_summary,
            "last_phase": self.last_phase_summary,
            "stream_request_status": self.stream_request_status,
            "line_point_start": self.line_point_start,
            "n_channels": self.n_channels,
        }

    def _emit_status(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_status_time < 1.0:
            return
        self._last_status_time = now
        packet_status = (
            f"UDP8227 raw={self.raw_packet_count}, phase={self.phase_packet_count}, "
            f"parse_fail={self.parse_error_count}, unmapped={self.unmapped_packet_count}, "
            f"blocks={self.block_emit_count}; {self.last_packet_summary}"
        )
        message = (
            f"{self.stream_request_status}; {packet_status}"
            if self.stream_request_status
            else packet_status
        )
        if self.status_callback is not None:
            self.status_callback(message)
        self._log(message)

    def _log(self, message: str) -> None:
        if self._diag_file is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._diag_file.write(f"[{stamp}] {message}\n")
        self._diag_file.flush()

    def _open_diagnostics(self) -> None:
        if self.diagnostics_path is None:
            return
        self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        self._diag_file = self.diagnostics_path.open("a", encoding="utf-8")
        self._log("DAS Recorder v1.3.2 Dunay receiver diagnostics started")
        self._log(
            f"device_ip={self.device_ip or '(any)'}, local_ip={self.local_ip}, "
            f"n_channels={self.n_channels}, chunk_samples={self.chunk_samples}, "
            f"line_point_start={self.line_point_start}, "
            f"phase_sample_rate_hz={phase_output_sample_rate_hz(self.sample_rate_hz):g}"
        )

    def _close_diagnostics(self) -> None:
        if self._diag_file is not None:
            self._log(f"summary={self.diagnostics_summary()}")
            self._diag_file.close()
            self._diag_file = None

    def _line_to_index(self, line_point_no: int) -> Optional[int]:
        """Convert Dunay line_point_no to zero-based row index.

        v1.0.1 showed that the field device can send absolute line_point_no
        values such as 0..19599 while the KIGAM-style H5 output keeps only a
        1457-channel window. For that case, line_point_start defines the first
        absolute line point saved as row 0. The reference KIGAM file uses
        LineOffset=73, so the current default line_point_start is 73.
        """
        line_point_no = int(line_point_no)

        # Field devices can stream a compact active range as 0..N instead of
        # absolute KIGAM line points such as 73..1529. Once line 0 appears,
        # treat this run as compact zero-based data so the display/save path
        # does not discard lines 0..72.
        if line_point_no == 0:
            self._compact_zero_based_stream = True

        if self._compact_zero_based_stream:
            if 0 <= line_point_no < self.n_channels:
                return line_point_no
            self.unmapped_packet_count += 1
            return None

        if self.line_point_start is not None:
            idx = line_point_no - int(self.line_point_start)
            if 0 <= idx < self.n_channels:
                return idx
            self.unmapped_packet_count += 1
            return None

        # If the device ever sends line 0, switch to zero-based indexing.
        if line_point_no == 0:
            self._line_number_base = 0

        if self._line_number_base == 0:
            idx = line_point_no
        else:
            idx = line_point_no - 1

        if 0 <= idx < self.n_channels:
            return idx

        self.unmapped_packet_count += 1
        return None

    def _parse_phase_packet(self, packet: bytes) -> Optional[Tuple[int, int, np.ndarray]]:
        """Return (line_point_no, block_no, samples) or None if packet is not phase data."""
        return self.parse_phase_packet(packet, self.n_channels)

    @staticmethod
    def parse_phase_packet(packet: bytes, n_channels: int) -> Optional[Tuple[int, int, np.ndarray]]:
        """Return (line_point_no, block_no, samples) or None if packet is not phase data."""
        if len(packet) < DunayNetworkSource.MIN_PHASE_PACKET_BYTES:
            return None

        # Try both endiannesses.
        #
        # Important compatibility fix for v1.1.0:
        # Some Dunay/KIGAM files use point indices near 1..N, while some firmware
        # variants may report a metric/trace coordinate instead of a compact
        # zero-based channel index. v1.0.1 worked because it saved raw packets
        # without rejecting them. Therefore, v1.1.0 must not reject a real phase
        # packet only because line_point_no is larger than n_channels.
        candidates = []
        max_reasonable_line = max(int(n_channels) * 20, 100000)
        for endian in ("<", ">"):
            try:
                line_point_no, block_no, _radius = struct.unpack_from(f"{endian}III", packet, 0)
            except struct.error:
                continue

            compact_index = (0 <= line_point_no < n_channels) or (1 <= line_point_no <= n_channels)
            reasonable_coordinate = 0 <= line_point_no <= max_reasonable_line
            if not (compact_index or reasonable_coordinate):
                continue

            sample_dtype = np.dtype(f"{endian}i2")
            samples = np.frombuffer(
                packet,
                dtype=sample_dtype,
                count=DunayNetworkSource.PHASE_SAMPLES,
                offset=DunayNetworkSource.HEADER_BYTES,
            ).astype(np.int16, copy=False)

            # A line number of zero is valid in both byte orders, so using only
            # the line number makes the first packet ambiguous. In that case,
            # the real block counter is normally far smaller than its
            # byte-swapped interpretation. Keep little-endian as the final
            # tie-breaker because that is what the field device uses.
            score = (
                0 if compact_index else 1,
                0 if block_no <= 0x00FFFFFF else 1,
                int(block_no),
                0 if endian == "<" else 1,
            )
            candidates.append((score, line_point_no, block_no, samples))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        _score, line_point_no, block_no, samples = candidates[0]
        return line_point_no, block_no, samples

    def _open_socket(self) -> socket.socket:
        # v1.0.1 listened on 0.0.0.0, which is more tolerant of adapter/IP
        # mismatch than binding only to the UI Local IP. Try that first.
        candidates = ["0.0.0.0"]
        if self.local_ip and self.local_ip not in candidates:
            candidates.append(self.local_ip)

        last_error: Optional[OSError] = None
        for bind_ip in candidates:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
            except OSError:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
                except OSError:
                    pass
            try:
                sock.bind((bind_ip, self.PHASE_PORT))
                sock.settimeout(0.20)
                self._log(f"Listening for phase packets on {bind_ip}:{self.PHASE_PORT}")
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()

        raise DunayConnectionError(f"Could not bind UDP {self.PHASE_PORT}: {last_error}")

    def blocks(self) -> Generator[DataBlock, None, None]:
        """Listen for Dunay UDP phase packets and yield reconstructed DAS blocks."""
        self._running = True
        self._open_diagnostics()
        sock = self._open_socket()

        if self.device_control_enabled and self.device_ip and self.device_ip not in ("0.0.0.0", "127.0.0.1"):
            try:
                message = DunayCommandClient(
                    self.device_ip,
                    self.local_ip,
                    self.n_channels,
                    sample_rate_hz=self.sample_rate_hz,
                    impulse_duration_ns=self.impulse_duration_ns,
                    probing_length_m=self.probing_length_m,
                    clock_period_ns=self.clock_period_ns,
                    command_timeout_s=1.0,
                    phase_timeout_s=1.0,
                ).initialize_and_request_phase_stream(mode=self.device_command_mode)
                self.stream_request_status = message
                self._log(f"Command request result: {message}")
                if self.status_callback is not None:
                    self.status_callback(message)
                self._emit_status(force=True)
            except Exception as exc:
                self.stream_request_status = (
                    f"Dunay stream request failed: {exc}; listening for external stream"
                )
                self._log(self.stream_request_status)
                if self.status_callback is not None:
                    self.status_callback(self.stream_request_status)
                self._emit_status(force=True)
        else:
            self._log("Listen-only mode: no Dunay control command sent")
            self._emit_status(force=True)

        current_block_no: Optional[int] = None
        emitted_current_block = False
        block_timestamp = time.time()
        matrix = np.zeros((self.n_channels, self.chunk_samples), dtype=np.int16)
        received = np.zeros(self.n_channels, dtype=bool)
        received_line_points = set()
        active = np.zeros(self.n_channels, dtype=bool)
        last_packet_time = time.time()

        def reset_block(new_block_no: Optional[int] = None) -> None:
            nonlocal current_block_no, emitted_current_block, block_timestamp
            nonlocal matrix, received, received_line_points, active
            current_block_no = new_block_no
            emitted_current_block = False
            block_timestamp = time.time()
            matrix = np.zeros((self.n_channels, self.chunk_samples), dtype=np.int16)
            received = np.zeros(self.n_channels, dtype=bool)
            received_line_points = set()
            active = np.zeros(self.n_channels, dtype=bool)

        def make_output_block() -> DataBlock:
            active_rows = np.flatnonzero(received)
            received_count = len(received_line_points)
            self.max_received_channel_count = max(
                self.max_received_channel_count,
                received_count,
            )
            if active_rows.size:
                row_start = int(active_rows[0])
                row_stop = int(active_rows[-1]) + 1
            else:
                row_start = 0
                row_stop = self.n_channels

            compact_limit = max(256, int(round(self.n_channels * 0.35)))
            compact_stream = (
                self._compact_zero_based_stream
                and row_start == 0
                and row_stop != self.n_channels
                and active_rows.size <= compact_limit
            )
            if compact_stream:
                data = matrix[row_start:row_stop, :]
                source_start = row_start
                source_end = row_stop - 1
            else:
                data = matrix
                source_start = 0
                source_end = self.n_channels - 1

            return DataBlock(
                data=data.copy(),
                timestamp=block_timestamp,
                sample_rate_hz=phase_output_sample_rate_hz(self.sample_rate_hz),
                source_line_start=source_start,
                source_line_end=source_end,
                compact_stream=compact_stream,
                received_channel_count=received_count,
                active_channel_count=int(np.count_nonzero(active)),
            )

        try:
            while self._running:
                if self._acquisition_update_requested:
                    self._acquisition_update_requested = False
                    try:
                        message = DunayCommandClient(
                            self.device_ip,
                            self.local_ip,
                            self.n_channels,
                            sample_rate_hz=self.sample_rate_hz,
                            impulse_duration_ns=self.impulse_duration_ns,
                            probing_length_m=self.probing_length_m,
                            clock_period_ns=self.clock_period_ns,
                            command_timeout_s=1.0,
                            phase_timeout_s=1.0,
                        ).apply_acquisition_setup_and_restart(
                            mode=self.device_command_mode
                        )
                        self.stream_request_status = message
                        self._log(message)
                        if self.status_callback is not None:
                            self.status_callback(message)
                    except Exception as exc:
                        message = f"Acquisition setup update failed: {exc}"
                        self.stream_request_status = message
                        self._log(message)
                        if self.status_callback is not None:
                            self.status_callback(message)

                try:
                    packet, addr = sock.recvfrom(4096)
                except socket.timeout:
                    # If only part of a block arrived and then packets paused, flush it
                    # instead of freezing the UI forever.
                    if current_block_no is not None and received.any() and not emitted_current_block:
                        if time.time() - last_packet_time > 0.75:
                            self.block_emit_count += 1
                            self._emit_status(force=True)
                            yield make_output_block()
                            reset_block(None)
                    self._emit_status()
                    continue

                self.raw_packet_count += 1
                self.last_packet_summary = f"{len(packet)} bytes from {addr[0]}:{addr[1]}"
                if self.raw_packet_count == 1:
                    self._log(f"First UDP 8227 packet: {self.last_packet_summary}; hex={packet[:32].hex(' ')}")

                # v1.0.1 did not discard data only because the source IP differed.
                # Count this for diagnostics but still try to parse the packet.
                if self.device_ip and self.device_ip not in ("0.0.0.0", "127.0.0.1"):
                    if addr[0] != self.device_ip:
                        self.source_mismatch_count += 1

                parsed = self._parse_phase_packet(packet)
                if parsed is None:
                    # Unknown packet type or non-phase packet. Ignore safely.
                    self.parse_error_count += 1
                    self._emit_status()
                    continue

                line_point_no, block_no, samples = parsed
                self.phase_packet_count += 1
                last_packet_time = time.time()
                self.last_phase_summary = f"line_point_no={line_point_no}, block_no={block_no}"
                if self.phase_packet_count == 1:
                    self._log(
                        f"First parsed phase packet: {self.last_phase_summary}, "
                        f"sample_min={int(samples.min())}, sample_max={int(samples.max())}"
                    )

                if current_block_no is None:
                    reset_block(block_no)

                # A new block number means the previous block is complete enough to emit.
                if block_no != current_block_no:
                    if received.any() and not emitted_current_block:
                        self.block_emit_count += 1
                        self._emit_status(force=True)
                        yield make_output_block()
                    reset_block(block_no)

                if emitted_current_block:
                    self._emit_status()
                    continue

                # Count the device's actual line points before applying the
                # configured/calculated channel-range mapping. This keeps the
                # Received channels value independent of probing length.
                received_line_points.add(int(line_point_no))

                if self._compact_zero_based_stream and line_point_no >= 0:
                    idx = int(line_point_no)
                    if idx >= matrix.shape[0]:
                        dynamic_limit = max(10000, int(self.n_channels))
                        if idx >= dynamic_limit:
                            self.unmapped_packet_count += 1
                            self._emit_status()
                            continue
                        new_rows = min(
                            dynamic_limit,
                            max(idx + 1, matrix.shape[0] * 2),
                        )
                        matrix = np.pad(
                            matrix,
                            ((0, new_rows - matrix.shape[0]), (0, 0)),
                            mode="constant",
                        )
                        received = np.pad(
                            received,
                            (0, new_rows - received.shape[0]),
                            mode="constant",
                        )
                        active = np.pad(
                            active,
                            (0, new_rows - active.shape[0]),
                            mode="constant",
                        )
                else:
                    idx = self._line_to_index(line_point_no)
                if idx is None:
                    self._emit_status()
                    continue

                n = min(self.chunk_samples, int(samples.size))
                matrix[idx, :n] = samples[:n]
                if n < self.chunk_samples:
                    matrix[idx, n:] = 0
                received[idx] = True
                active[idx] = bool(np.any(samples[:n] != -15708))
                self._emit_status()

            # Stop requested: flush one final partial block if available.
            if current_block_no is not None and received.any() and not emitted_current_block:
                self.block_emit_count += 1
                self._emit_status(force=True)
                yield make_output_block()

        finally:
            sock.close()
            self._emit_status(force=True)
            self._close_diagnostics()
