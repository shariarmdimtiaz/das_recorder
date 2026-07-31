# Dunay DAS UDP Packet Structure

This document is a simplified explanation of the UDP command, response, and phase-data packets exchanged between the PC recorder and a Dunay DAS device.

## 1. PC -> DAS: Command Packets

A UDP command sent to the DAS device has this basic structure:

```text
[ command_code: 4 bytes ] + [ payload: optional ]
```

For example, the "Transfer Phase" command has no payload and is only 4 bytes.

```text
Command: Transfer Phase
Hex:     00 00 03 08

[00 00 03 08]
 └─ command_code = 0x00000308
```

The command that tells the device where to send data uses command + IP payload.

```text
Command: Set receiver IP
Hex:     00 00 00 08  C0 A8 B4 0B

[00 00 00 08] [C0 A8 B4 0B]
 └ command      └ 192.168.180.11
```

The `SetParams` command uses command + register payload.

```text
Command: Set 32-bit registers
Hex:     00 00 00 07 + registers payload

[00 00 00 07] [reg0][reg1][reg2]...[reg15][00 00]
```

Conceptually, the registers mean:

```text
reg0  = pulse period, 10 ns unit
reg1  = pulse width/duration, 10 ns unit
reg4  = trace read start coordinate
reg5  = trace read end coordinate
reg8  = time smoothing points
reg11 = filter/control flags
reg13 = LineStart / LineOffset
reg14 = line decimation
reg15 = flags
tail  = 00 00
```

For a 2000 Hz, 200 ns setting:

```text
Frequency 2000 Hz
Period = 1 / 2000 s = 0.0005 s = 500,000 ns
10 ns unit => 50,000

Duration 200 ns
10 ns unit => 20
```

So the first registers are approximately:

```text
reg0 = 50000  -> 00 00 C3 50
reg1 = 20     -> 00 00 00 14
```

## 2. DAS -> PC: Response Packets

When the DAS device receives a command, it responds on UDP 8211.

```text
[ response_code: 4 bytes ] + [ optional data ]
```

For example, if the `Transfer Phase` command `0x00000308` is accepted:

```text
Command 0x00000308 accepted
Response: F0 01 03 08

[F0 01 03 08]
 └ response_code = 0xF0010308
```

Response codes are usually interpreted as:

```text
F001xxxx = supported/accepted
F002xxxx = unsupported/rejected
```

## 3. DAS -> PC: Phase Data Packets

The phase data used for recording arrives on UDP 8227.

One phase packet can be understood as "one spatial line point + 500 time samples."

```text
[ line_point_no: 4 bytes ]
[ block_no     : 4 bytes ]
[ radius       : 4 bytes ]
[ phase samples: 500 * int16 = 1000 bytes ]
```

The total payload length is:

```text
4 + 4 + 4 + 1000 = 1012 bytes
```

Example:

```text
Packet A
line_point_no = 0
block_no      = 125
samples       = 500 values

Packet B
line_point_no = 1
block_no      = 125
samples       = 500 values

...

Packet N
line_point_no = 111
block_no      = 125
samples       = 500 values
```

Packets with the same `block_no=125` are assembled into one spatial frame.

```text
block_no 125 frame

row 0   <- 500 samples from line_point_no 0
row 1   <- 500 samples from line_point_no 1
row 2   <- 500 samples from line_point_no 2
...
row 111 <- 500 samples from line_point_no 111
```

## 4. Raw Rows and Saved H5 Rows

The recorder does not necessarily save every raw line point. It crops the valid rows for the requested distance window and writes those rows to H5.

For example, the following pattern was observed for a 100 m setting:

```text
raw receive : 112 rows, line_point 0..111
valid save  : 98 rows
discard     : line_point 98..111, fill/no-data values
H5 shape    : (98, time_columns)
```

In raw packet captures, the discarded tail rows were mostly or entirely the fixed fill/no-data value `-15708`.

For a 200 m setting:

```text
raw receive : a larger line_point range
valid save  : 196 rows
H5 shape    : (196, time_columns)
```

## 5. Axis Summary

```text
line_point_no = spatial axis
samples[500]  = time-axis chunk
block_no      = frame identifier for the same moment
```

Therefore the final H5 dataset has this structure:

```text
H5 dataset shape = (spatial_channels, time_columns)
```

Example:

```text
100 m, 2000 Hz, 60 s

spatial_channels ~= 98
time_columns     = 2000 / 4 * 60 = 30000

H5 shape = (98, 30000)
```

The `/4` factor comes from the Dunay phase output being decimated by 4 along the time axis.
