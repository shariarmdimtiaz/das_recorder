# H5 File Structure Comparison

Comparison date: 2026-07-27

## Files

| Label | File | Size |
| --- | --- | ---: |
| DASRecorder | `DS_20260727_103457_2000Hz.h5` | 9,139,840 bytes (8.72 MiB) |
| KIGAM reference | `KIGAM_40Hz_2kHz.h5` | 93,254,768 bytes (88.93 MiB) |

## Conclusion

The files use the same HDF5 schema, but they are not the same acquisition.

- Both have the same group/dataset hierarchy.
- Both store an extensible, uncompressed, two-dimensional `int16` dataset.
- Both have the same 22 dataset attribute names and matching attribute data types.
- Their channel counts, sample counts, duration, acquisition metadata, and data distributions differ substantially.
- The DASRecorder file contains a large amount of the device no-data value `-15708`.

A reader that recognizes the KIGAM `/DataStreams/DS#<timestamp>` schema should
be able to open both files. A process that requires the KIGAM reference's exact
channel geometry or metadata values must not treat the DASRecorder file as
equivalent.

## HDF5 Tree

Both files have this tree:

```text
/
└── DataStreams
    └── DS#YYYYMMDDTHH:MM:SS
```

Neither file has root attributes. The timestamp portion of the dataset name is
different because the recordings were created at different times.

## Dataset Layout

| Property | DASRecorder file | KIGAM reference | Same? |
| --- | ---: | ---: | --- |
| Dataset path | `/DataStreams/DS#20260727T10:34:57` | `/DataStreams/DS#20260624T15:35:01` | Pattern only |
| Shape | `(38, 120000)` | `(1457, 32000)` | No |
| Channels | 38 | 1,457 | No |
| Samples per channel | 120,000 | 32,000 | No |
| Data type | `int16` | `int16` | Yes |
| Maximum shape | `(38, unlimited)` | `(1457, unlimited)` | Same design |
| Chunk shape | `(38, 500)` | `(1457, 500)` | Same 500-sample chunk design |
| Compression | None | None | Yes |
| Fill value | 0 | 0 | Yes |
| Frequency | 2,000 Hz | 2,000 Hz | Yes |
| Calculated duration | 60 seconds | 16 seconds | No |

The approximately ten-times larger KIGAM file is explained by its sample
matrix: 46,624,000 values compared with 4,560,000 values in the DASRecorder
file.

## Attribute Schema

Both datasets have exactly 22 attributes. Attribute names, array shapes, numeric
types, and low-level fixed-string definitions match.

### Different Attribute Values

| Attribute | DASRecorder file | KIGAM reference | Meaning |
| --- | ---: | ---: | --- |
| `EndPoint` | 37 | 1470 | Acquisition/device endpoint metadata |
| `ImpDuration` | 200 | 20 | Configured impulse duration |
| `IsNoiseSuppressed` | 0 | 1 | Noise suppression disabled/enabled |
| `MetricLength` | 50 | 1500 | Recorded metric length |
| `RecordEndPoint` | 38 | 1489 | Recording endpoint metadata |
| `RecordingTime` | `2026-07-27T10:34:57` | `2026-06-24T15:35:01` | Recording start time |

The other 16 attribute values match, including:

```text
Frequency = 2000
LineOffset = 73
MetricOffset = 0
Mode = Phase
RecordStartPoint = 1
FirmwareVersion = 04:00:03:0C
HardwareVersion = 03:01:02:00
Version = 1.0.0.98
```

### Metadata Compatibility Note

In the DASRecorder file, `EndPoint=37` and `RecordEndPoint=38` directly follow
the 38-row dataset. In the KIGAM reference, `EndPoint=1470` and
`RecordEndPoint=1489` do not directly equal the 1,457-row dataset size.

This indicates that the reference fields likely describe vendor device
coordinates or acquisition line points, not simply a zero-based row index and
row count. The schemas match, but these endpoint values may not have identical
semantics. This should be confirmed before strict vendor compatibility is
claimed.

Neither file contains a dedicated channel-spacing attribute. Exact channel
spacing therefore cannot be verified from these files alone. Inferring it by
dividing `MetricLength` by the row count would be unreliable because the
endpoint fields use vendor-specific acquisition coordinates.

## Data Comparison

| Statistic | DASRecorder file | KIGAM reference |
| --- | ---: | ---: |
| Total values | 4,560,000 | 46,624,000 |
| Minimum | -19,635 | -32,768 |
| Maximum | 19,437 | 32,767 |
| Mean | -6,808.49 | -0.50 |
| Standard deviation | 7,829.72 | 659.31 |
| Zero values | 104,133 (2.28%) | 4,571,670 (9.81%) |
| `-15708` values | 1,959,802 (42.98%) | 1 (approximately 0%) |
| Constant channels | 9 | 59 |

The large negative mean in the DASRecorder file is caused mainly by the
`-15708` device no-data value. It is not representative of the valid phase
signal mean.

### DASRecorder Channel Availability

- Channels `0`, `1`, and `10–16` are completely constant at `-15708`.
- Channels `2`, `3`, `9`, and `17` contain at least 97% `-15708`.
- Channels `4–8` and `18–20` contain mixed phase and no-data values.
- Channels `21–37` contain varying phase data with no `-15708` values.

The KIGAM reference has only one `-15708` value in the entire dataset. Its 59
constant channels are constant zero, not the Dunay `-15708` no-data marker.

## Final Assessment

| Question | Answer |
| --- | --- |
| Same HDF5 hierarchy? | Yes |
| Same dataset and attribute types? | Yes |
| Same number of attributes? | Yes, 22 |
| Same dimensions? | No |
| Same acquisition duration? | No |
| Same channel geometry? | No |
| Same acquisition settings? | No |
| Same data quality/content? | No |
| Structurally readable as the same file family? | Yes |
| Fully equivalent to the KIGAM reference? | No |
