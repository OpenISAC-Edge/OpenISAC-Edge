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

## gNB configuration and CSI path selection

The ISAC exporter has its own top-level YAML section. The defaults are:

```yaml
isac:
  enabled: false
  bind_addr: 127.0.0.1
  port: 5556
  nof_rx_ports: 1
  nof_tx_layers: 1
```

Setting only `enabled: true` retains the default CSI selection of the first
participating RX port and layer 0, and also retains the existing EQ stream.
Use `bind_addr: 0.0.0.0` to accept subscribers from another PC; subscribers
connect to the gNB's reachable IP address, not to `0.0.0.0`. Use a numeric IPv4
bind address. The ISAC address is independent of remote control. ISAC settings
are accepted only in this top-level section, not under `metrics`.

For multi-path CSI capture, for example:

```yaml
isac:
  enabled: true
  bind_addr: 0.0.0.0
  port: 5556
  nof_rx_ports: 2
  nof_tx_layers: 2
```

These are **capture counts**, not port/layer indices or radio settings. They
select up to the first N entries in the PUSCH RX-port list and up to the first
N active UE TX layers. Configure receiving antennas and permitted PUSCH rank
separately in `cell_cfg`. RX-port and layer counts are independent: capturing
two layers from one RX port is valid when the underlying PUSCH supports it.

For two UL layers, the cell needs at least two `nof_antennas_ul`, a
`pusch.max_rank` of at least two, transform precoding disabled, and a UE-dedicated
SS2 with `pdcch.dedicated.dci_format_0_1_and_1_1: true`. The UE must also support
and actually be scheduled with two layers. A two-port/one-layer capture does
not require a two-layer UE transmission.

The actual dimensions are read from each processed PUSCH, rather than assumed
from the configured maximum rank. Each existing selected pair produces one
complete allocated-subcarrier CSI vector, with no sample truncation or padding:

| Actual PUSCH (RX ports × layers) | Requested capture | CSI messages per captured PUSCH |
|---|---|---|
| 1 × 1 | 1 × 1 | RX0/L0 |
| 2 × 1 | 2 × 2 | RX0/L0, RX1/L0 |
| 2 × 2 | 2 × 2 | RX0/L0, RX0/L1, RX1/L0, RX1/L1 |
| 2 × 2 | 1 × 2 | RX0/L0, RX0/L1 |
| 4 × 4 | 4 × 4 | 16 separate path messages |

The examples assume RX-port identities start at zero. The message identifies
the configured grid port, which may differ from its position in the PUSCH list.
Layers describe effective UE transmission streams, not physical UE antennas.
Absent layers produce no messages; a later rank increase exposes the additional
selected paths automatically. A numerical zero inside an existing estimate is
not the same as an absent path.

### Configuration guards

- Unknown fields, invalid types, zero/negative counts and TCP ports outside
  `1..65535` are rejected even when ISAC is disabled.
- When enabled, the bind address must be a numeric IP address. The requested
  port count must fit every configured cell's UL RX capacity, and the layer
  count must fit its effective UL layer limit (including maximum rank,
  transform precoding and DCI/search-space restrictions).
- At runtime, each count is independently limited to the actual PUSCH count.
  Capture selection never changes estimation, equalization, scheduling or rank.
- A publisher initialization/bind failure stops startup with an error.
- When disabled, no ISAC publisher is constructed or sink registered. Address
  and cell-capacity compatibility checks are skipped.

### Queueing and capture completeness

The tap copies estimates before their buffers can be reused, then defers
serialization and publication to the existing **shared metrics executor**.
There is no additional timer, receiver acknowledgement or matrix-completion
barrier. More paths increase copying and queue/transport load; the export is
not zero-overhead and has no dedicated worker yet.

Executor enqueue failures discard whole path captures rather than shorten
vectors. Shutdown reports `ISAC CSI captures rejected by executor`. This counts
failed CSI submissions, not all transport losses: ZMQ PUB delivery is best-effort
and can silently drop messages for unavailable/slow subscribers. Other executor
dispatch failures can also discard previously queued work. A complete vector
does not guarantee a complete matrix or a lossless time series. No new drop
counter is added to the unchanged raw recorder.

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
| 15 | `symbol` | `uint8` | CSI estimate's OFDM symbol index; EQ currently uses placeholder 0 |
| 16 | `rx_port` | `uint8` | CSI configured RX-port identity; EQ currently uses placeholder 0 |
| 17 | `tx_layer` | `uint8` | CSI UE layer index; EQ currently uses placeholder 0 |
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

The v2 header and complex64 payload format are unchanged. Each CSI message
contains only one RX-port/layer pair; it is not a flattened MIMO matrix. The
selected symbol remains the first DMRS symbol in the PUSCH. With the default
`average` estimator strategy, its coefficients are time-averaged estimates,
not independent instantaneous measurements of every DMRS symbol.

EQ export is unchanged: one message for the first OFDM symbol containing data
per PUSCH, with layers interleaved RE-major. Its port/layer fields do not label
individual paths, and the existing export-length cap remains. The CSI capture
counts do not filter EQ or determine its actual layer count.

In particular, a current CSI frame is best understood as:

```text
CSI frame = H[rx_port, tx_layer, k] for one selected estimate symbol
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
        process_csi(
            frame["iq"], frame["slot"], frame["rnti"],
            frame["rx_port"], frame["tx_layer"],
        )
    elif frame["kind"] == EQ:
        process_equalized_symbols(frame["iq"], frame["slot"])
```

### Select one CSI path before time-series analysis

When multiple paths are exported, do not append their coefficients into one
Doppler time series. Select a UE and path first:

```python
selected_rnti, selected_rx_port, selected_tx_layer = 0x4601, 1, 0

selected_csi = [
    frame for frame in csi_frames
    if (frame["rnti"], frame["rx_port"], frame["tx_layer"])
    == (selected_rnti, selected_rx_port, selected_tx_layer)
]
```

Both GNU Radio blocks accept appended `rx_port=0` and `tx_layer=0` arguments:

```python
# In isac_gr_block.py (CSI source):
blk(endpoint="tcp://127.0.0.1:5556", kind=0, sc=0,
    rnti=0x4601, rx_port=1, tx_layer=0)

# In isac_doppler_block.py (Doppler spectrum):
blk(endpoint="tcp://127.0.0.1:5556", sc=0, nfft=1024, hop=128,
    rnti=0x4601, rx_port=1, tx_layer=0)
```

Run separate block instances for separate paths. The port/layer filters apply
only to CSI; the general block's EQ behavior is unchanged. Existing positional
arguments still work. `sc` remains a vector-relative index, not a global
subcarrier index; changing allocations can change which physical subcarrier
that index represents. The default `rnti=-1` accepts all UEs, so choose an
explicit RNTI when constructing a single UE's Doppler history.

These blocks do not interpolate missing slots, reconstruct dropped messages,
or resample irregular arrivals. Slot-rate sampling assumes one captured PUSCH
per slot for the chosen path. Joining mid-run is supported; earlier packets
cannot be replayed. The raw recorder continues to save every received path
and both frame kinds regardless of these analysis filters.

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
