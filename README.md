# Dunay DAS Recorder UI v1.3.2

This is a desktop prototype application with a UI layout similar to the uploaded 'DAS Recorder' screenshot:

# Used
- Python
- PyQt5
- Dunay device
- Optical fiber cable

## Features
- Connect area
- Start / Stop buttons
- Device IP / Local IP fields
- File size and free disk status
- Left sidebar sections: Mode, File, Impulse parameters, Data acquisition region, Fiber properties, Noise reduction
- Large live waterfall viewer
- 1-minute H5 / TDMS segmentation

## Important in v1.3.2

v1.3.2 restores the safer recording behavior while keeping the H5 structure.

The important workflow is:

- `Start` opens the UDP 8227 receiver first.
- The recorder then sends the captured Dunay initialization/setup sequence and
  requests the phase stream.
- If the device rejects or ignores the command request, recording continues in
  listen-only mode.
- `Connect` is no longer required before `Start`.
- The `Disconnect` state is shown only after real data blocks arrive.
- A diagnostic log and summary are written to the output folder's `log` subfolder.

This addresses the v1.1.x failure mode where command success/failure could
block recording before the long-lived UDP receiver was allowed to listen.

The program listens for Dunay phase data on UDP port `8227` and reconstructs a
matrix with shape:

```text
(n_channels, chunk_samples)
```

The current implementation records `Transfer Phase` packets. The optional
`Transfer Phase and Filters` command is implemented, but the default mode is
plain phase.

## Dunay network ports

All Dunay communication used by this application is UDP.

| Port | Direction | Endpoint | Work |
| --- | --- | --- | --- |
| `8201` | PC -> device | Device port | Receives control commands sent by DASRecorder. |
| `8211` | Device -> PC | PC local port | Receives command acknowledgements, rejections, and acquisition-setup readback data. |
| `8227` | Device -> PC | PC local port | Carries live phase packets used by the waterfall and H5/TDMS recorder. |

The application opens the phase receiver on `0.0.0.0:8227` first. This listens
on every PC network adapter and avoids losing the first data packets. The Local
IP field is still used for device control and network routing. The Device IP is
the destination for commands on port `8201` and is also used to identify the
expected data source.

## Dunay control commands

Control packets start with a 4-byte command value. The captured vendor startup
sequence sends these command values in big-endian byte order.

| Command | Name | Work in DASRecorder |
| --- | --- | --- |
| `0x00000003` | Stop transfer | Stops the current device stream before initialization or a mode change. |
| `0x00000017` | Device status | Checks that the device control service is responding. It is sent before and after initialization. |
| `0x00000100` | Initialize device | Performs the device initialization step. Some firmware rejects this command when the device is already initialized; DASRecorder accepts that condition and continues. |
| `0x00000007` | Write acquisition setup | Sends the 66-byte vendor acquisition profile with clock-derived pulse period, pulse duration, and probing endpoint. |
| `0x00000006` | Read acquisition setup | Reads the setup back from the device and confirms that a setup response was returned. |
| `0x00000308` | Transfer phase | Starts the phase stream received by the application on UDP `8227`. This is the default stream command. |
| `0x00000309` | Transfer phase and filters | Requests phase packets with filter-envelope data. It is available through `device_command_mode: "phase_filters"` but is not the default. |
| `0x00000008` | Set receiver IP | Sends the PC receiver IP in the alternative connection helper. It is implemented for protocol diagnostics but is not part of the active captured-vendor startup sequence. |

The active initialization sequence is:

```text
Open PC UDP 8227 receiver
    -> 0x00000003  Stop transfer
    -> 0x00000017  Read device status
    -> 0x00000100  Initialize device
    -> wait 2 seconds
    -> 0x00000017  Read device status again
    -> 0x00000007  Write captured acquisition setup
    -> 0x00000006  Read acquisition setup
    -> 0x00000308  Start phase transfer
    -> receive phase packets on PC UDP 8227
```

For normal command acknowledgements:

| Response pattern | Meaning |
| --- | --- |
| `0xF001xxxx` | Command accepted/supported. The final four hexadecimal digits match the command. |
| `0xF002xxxx` | Command rejected/unsupported. The final four hexadecimal digits match the command. |
| `0x00010006` | Acquisition-setup readback response for command `0x00000006`. |

The setup packet is based on the working vendor profile captured on 2026-07-24.
Three confirmed big-endian fields are updated before every initialization:

```text
word 0: ImpPeriod ticks = round((1e9 / clock_period_ns) / frequency_hz)
word 1: ImpDuration ticks = round(duration_ns / clock_period_ns)
word 5: probing endpoint = round(probing_length_m) - 1
```

The tested Dunay clock period is `10 ns`, or `100 MHz`. For example, a
`1000 Hz` repetition frequency produces `100000` ticks, and a `200 ns` pulse
produces `20` ticks. These are the values found in the original Dunay H5 file.
Changing probing length while connected and not recording queues this updated
setup and restarts phase transfer while the UDP 8227 receiver remains open.

Device API Table 6 defines a fixed phase decimation factor of `4`. The
configured frequency remains the laser pulse frequency used by `ImpPeriod`, but
the stored phase-sample rate and file-duration calculation use:

```text
phase_sample_rate_hz = frequency_hz / 4
samples_per_file = phase_sample_rate_hz * file_duration_s
```

At `2000 Hz`, the phase stream is therefore `500 samples/s`. A `60 s` H5/TDMS
segment closes after `30000` samples per channel, or `60` packets containing
`500` samples each. The H5 root attributes and TDMS root properties record the
effective phase rate and decimation factor while standard H5 `Frequency` and
`ImpPeriod` values remain tied to the configured pulse frequency.

For field diagnostics, check the output folder after each run:

```text
log\dunay_receiver_diagnostics_YYYYMMDD_HHMMSS.log
log\dunay_receiver_diagnostics_YYYYMMDD_HHMMSS.summary.json
```

The summary separates:

```text
udp8227_raw_packets
phase_packets_parsed
parse_failures
unmapped_phase_packets
blocks_emitted
```

This tells whether the issue is no UDP data, packet parser mismatch, channel
mapping, or writer output.

## Install

```bat
cd das_recorder
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run GUI

```bat
python run_app.py
```

## Saving startup defaults

Set the required values in the main window, then use:

```text
Settings -> Save Current Values as Defaults
```

The saved settings include device/local IP addresses, recording path and
format, frequency, pulse duration, file duration, origin offset, probing
length, channel-spacing mode, refractive index, distance range, and noise
settings. The red, green, and blue waterfall color-bar handle positions are
also saved and restored. DASRecorder loads these values automatically the next
time it starts.

For example, the user can set:

```text
Origin offset: 10 m
Probing length: 20000 m
```

and save them without editing JSON manually. User defaults are stored at:

```text
%APPDATA%\H4Tech\DASRecorder\default_config.json
```

The per-user location remains writable when the application is installed under
`Program Files`. If no user settings have been saved, DASRecorder loads the
factory `config\default_config.json` supplied with the application.
The `app_version` value in that configuration is displayed in the main-window
title and About dialog.

## Build Windows EXE

Install PyInstaller once:

```bat
python -m pip install pyinstaller
```

Then build:

```bat
build_exe.bat
```

The portable app is created here:

```text
dist\DASRecorder\DASRecorder.exe
```

Copy the whole `dist\DASRecorder` folder to the target PC. Keep
`config\default_config.json` beside the EXE so the device IP, local IP, output
folder, and recording settings can be edited after packaging.

For a real installer, install Inno Setup, then open and compile:

```text
DASRecorderInstaller.iss
```

The installer will be created under:

```text
installer\DASRecorder_Setup.exe
```

## Production settings

For KIGAM-style H5 (example):

```json
{
  "formats": ["h5"],
  "sample_rate_hz": 2000,
  "probing_length_m": 20000,
  "channel_spacing_m": 1.021091478201635,
  "auto_channel_spacing": true,
  "clock_period_ns": 10.0,
  "index_of_refraction": 1.468,
  "line_point_start": 73,
  "segment_duration_s": 60,
  "chunk_samples": 500
}
```

For PSUDAS-style TDMS (example0):

```json
{
  "formats": ["tdms"],
  "sample_rate_hz": 500,
  "probing_length_m": 20000,
  "channel_spacing_m": 1.021091478201635,
  "auto_channel_spacing": true,
  "clock_period_ns": 10.0,
  "index_of_refraction": 1.468,
  "segment_duration_s": 60,
  "chunk_samples": 500
}
```

Automatic spacing and channel count use:

```text
channel_spacing_m =
    speed_of_light * clock_period_s / (2 * index_of_refraction)

calculated_channels =
    floor((probing_length_m - origin_offset_m) / channel_spacing_m) + 1
```

With a `10 ns` clock and refractive index `1.468`, exact spacing is
`1.021091478201635 m`. The UI displays this as `1.02 m`, but calculations,
device setup, H5 metadata, and TDMS metadata retain the exact value. This gives
`49` calculated channels for `50 m` and `98` for `100 m`.

## Latest UI update

The File section now includes File duration. Chunk samples was removed from the
main window and remains only in the config file as an internal streaming buffer.
The Data acquisition region shows Calculated channels and Received channels as
display-only values. Origin offset and Probing length can be edited before
connecting or while connected. Channel spacing defaults to clock-derived mode.
Clear `Calculate spacing from device clock` to enter a manual value. A
connected geometry change immediately recalculates the channel grid, updates the device setup when
required, and resets the live waterfall. These controls are locked while a
recording segment is open so an H5/TDMS dataset cannot change shape.

The top Save distance controls use absolute fiber distance:

```text
minimum allowed distance = origin offset
maximum allowed distance = probing length
```

Min and Max can be changed either by typing in the two inputs or by dragging
the two handles on the range slider. Both control methods remain synchronized.

Distance geometry uses:

```text
calculated_channels =
    floor((probing_length_m - origin_offset_m) / channel_spacing_m) + 1
```

Received channels is counted from the unique line-point numbers sent by the
device in a complete packet block and remains independent of Calculated
channels. Active channels excludes rows containing only the Dunay fill value
`-15708`. A probing length longer than the connected fiber therefore shows the
remaining distance as black/no data rather than converting fill values into
false signal.

Each new H5 and TDMS segment automatically records:

```text
CalculatedChannelCount
ReceivedChannelCount
SavedChannelCount
ChannelSpacing_m
ClockPeriod_ns
```

`ReceivedChannelCount` is the maximum number of channel packets observed in a
complete device block during that file segment. H5 stores these values as root
attributes so the standard 22 KIGAM dataset attributes remain unchanged. TDMS
stores them as root properties.


## Real Dunay UDP test

Before running the GUI, you can check whether the PC receives UDP phase packets:

```bat
python tools\test_dunay_udp_receive.py --connect --local-ip 192.168.180.11 --device-ip 192.168.180.10 --seconds 10
```

If bind fails, try listening on all adapters:

```bat
python tools\test_dunay_udp_receive.py --connect --local-ip 0.0.0.0 --device-ip 192.168.180.10 --seconds 10
```

Expected result:

```text
Packet 1: ... bytes from ('192.168.180.10', ...)
  parsed phase: endian=<, line_point_no=..., block_no=...
```

If no packets are received, check:

1. The PC and Dunay device are in the same LAN/subnet.
2. Windows Firewall allows inbound UDP ports 8211 and 8227.
3. The Local IP field is the PC adapter IP connected to the Dunay LAN.
4. No other program is already using UDP port 8227.

## Windows Firewall Setup

The app can add the required Windows Firewall rules:

- inbound UDP `8211` for Dunay command responses
- inbound UDP `8227` for live phase data
- outbound UDP `8201` for commands sent to the Dunay device

From the GUI:

```text
Tools -> Configure Windows Firewall
```

From source:

```bat
configure_firewall.bat
```

Or run as Administrator:

```bat
python run_app.py --configure-firewall
```

Administrator permission is required because Windows does not allow normal
applications to change firewall rules silently.
