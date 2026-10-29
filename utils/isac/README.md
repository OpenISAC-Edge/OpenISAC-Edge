# ISAC capture and analysis utilities

This directory provides standalone capture/reconstruction tools and GNU Radio
blocks for the OCUDU ISAC tap. The tap exports existing uplink channel estimates
(CSI) and equalized symbols (EQ); sensing, localization and gap-handling
algorithms remain external.

All commands and notebook paths below assume the **repository root** as the
working directory. Example IP addresses and RNTI `0x4601` must be replaced with
the gNB's reachable address and the UE's observed RNTI.

## Utilities and launch instructions

| File | How it runs | Purpose and output | Python dependencies |
|---|---|---|---|
| [isac_packet_capture.py](isac_packet_capture.py) | Standalone CLI | Records every received CSI/EQ ZMQ message unchanged in a framed `.bin` file | `pyzmq` |
| [isac_csi_grid.py](isac_csi_grid.py) | Standalone CLI | Reconstructs per-UE/path CSI grids from a closed capture into `.npz` | `numpy` |
| [isac_gr_block.py](isac_gr_block.py) | GNU Radio `blk` class; no standalone CLI | Outputs one CSI coefficient per message, or all captured EQ points, as a complex stream | GNU Radio, `numpy`, `pyzmq` |
| [isac_doppler_block.py](isac_doppler_block.py) | GNU Radio `blk` class; no standalone CLI | Outputs a rolling CSI Doppler-magnitude spectrum as a float vector | GNU Radio, `numpy`, `pyzmq` |

Use a Python environment that can import the dependencies for the selected
utility. Capture `.bin` files are data, not scripts; `__pycache__/` contains
generated Python bytecode and is not a launch target.

### Record complete packets: standalone Python

```bash
python3 utils/isac/isac_packet_capture.py
```

By default, this connects to `tcp://127.0.0.1:5556` and creates
`isac_capture_<local-date-time>.bin` in the current working directory. For a
remote gNB and an explicit output name:

```bash
python3 utils/isac/isac_packet_capture.py \
  --endpoint tcp://192.168.1.20:5556 \
  --output utils/isac/session.bin
```

| Option | Default | Meaning |
|---|---|---|
| `--endpoint` | `tcp://127.0.0.1:5556` | ZMQ publisher address to connect to |
| `--output` | Timestamped `.bin` filename | New capture file; existing files are not overwritten |
| `--queue-size` | `4096` | Maximum packets waiting for the disk-writer thread |
| `--rcv-hwm` | `10000` | ZMQ receive high-water mark |
| `--rcvbuf` | `67108864` | Requested socket receive buffer, in bytes |
| `--fsync-every` | `0` | Call `fsync` every N packets; zero disables it |

The receiver and disk writer run in separate threads. The application queue
blocks when full rather than intentionally dropping accepted packets; this
does not prevent upstream ZMQ/publisher losses. Each complete record is flushed
from Python's buffer, while `fsync` is an optional, more expensive durability
step. This recorder thread is separate from the gNB publisher worker discussed
under [planned development](#planned-development).

Press **Ctrl-C** to stop and drain accepted packets before closing the file.
The subscriber may start before or after the gNB; late joining cannot recover
earlier messages. Both CSI and EQ, all UEs and all exported paths are recorded
without decoding or filtering.

The extension is a convention: older `.isacraw` files use the same capture
container and can be read identically. See all options with:

```bash
python3 utils/isac/isac_packet_capture.py --help
```

### Generate CSI grids: standalone Python

Stop the recorder first, then reconstruct the closed capture:

```bash
python3 utils/isac/isac_csi_grid.py --file utils/isac/session.bin
```

The only input options are `--file` (required) and `--scs` (default **15 kHz**).
For example, for a 30 kHz capture:

```bash
python3 utils/isac/isac_csi_grid.py --file utils/isac/session.bin --scs 30
```

Valid `--scs` choices are `15`, `30`, `60`, `120` and `240`, matching the local
OCUDU SCS enum. Every packet's SCS must match the selection; this option sets
counter bounds and slot duration, not resampling or radio configuration.
Accepted counter numerologies do not imply support for every SCS/band in the
gNB.

The output is `utils/isac/session_csi_grid.npz`. The input is unchanged and an
existing output is never overwritten. Mixed CSI/EQ captures are accepted, but
only CSI enters the grids. See [CSI resource-grid reconstruction](#csi-resource-grid-reconstruction)
for array layout, validity masks and loading examples.

```bash
python3 utils/isac/isac_csi_grid.py --help
```

### Use the GNU Radio blocks

These two modules define `blk(gr.sync_block)` classes. Running either module
with `python3` alone does **not** launch a flowgraph or a GUI.

In GNU Radio Companion, launch the editor with:

```bash
gnuradio-companion
```

Add an **Embedded Python Block**, put the selected module's code in its editor,
set its constructor parameters, connect a compatible sink, and run the
flowgraph. Alternatively, import the classes in an existing Python flowgraph
as below. The examples construct blocks; the containing flowgraph must connect
and start them.

#### Complex CSI/EQ source: `isac_gr_block.py`

```python
import sys
sys.path.insert(0, "utils/isac")

from isac_gr_block import blk as ISACSource

csi_source = ISACSource(
    endpoint="tcp://127.0.0.1:5556", kind=0, sc=0,
    rnti=0x4601, rx_port=0, tx_layer=0,
)
eq_source = ISACSource(
    endpoint="tcp://127.0.0.1:5556", kind=1, rnti=0x4601,
)
```

- `kind=0` emits one `complex64` CSI coefficient at vector-relative index `sc`
  for the selected UE/RX-port/TX-layer. Connect to a complex Time or Frequency
  Sink.
- `kind=1` emits all retained EQ constellation points from each selected UE's
  EQ packet. Connect to a Constellation Sink. This output is not deinterleaved
  by layer, and the CSI port/layer filters do not apply to it.
- Constructor defaults are `endpoint="tcp://127.0.0.1:5556"`, `kind=0`,
  `sc=0`, `rnti=-1`, `rx_port=0` and `tx_layer=0`. Select an explicit RNTI for
  multi-UE work: `-1` accepts all UEs and can mix their samples.
- EQ emits many points per packet, not one sample per slot; do not interpret
  its complex-stream rate as the CSI slot rate.

#### Rolling Doppler spectrum: `isac_doppler_block.py`

```python
import sys
sys.path.insert(0, "utils/isac")

from isac_doppler_block import blk as ISACDoppler

doppler = ISACDoppler(
    endpoint="tcp://127.0.0.1:5556", sc=0,
    nfft=1024, hop=128, rnti=0x4601, remove_dc=True,
    rx_port=0, tx_layer=0,
)
```

This follows one CSI coefficient for the selected UE/path. After filling its
rolling window, it emits a `float32` vector of length `nfft` containing
`20*log10(abs(fftshift(FFT)))`, with optional window-mean removal. Connect it
directly to a **QT GUI Vector Sink** with vector length `nfft`, not a complex
Frequency Sink. `nfft` sets the observation-window length, and `hop` is the
minimum number of newly accepted CSI samples before refreshing the spectrum.

The defaults are `nfft=1024`, `hop=128` and `remove_dc=True`; the other defaults
match the CSI source. For an even `nfft`, configure the Vector Sink's frequency
axis with start `-fs/2` and step `fs/nfft`. Here `fs` is the actual uniform
CSI-observation rate, **not the SDR sample rate**. For example, 15 kHz SCS
gives `fs=1000` Hz only when this UE/path contributes one observation in every
slot.

Both live blocks discard radio slot/SCS metadata from their output and do not
unwrap counters, preserve gaps, insert NaNs, interpolate or correct irregular
sampling. Use the offline grid tool or a custom time-aware processor for
gap-sensitive analysis. Also, `sc` is an index into the captured vector:
changing RB allocations can change the physical SC being followed. See
[operator assumptions and responsibilities](#operator-assumptions-and-responsibilities).

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

With two UEs each actually using two layers and two RX ports, a 2×2 tap
produces four CSI messages per UE's processed PUSCH, eight in total when both
are processed. Their RNTIs keep the UE-specific observations distinct; this is
not a promise that both UEs are scheduled in every slot.

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

## ISAC packet structure

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
| 0 | `magic` | `uint32` | `0x49534143`, ISAC frame marker |
| 4 | `version` | `uint16` | Wire format version (`2`) |
| 6 | `kind` | `uint8` | `0` = CSI, `1` = equalized symbols |
| 7 | `scs_khz` | `uint16` | Subcarrier spacing in kHz |
| 9 | `slot` | `uint32` | Combined SFN/slot counter; wraps every 10.24 s (see [counter unwrapping](#session-boundaries-slot-bounds-and-counter-unwrapping)) |
| 13 | `rnti` | `uint16` | UE RNTI |
| 15 | `symbol` | `uint8` | CSI estimate's OFDM symbol index; EQ currently uses placeholder 0 |
| 16 | `rx_port` | `uint8` | CSI configured RX-port identity; EQ currently uses placeholder 0 |
| 17 | `tx_layer` | `uint8` | CSI UE layer index; EQ currently uses placeholder 0 |
| 18 | `rb_start` | `uint16` | First RB represented by the vector |
| 20 | `count` | `uint16` | Number of complex samples following the header |

The payload consists of `count` little-endian complex-64 values, with each
value stored as a `float32` real part followed by a `float32` imaginary part.

<strong>Each CSI message is specific to one UE (identified by its RNTI), one RX port, and one TX layer; CSI from different UEs, ports, or layers is exported in separate messages.</strong>

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
contain one populated symbol column. The reconstruction examples below use
complex NaN in unobserved positions, not a measured zero channel response.

## Capture container and notebook decoding

The `.bin` file adds framing and receive-time metadata around each unchanged
ZeroMQ message.

```text
16-byte file header:
  8-byte magic:             b"ISACRAW\0"
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

The snippets below are notebook examples, not additional CLI scripts. The
raw recorder never changes the ZMQ message; this reader validates framing and
decodes its fields for analysis.

### Read the binary capture

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

            if frame_magic != FRAME_MAGIC or frame_version != 2 or kind not in (CSI, EQ):
                raise ValueError(f"unsupported ISAC frame at record {record_index}")
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

### Separate frames by kind

Both frame kinds can be separated while reading the same file:

```python
frames_by_kind = {CSI: [], EQ: []}

for frame in read_capture("utils/isac/isac_capture_2hz.bin"):
    if frame["kind"] in frames_by_kind:
        frames_by_kind[frame["kind"]].append(frame)

csi_frames = frames_by_kind[CSI]
eq_frames = frames_by_kind[EQ]

print(f"CSI frames: {len(csi_frames)}")
print(f"EQ frames:  {len(eq_frames)}")
```

For large recordings, iterate over `read_capture` and process each kind
incrementally instead of retaining every decoded payload in a list.

### Select a UE and CSI path

When multiple paths are exported, do not append their coefficients into one
Doppler time series. Select a UE and path first:

```python
selected_rnti, selected_rx_port, selected_tx_layer = 0x4601, 0, 0

selected_csi = [
    frame for frame in csi_frames
    if (frame["rnti"], frame["rx_port"], frame["tx_layer"])
    == (selected_rnti, selected_rx_port, selected_tx_layer)
]
```

### Recover EQ layers from the interleaved payload

Each EQ message belongs to one UE/RNTI and contains the first OFDM symbol
with data in that PUSCH. Unlike CSI, it combines the equalized **TX layers in
one message**; contributions from the RX ports have already been combined
by the equalizer. There is no separate EQ vector for each RX port.

The payload is RE-major, layer-interleaved. For two layers its order is:

```text
RE0/L0, RE0/L1, RE1/L0, RE1/L1, RE2/L0, RE2/L1, ...
```

The built-in interleaving rule is
`out[nof_layers * re_index + layer_index] = layer_value[re_index]`.
For a decoded EQ frame from the reader above, reverse that ordering with NumPy's
default C-order reshape:

```python
eq_frame = eq_frames[0]  # Select one UE/PUSCH's EQ message.
iq = eq_frame["iq"]
nof_layers = 2  # Actual TX-layer count for THIS PUSCH, known independently.

if nof_layers < 1 or iq.size % nof_layers:
    raise ValueError("EQ vector cannot be split into this number of layers")

eq_re_by_layer = iq.reshape(-1, nof_layers)  # Shape: [data RE, TX layer]
eq_by_layer = eq_re_by_layer.T              # Shape: [TX layer, data RE]
layer_0 = eq_by_layer[0]
```

For one active layer, this reduces to `iq.reshape(-1, 1)` (or its transpose
for a `1 × RE` array). A three-layer example with three data REs is:

```python
iq = np.array([1, 11, 21, 2, 12, 22, 3, 13, 23], dtype=np.complex64)
eq_by_layer = iq.reshape(-1, 3).T
# Rows are layers; columns are successive exported data REs:
# [[ 1,  2,  3],
#  [11, 12, 13],
#  [21, 22, 23]]
```

The current EQ header **does not export the actual layer count**. Its
`tx_layer` and `rx_port` fields are placeholders, not dimensions. Neither
`pusch.max_rank` nor `isac.nof_tx_layers` proves how many layers a particular
PUSCH used, and divisibility alone cannot identify that count. Use the actual
per-PUSCH layer count or a controlled experiment in which it is known.

The existing EQ export cap is **3300 complex samples in total**, not per layer.
Reshaping separates only the retained prefix; it cannot restore truncated
samples. Here, RE means a data resource element from that one exported OFDM
symbol, not an OFDM-symbol time index or necessarily every carrier subcarrier.
The EQ header's `symbol` and `rb_start` are also placeholders, so this reshape
does not reconstruct an absolute SC × OFDM-symbol resource grid.

## CSI resource-grid reconstruction

The file must be a valid ISACRAW capture containing CSI v2 messages. A normal
capture containing **both CSI and EQ is accepted**: EQ is counted and excluded
from the grids, but its slot metadata also helps track the common timeline.
Empty files, EQ-only captures, malformed headers, unsupported kinds/versions,
inconsistent payload lengths and out-of-range slot counters are rejected.

The output is `<capture-stem>_csi_grid.npz` beside the input, which is never
changed. Existing outputs are not overwritten. All output streams share the
minimum-to-maximum observed SC and extended-slot bounds. Each distinct
`(rnti, rx_port, tx_layer, symbol)` has its own **complex64 SC × slot grid**:

- Rows cover every SC between the smallest `rb_start × 12` and the largest
  `rb_start × 12 + count`, with the upper bound excluded.
- Columns cover every radio slot between the earliest and latest CSI
  observations, including slots with no CSI. This is not an OFDM-symbol axis.
- Arrays start at complex NaN (`nan + nan*j`); actual IQ values from CSI packets
  replace these placeholders at their positions. A same-shaped Boolean `valid`
  array marks measured entries, including genuinely measured numerical zeros.
- Missing slots, inactive layers and unallocated SCs remain NaN with `valid=False`.
  No interpolation, extrapolation, averaging or Doppler processing is applied.
- Different reported OFDM-symbol indices remain separate streams rather than
  being collapsed into one slot observation. Overlapping packets for the same
  stream/slot/SC are rejected instead of silently overwriting measurements.

Dense reconstruction can be much larger than the packet file. Paths are
written one at a time, with a 1 GiB limit per dense grid plus validity mask;
use shorter recordings if this bound is exceeded. Results are published only
after reconstruction succeeds, so an error does not leave a partial `.npz`.

### Read the generated grids

The `.npz` contains ordinary NumPy arrays; loading requires no pickle:

```python
import numpy as np

with np.load("utils/isac/isac_capture_2hz_csi_grid.npz", allow_pickle=False) as data:
    # Row i labels the arrays named stream_<i>_*.
    # Columns: RNTI, RX port, TX layer, reported OFDM symbol.
    keys = data["stream_keys"]
    for i, (rnti, rx_port, tx_layer, symbol) in enumerate(keys):
        print(i, hex(int(rnti)), int(rx_port), int(tx_layer), int(symbol))

    i = 0  # Select the desired row/path from the listing above.
    prefix = f"stream_{i:04d}"
    csi = data[f"{prefix}_csi"]         # complex64 [SC, extended slot]
    valid = data[f"{prefix}_valid"]     # bool, same shape as csi
    sc_indices = data["sc_indices"]    # Absolute SC index for each row
    slots = data["slots"]              # Extended slot for each column
    raw_slots = data["raw_slots"]      # slots % slot_modulus
    time_s = data["time_s"]            # Relative radio time, first column = 0

    # An empty column means no CSI for this stream, not a measured zero channel.
    observed_slots = slots[valid.any(axis=0)]
    print(csi.shape, observed_slots)

    # Optional: select finite measured values for one SC, keeping their times.
    row = 0
    keep = valid[row] & np.isfinite(csi[row])
    measured_csi = csi[row, keep]
    measured_time_s = time_s[keep]
```

Archive metadata also includes `format_version` (grid archive version 1,
independent of capture/wire versions), `scs_khz`, `slots_per_frame`,
`slot_modulus` and `slot_duration_s`. Per-stream `record_indices`,
`packet_slots`, `rb_start` and `count` arrays describe each contributing CSI
packet in slot/record-index order, retaining the link to the raw capture.

### A per-slot `SC × OFDM-symbol` grid is a different view

Do not group an entire recording by raw `slot`: its value repeats every
10.24 s. For notebook use with the capture reader above, unwrap the common
timeline first, under the same assumptions as the utility:

```python
from collections import defaultdict
import sys

sys.path.insert(0, "utils/isac")  # When running from the repository root.
from isac_csi_grid import SlotUnwrapper

scs_khz = 15
unwrapper = SlotUnwrapper(scs_khz)
groups = defaultdict(list)
for frame in read_capture("utils/isac/isac_capture_2hz.bin"):
    if frame["scs_khz"] != scs_khz:
        raise ValueError("SCS differs from the selected numerology")
    extended_slot = unwrapper.unwrap(frame["slot"])
    if frame["kind"] == CSI:
        key = (extended_slot, frame["rnti"], frame["rx_port"], frame["tx_layer"])
        groups[key].append(frame)

for key, frames_for_grid in groups.items():
    min_sc = min(f["rb_start"] * 12 for f in frames_for_grid)
    max_sc = max(f["rb_start"] * 12 + f["count"] for f in frames_for_grid)
    grid = np.full((max_sc - min_sc, 14), complex(np.nan, np.nan), dtype=np.complex64)
    valid = np.zeros(grid.shape, dtype=bool)
    for frame in frames_for_grid:
        start = frame["rb_start"] * 12 - min_sc
        stop = start + frame["count"]
        symbol = frame["symbol"]
        if not 0 <= symbol < 14:
            raise ValueError("OFDM symbol index is outside the grid")
        if valid[start:stop, symbol].any():
            raise ValueError("overlapping CSI; refusing to overwrite")
        grid[start:stop, symbol] = frame["iq"]
        valid[start:stop, symbol] = True
    print(key, grid.shape)
```

Here the columns are actual OFDM-symbol indices `0..13` within one slot
(normal CP), not successive CSI observations. The usual capture populates
only the first selected DMRS-symbol column; other columns remain invalid.
For slow-time sensing across slots, use the SC × slot grids above instead.

## Operator assumptions and responsibilities

The tap provides UE/path identifiers and slot/numerology metadata for external
MIMO and TDD-aware processing. It does not prescribe a sensing algorithm,
schedule continuous traffic, identify missing-slot causes or estimate position.

### NaNs, validity masks and quality control

Missing entries have NaN in both real and imaginary components. Ordinary FFTs
will propagate those NaNs, prompting the operator to choose gap handling
explicitly. The validity mask is retained: it describes presence in the capture,
not signal quality; a NaN actually received in a CSI packet is preserved with
`valid=True`. Dropping missing entries can leave an irregular time axis, so keep
the corresponding timestamps rather than treating the remaining values as
uniformly spaced. Interpolation remains an external processing choice.

Selecting finite samples, as shown above, is optional. Preserve the associated
`time_s`/`slots` whenever samples are dropped. Gap limits, interpolation method,
clutter removal, calibration and quality rejection are operator decisions;
none is applied automatically by the grid generator.

### TDD pattern and sampling-rate knowledge

The operator must know the **actual DL/UL/special-slot configuration**, including
its alignment to the radio slot indices. The packet does not carry that
pattern or grant history. An UL-capable slot does not guarantee PUSCH for a
particular UE, and offered application traffic alone does not guarantee
continuous scheduling.

For example, at 15 kHz, CSI at extended slots `[0, 2, 8]` produces columns
`0..8`, populated at `0`, `2`, `8`. Other columns stay invalid. An external
processor with a known alternating TDD pattern could choose the UL-only axis
`[0, 2, 4, 6, 8]` and consider interpolating only missing UL observations at
`4` and `6`. The grid utility does not guess the TDD pattern or why a slot is
missing. Slot gaps alone cannot distinguish DL, no scheduled PUSCH, or lost
export messages. Handle missing NaNs explicitly before a sensing FFT; replacing
them with zeros is itself a processing choice, not a recovered measurement.

This even/odd example illustrates sampling only; it is not a proposed NR TDD
configuration. Real configurations must account for special slots, guard time
and where PUSCH is scheduled. Apply the pattern from the gNB configuration,
rather than inferring DL slots simply from NaNs.

A complete, uniformly alternating UL-only series at 15 kHz has a 2 ms interval
(500 observations/s); it need not be interpolated onto the intervening DL slots
to perform an FFT. Burst patterns or additional missing UL observations can
make the series irregular. The live GNU Radio blocks do not resolve this.
Choose an appropriate uniform reconstruction or irregular-sampling method
externally. FFT bin spacing is `fs/N = 1/T` for a uniform N-sample observation
window; interpolation does not create additional measured sensing information.

### Session boundaries, slot bounds and counter unwrapping

The wire `slot` equals `SFN × slots_per_frame + slot_index_in_frame`, not just
the slot index inside one frame. SFN cycles after 1024 radio frames:

| SCS (kHz) | Slots/frame | Slot duration (ms) | Wire slot range | Modulus |
|---:|---:|---:|---:|---:|
| 15 | 10 | 1 | 0–10239 | 10240 |
| 30 | 20 | 0.5 | 0–20479 | 20480 |
| 60 | 40 | 0.25 | 0–40959 | 40960 |
| 120 | 80 | 0.125 | 0–81919 | 81920 |
| 240 | 160 | 0.0625 | 0–163839 | 163840 |

All five counters wrap every **10.24 seconds**. At 15 kHz, an ordered sequence
`10236, 10238, 2, 8` becomes `10236, 10238, 10242, 10248`.

The utility assigns each packet to the nearest counter cycle of the latest
extended-slot high-water mark, then sorts packets onto the grid. Small
backward steps such as `100, 102, 101, 103` are treated as late packets, not
wraps. Repeated slots for separate MIMO paths share the same extended slot.
Unwrapping is performed across all paths and both message kinds before
grouping, so an inactive layer does not maintain its own stale cycle counter.

This requires a **single cell/radio timeline with no restart**, forward gaps
between received observations shorter than **5.12 s**, and out-of-order
delivery less than 5.12 s behind the high-water mark. Exactly half-cycle jumps
are rejected. Longer gaps and restarts can be indistinguishable from other
valid slot sequences and cannot always be detected: selecting SCS alone does
not remove that ambiguity. Use separate recordings for separate sessions;
the current packet has no session ID or unbounded radio timestamp.

The first packet anchors an arbitrary cycle, not an absolute network epoch.
A late packet from before that first packet can have a negative extended
slot. Receiver wall/monotonic arrival clocks are not used to infer radio time.

RNTI is a temporary cell-local identifier, not a permanent subscriber ID.
Reconnection/reassignment can change or reuse it. The packet has no cell,
session or persistent UE identifier; keep separate source/session context
rather than merging unrelated recordings with the same RNTI.

### Contiguous-frequency mapping

The current CSI header contains `rb_start` and `count`, but not a complete
subcarrier/PRB mask. The grid tool and notebook examples assume that the
packed vector is contiguous, beginning at `rb_start × 12`. This is appropriate
for the ordinary contiguous `vrb_interval`, non-interleaved PUSCH scheduler
path in this repository. A 100% maximum-PRB policy permits full-band grants
but does not guarantee one in every slot.

If a future configuration uses non-contiguous allocation, the wire format
must add an RB/RE mask or explicit indices for exact placement. The current
header cannot verify contiguity; keep the original capture as the source data.

### Actual EQ layer count

Use the actual layer count for each UE's PUSCH when applying the
[EQ reshape](#recover-eq-layers-from-the-interleaved-payload). Configured maximum
rank and CSI capture counts are not sufficient if the active rank changes.
The current EQ header does not supply that information, so a fixed-rank
experiment or another source of per-PUSCH rank information is required.

### Queueing, overhead and capture completeness

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

## Planned development

Two architectural improvements remain planned: isolating the publisher worker
and migrating the integrated prototype into an independent add-on plugin.

### Dedicated ISAC publisher worker

**Status: planned, not implemented.** No independent gNB ISAC publisher worker
was added in the current implementation. Serialization and ZMQ publication are
already deferred off the PHY path, but the sink still uses
`workers.get_metrics_executor()`. The recorder's existing disk-writer thread
is receiver-side and does not remove this publisher-side dependency.

The proposed change is an **independent ISAC worker/executor with its own
bounded queue**, with the following intended advantages:

- Reduce mutual queue contention: unrelated metrics should be less able to
  delay or cause rejection of ISAC work, while high-rate ISAC export should
  interfere less with metrics processing.
- Give ISAC its own queue capacity and independently tunable worker resources,
  especially as UE, RX-port and layer counts increase the export load.
- Provide export-specific backlog and rejection statistics so operators can
  distinguish publisher congestion from missing observations more easily.

Implementation must retain non-blocking submission from the PHY path, owned
capture buffers and per-UE/path metadata, and define worker startup, drain and
shutdown behavior. These are expected isolation and observability benefits,
not measured performance improvements. A dedicated worker will not eliminate
PHY-side copying, shared CPU/memory/network contention, or ZMQ PUB losses, and
will not by itself make capture lossless.

### Independent ISAC tap plugin

**Status: planned, not implemented.** The current ISAC tap is implemented as
additions and modifications within this OCUDU fork; it has not yet been
packaged as an independently maintained `isac_tap_plugin`.

The migration should:

- Move the concrete ISAC capture/export implementation, transport,
  plugin-specific configuration and companion utilities into a separate plugin
  repository with its own build configuration and optional OCUDU attachment.
- Preserve the dependency direction: the plugin depends on OCUDU interfaces
  and libraries, while OCUDU's normal build must not depend on the plugin's
  implementation, headers, wire format or transport-specific dependencies.
- Use, or agree with upstream, generic post-estimation/post-equalization
  observer hooks and factory/configuration extension points. Packaging alone
  does not supply missing host interfaces; any required generic integration
  must be distinguished from plugin-specific implementation.
- Remain a distinct ISAC-focused plugin, not a dependency on or fallback to
  `phy_tap_plugin_example`, while retaining per-UE/path CSI, EQ export and the
  downstream analysis workflow.

The intended advantages are clearer ownership and separation of concerns,
optional ISAC-specific dependencies, and less fork-specific integration work
when updating OCUDU. Plugin packaging and the dedicated publisher worker are
separate tasks: completing either one does not automatically complete the other.
