# ISAC packet captures

This directory contains utilities for receiving the OCUDU ISAC ZeroMQ stream.
The packet recorder stores every received ZeroMQ message in a binary capture
file. It does not decode, filter, or transform the ISAC payload.

Start a capture with:

```bash
python3 utils/isac/isac_packet_capture.py
```

The default output is a timestamped file such as
`isac_capture_20261020_143015.bin`. For a gNB running on another machine:

```bash
python3 utils/isac/isac_packet_capture.py \
  --endpoint tcp://192.168.1.20:5556
```

The extension is only a naming convention. `.bin` is used because the capture
is a custom binary container; an older `.isacraw` file has the same format and
can be read identically.

## What an ISAC ZeroMQ message contains

The gNB publishes CSI and equalized-symbol frames on the same ZeroMQ PUB
socket. They are separate ZeroMQ messages, not two payloads layered inside one
message. The recorder subscribes to all messages, so both kinds are stored in
the same capture file.

Each ZeroMQ message has this layout:

```text
22-byte ISAC frame header
count × complex64 samples
```

The header is tightly packed, little-endian, and corresponds to:

```python
FRAME_HEADER = struct.Struct("<IHBHIHBBBHH")
```

| Offset | Field | Type | Meaning |
|---:|---|---|---|
| 0 | `magic` | `uint32` | `0x49534143`, ASCII `ISAC` |
| 4 | `version` | `uint16` | Wire format version |
| 6 | `kind` | `uint8` | `0` = CSI, `1` = equalized symbols |
| 7 | `scs_khz` | `uint16` | Subcarrier spacing in kHz |
| 9 | `slot` | `uint32` | System slot number |
| 13 | `rnti` | `uint16` | UE RNTI |
| 15 | `symbol` | `uint8` | OFDM symbol index represented by the frame |
| 16 | `rx_port` | `uint8` | Receive-port index |
| 17 | `tx_layer` | `uint8` | Transmit-layer index |
| 18 | `rb_start` | `uint16` | First RB represented by the vector |
| 20 | `count` | `uint16` | Number of complex samples following the header |

The payload consists of `count` little-endian complex-64 values, with each
value stored as a `float32` real part followed by a `float32` imaginary part.

For the current publisher:

- `kind == 0` is a CSI vector containing the active subcarriers for one
  reported DMRS/OFDM symbol;
- `kind == 1` is a vector of equalized complex symbols;
- CSI is not currently a complete 14-symbol `SC × Sym` grid;
- `count` describes the samples in that frame, not necessarily every
  subcarrier in the full carrier bandwidth;
- `symbol` identifies which OFDM symbol the CSI vector belongs to.

In particular, a current CSI frame is best understood as:

```text
CSI frame = H[k] for one symbol, where k runs over the active subcarriers
```

If only one DMRS symbol is exported, a per-slot `SC × Sym` grid will naturally
contain one populated symbol column and zeros in the unobserved columns. Those
zeros mean “not present in this capture,” not measured zero channel response.

## Capture file layout

The `.bin` file adds framing and receive-time metadata around each unchanged
ZeroMQ message.

```text
16-byte file header:
  8-byte magic:             b"ISACRAW\\0"
  uint16 version:           1
  uint16 record-header size: 28
  uint32 reserved:          0

For every received ZeroMQ message:
  uint64 wall-clock arrival time in ns
  uint64 monotonic arrival time in ns
  uint64 receive-order index
  uint32 ZeroMQ message length in bytes
  message bytes exactly as received
```

The following snippets are deliberately shown inline for analysis notebooks or
experiments; they are not additional utility scripts.

## 1. Read the binary capture

This reader preserves the raw frame while also decoding the metadata needed for
analysis. It raises on a truncated record instead of silently interpreting an
incomplete packet.

```python
from pathlib import Path
import struct

import numpy as np


FILE_HEADER = struct.Struct("<8sHHI")
RECORD_HEADER = struct.Struct("<QQQI")
FRAME_HEADER = struct.Struct("<IHBHIHBBBHH")

FILE_MAGIC = b"ISACRAW\0"
FRAME_MAGIC = 0x49534143
CSI = 0
EQ = 1


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise EOFError(f"truncated capture: expected {size} bytes, got {len(data)}")
    return data


def read_capture(path):
    with Path(path).open("rb") as stream:
        magic, version, record_header_size, _reserved = FILE_HEADER.unpack(
            read_exact(stream, FILE_HEADER.size)
        )
        if magic != FILE_MAGIC:
            raise ValueError(f"not an ISAC capture: {magic!r}")
        if version != 1 or record_header_size != RECORD_HEADER.size:
            raise ValueError(
                f"unsupported capture format: version={version}, "
                f"record_header_size={record_header_size}"
            )

        while True:
            record = stream.read(RECORD_HEADER.size)
            if not record:
                return
            if len(record) != RECORD_HEADER.size:
                raise EOFError("truncated record header")

            wall_ns, monotonic_ns, record_index, message_size = RECORD_HEADER.unpack(record)
            raw = read_exact(stream, message_size)
            if len(raw) < FRAME_HEADER.size:
                raise ValueError("ISAC message is shorter than its frame header")

            (
                frame_magic,
                frame_version,
                kind,
                scs_khz,
                slot,
                rnti,
                symbol,
                rx_port,
                tx_layer,
                rb_start,
                count,
            ) = FRAME_HEADER.unpack_from(raw)

            if frame_magic != FRAME_MAGIC:
                raise ValueError(f"bad ISAC frame magic at record {record_index}")
            expected_size = FRAME_HEADER.size + count * np.dtype("<c8").itemsize
            if len(raw) != expected_size:
                raise ValueError(
                    f"record {record_index}: header says {expected_size} bytes, "
                    f"received {len(raw)}"
                )

            # copy() makes the array independent of the temporary raw bytes.
            iq = np.frombuffer(
                raw,
                dtype=np.dtype("<c8"),
                count=count,
                offset=FRAME_HEADER.size,
            ).copy()

            yield {
                "wall_ns": wall_ns,
                "monotonic_ns": monotonic_ns,
                "record_index": record_index,
                "version": frame_version,
                "kind": kind,
                "scs_khz": scs_khz,
                "slot": slot,
                "rnti": rnti,
                "symbol": symbol,
                "rx_port": rx_port,
                "tx_layer": tx_layer,
                "rb_start": rb_start,
                "count": count,
                "iq": iq,
                "raw": raw,
            }
```

The `raw` field is retained in each returned record when exact replay or
re-export is needed. The `iq` field is the decoded complex-valued vector.

## 2. Separate frames by kind

Both frame kinds can be separated while reading the same file:

```python
frames_by_kind = {CSI: [], EQ: []}

for frame in read_capture("isac_capture_20261020_143015.bin"):
    if frame["kind"] in frames_by_kind:
        frames_by_kind[frame["kind"]].append(frame)

csi_frames = frames_by_kind[CSI]
eq_frames = frames_by_kind[EQ]

print(f"CSI frames: {len(csi_frames)}")
print(f"EQ frames:  {len(eq_frames)}")
```

For large captures, process frames incrementally rather than building lists:

```python
for frame in read_capture("isac_capture_20261020_143015.bin"):
    if frame["kind"] == CSI:
        process_csi(frame["iq"], frame["slot"])
    elif frame["kind"] == EQ:
        process_equalized_symbols(frame["iq"], frame["slot"])
```

## 3. Zero-initialize and populate an `SC × Sym` CSI grid

The current CSI packet is one vector, so a downstream processor may need to
place vectors into a two-dimensional grid. The packet gives the first RB,
sample count, and OFDM symbol index. Since one RB contains 12 subcarriers, the
frequency span represented by a packet can be estimated as:

```python
sc_begin = frame["rb_start"] * 12
sc_end = sc_begin + frame["count"]
```

For a collection of CSI frames belonging to the same logical grid (for
example, the same slot/RNTI/port/layer), first determine the global frequency
and symbol bounds:

```python
import numpy as np


def make_csi_grid(csi_frames):
    if not csi_frames:
        raise ValueError("no CSI frames")

    sc_ranges = [
        (
            frame["rb_start"] * 12,
            frame["rb_start"] * 12 + frame["count"],
        )
        for frame in csi_frames
    ]

    min_sc = min(start for start, _end in sc_ranges)
    max_sc = max(end for _start, end in sc_ranges)
    min_symbol = min(frame["symbol"] for frame in csi_frames)
    max_symbol = max(frame["symbol"] for frame in csi_frames)

    # Axis 0 is subcarrier; axis 1 is OFDM symbol.
    grid = np.zeros(
        (max_sc - min_sc, max_symbol - min_symbol + 1),
        dtype=csi_frames[0]["iq"].dtype,
    )

    for frame in csi_frames:
        start = frame["rb_start"] * 12
        stop = start + frame["count"]
        column = frame["symbol"] - min_symbol

        if frame["iq"].size != stop - start:
            raise ValueError("CSI count does not match its decoded vector length")

        # The offset makes different RB ranges line up in the common grid.
        grid[start - min_sc : stop - min_sc, column] = frame["iq"]

    return grid, min_sc, max_sc, min_symbol, max_symbol


csi_grid, min_sc, max_sc, min_symbol, max_symbol = make_csi_grid(csi_frames)
print("grid shape (SC, Sym):", csi_grid.shape)
```

This first creates a complex-valued grid initialized to zero, with a frequency
extent large enough for the minimum-to-maximum subcarrier span observed in the
input. The loop then writes the real CSI vectors decoded from the capture into
their corresponding subcarrier and symbol positions. Any entries still equal
to zero are unobserved positions and must be distinguished from measured zero
values during later processing.

### Important frequency-mapping limitation

The current CSI header contains `rb_start` and `count`, but not a complete
subcarrier/PRB mask. The grid example therefore assumes that the packed CSI
vector represents a contiguous frequency span beginning at `rb_start × 12`.
For the ordinary PUSCH scheduler path in this repository, that assumption is
currently appropriate: the scheduler selects a contiguous `vrb_interval` and
PUSCH uses non-interleaved mapping. A 100% maximum-PRB policy setting permits a
slice or UE to use the full available bandwidth, but it does not by itself
guarantee that every grant uses the full bandwidth; grant size still depends on
pending data, MCS, PHR, other UEs, and available resources.

If a future scheduler configuration produces a non-contiguous RB allocation,
`count` alone is not enough to recover the exact absolute position of every
packed sample. The ISAC wire format would then need to export an RB/RE mask or
an explicit list of absolute subcarrier indices. Until then, a processor should
retain the original vector and treat the simple contiguous placement as an
allocation-dependent interpretation.

### Grouping before grid construction

Do not combine unrelated UEs, slots, ports, or layers into one grid. A typical
grouping key is:

```python
from collections import defaultdict

groups = defaultdict(list)
for frame in csi_frames:
    key = (
        frame["slot"],
        frame["rnti"],
        frame["rx_port"],
        frame["tx_layer"],
    )
    groups[key].append(frame)

for key, frames_for_grid in groups.items():
    grid, *_ = make_csi_grid(frames_for_grid)
    print(key, grid.shape)
```

For Doppler or other slow-time analysis, the slot/time dimension should remain
an additional axis outside this per-slot `SC × Sym` grid. Missing TDD/PUSCH
slots should be identified from slot or arrival-time metadata before any
interpolation is applied.
