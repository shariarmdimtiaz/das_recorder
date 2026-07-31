# Clock Pulse and H5 Geometry Fix Report

## Compared recordings

| Value | DASRecorder before fix | Original Dunay |
| --- | ---: | ---: |
| Dataset shape | `(76, 60000)` | `(98, 16000)` |
| Frequency | `1000 Hz` | `1000 Hz` |
| Metric length | `100 m` | `100 m` |
| `ImpPeriod` | `50000` | `100000` |
| `ImpDuration` | `200` | `20` |
| `EndPoint` | `75` | `99` |
| `RecordStartPoint` | `1` | `0` |
| `RecordEndPoint` | `76` | `100` |
| Configured spacing | `1.32 m` | Clock-derived |

## Root cause

The Dunay acquisition metadata uses hardware-clock ticks, not nanoseconds.
The captured vendor profile confirms a `10 ns` clock period (`100 MHz`):

```text
ImpPeriod = round(100,000,000 / frequency_hz)
ImpDuration = round(duration_ns / 10)
```

Therefore:

```text
1000 Hz -> ImpPeriod 100000
1500 Hz -> ImpPeriod 66667
2000 Hz -> ImpPeriod 50000
200 ns  -> ImpDuration 20
```

The physical distance represented by one round-trip clock tick is:

```text
spacing =
    299792458 * 10e-9 / (2 * 1.468)
  = 1.021091478201635 m
```

For a `100 m` probing length:

```text
channels = floor(100 / 1.021091478201635) + 1
         = 98
```

The summary's `100 / (98 - 1) = 1.030928 m` is only an endpoint-to-endpoint
estimate. The last of 98 clock-derived positions is approximately `99.046 m`;
the `MetricLength=100` field describes the requested acquisition region.

## Implemented correction

- The default spacing is derived from the `10 ns` clock and refractive index.
- The UI displays `1.02 m` while retaining the exact spacing internally.
- The calculated channel count is dynamic for every probing length.
- The device setup packet now updates pulse period, pulse duration, and probing
  endpoint before phase transfer starts.
- A probing-length change while connected queues a setup refresh and restarts
  phase transfer.
- H5 writes clock-tick `ImpPeriod` and `ImpDuration` values.
- H5 endpoint values now match the original Dunay convention for a full region:
  `StartPoint=0`, `EndPoint=99`, `RecordStartPoint=0`,
  `RecordEndPoint=100`.
- H5 and TDMS store exact `ChannelSpacing_m` and `ClockPeriod_ns` metadata.

## Verification

Automated checks confirm:

```text
10 ns, n=1.468 -> 1.021091478201635 m
100 m -> 98 channels
1000 Hz -> 100000 period ticks
200 ns -> 20 duration ticks
100 m setup endpoint -> 99
```

An H5/TDMS integration run created both formats and verified the same clock and
spacing metadata in their outputs.
