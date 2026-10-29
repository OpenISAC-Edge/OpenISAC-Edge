"""Reconstruct subcarrier x radio-slot CSI grids from a closed ISACRAW capture.

Only NumPy is required; this offline utility does not import GNU Radio or ZMQ.
The input remains unchanged. Mixed CSI/EQ captures are accepted, but only CSI
is placed in the output. Missing measurements are complex NaN with valid=False.

Slot unwrapping assumes a single uninterrupted radio timeline: successive
observations and out-of-order delivery must be less than half the 10.24-second
counter cycle apart. Neither SCS nor the current wire header can resolve an
unobserved whole cycle or a gNB restart. Arrival timestamps are not used.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import os
from pathlib import Path
import struct
import sys
import tempfile
from typing import BinaryIO
import zipfile

import numpy as np


FILE_HEADER = struct.Struct("<8sHHI")
RECORD_HEADER = struct.Struct("<QQQI")
FRAME_HEADER = struct.Struct("<IHBHIHBBBHH")
VALID_SCS = (15, 30, 60, 120, 240)  # Local OCUDU subcarrier_spacing enum.
CSI, EQ = 0, 1
IQ_DTYPE = np.dtype("<c8")
# Bound each dense CSI + validity allocation; paths are written one at a time.
MAX_GRID_BYTES = 1024**3


@dataclass(frozen=True)
class CSIPacket:
    record_index: int
    slot: int  # Unwrapped, on the common timeline before separating paths.
    rnti: int
    rx_port: int
    tx_layer: int
    symbol: int
    rb_start: int
    count: int
    payload_offset: int

    @property
    def key(self) -> tuple[int, int, int, int]:
        return self.rnti, self.rx_port, self.tx_layer, self.symbol


class SlotUnwrapper:
    """Map to the nearest cycle of a high-water mark, not the preceding packet.

    Small backward steps are late packets, not new cycles. The high-water mark
    never retreats, including when a late packet belongs before a wrap. The
    first packet anchors an arbitrary epoch; a subsequently received earlier
    packet can therefore have a negative extended slot.
    """

    def __init__(self, scs: int):
        if scs not in VALID_SCS:
            raise ValueError(f"unsupported SCS: {scs}")
        self.modulus = 1024 * 10 * (scs // 15)
        self.high_water: int | None = None

    def unwrap(self, slot: int) -> int:
        if not 0 <= slot < self.modulus:
            raise ValueError(f"slot {slot} is outside 0..{self.modulus - 1}")
        if self.high_water is None:
            self.high_water = slot
            return slot
        forward = (slot - self.high_water % self.modulus) % self.modulus
        if forward == self.modulus // 2:
            raise ValueError("ambiguous half-cycle slot jump; split this capture")
        delta = forward if forward < self.modulus // 2 else forward - self.modulus
        extended = self.high_water + delta
        self.high_water = max(self.high_water, extended)
        return extended


def read_exact(stream: BinaryIO, size: int) -> bytes:
    raw = stream.read(size)
    if len(raw) != size:
        raise ValueError(f"truncated capture: expected {size} bytes, got {len(raw)}")
    return raw


def index_capture(stream: BinaryIO, scs: int) -> tuple[list[CSIPacket], int]:
    """Validate every frame, index CSI payloads, and skip EQ without loading IQ."""

    file_size = os.fstat(stream.fileno()).st_size
    magic, version, header_size, _ = FILE_HEADER.unpack(read_exact(stream, FILE_HEADER.size))
    if magic != b"ISACRAW\0" or version != 1 or header_size != RECORD_HEADER.size:
        raise ValueError("not a supported ISACRAW v1 packet capture")
    unwrapper = SlotUnwrapper(scs)
    packets: list[CSIPacket] = []
    eq_count = 0
    previous_index = -1
    while stream.tell() < file_size:
        _, _, index, size = RECORD_HEADER.unpack(read_exact(stream, RECORD_HEADER.size))
        if index <= previous_index:
            raise ValueError(f"record {index}: capture record indices are not increasing")
        previous_index = index
        if size < FRAME_HEADER.size or stream.tell() + size > file_size:
            raise ValueError(f"record {index}: invalid or truncated ISAC message length")
        (
            frame_magic, frame_version, kind, wire_scs, slot, rnti,
            symbol, rx_port, tx_layer, rb_start, count,
        ) = FRAME_HEADER.unpack(read_exact(stream, FRAME_HEADER.size))
        if frame_magic != 0x49534143 or frame_version != 2:
            raise ValueError(f"record {index}: expected an ISAC v2 frame")
        if kind not in (CSI, EQ):
            raise ValueError(f"record {index}: unsupported ISAC kind {kind}")
        if size != FRAME_HEADER.size + count * IQ_DTYPE.itemsize:
            raise ValueError(f"record {index}: sample count does not match message length")
        if wire_scs != scs:
            raise ValueError(
                f"record {index}: packet SCS is {wire_scs} kHz, not --scs {scs}; "
                "select the matching SCS (mixed-numerology captures are unsupported)"
            )
        try:
            extended_slot = unwrapper.unwrap(slot)
        except ValueError as exc:
            raise ValueError(f"record {index}: {exc}") from exc
        if kind == CSI:
            if not count or symbol >= 14:
                raise ValueError(f"record {index}: empty CSI vector or invalid OFDM symbol")
            packets.append(CSIPacket(
                index, extended_slot, rnti, rx_port, tx_layer, symbol,
                rb_start, count, stream.tell(),
            ))
        else:
            eq_count += 1
        stream.seek(count * IQ_DTYPE.itemsize, os.SEEK_CUR)
    if not packets:
        raise ValueError(f"capture contains no CSI frames ({eq_count} EQ frames)")
    return packets, eq_count


def _save_array(archive: zipfile.ZipFile, name: str, value: object) -> None:
    # A standard .npz is a ZIP of .npy arrays. Stream one array at a time rather
    # than retaining dense grids for every UE/port/layer simultaneously.
    with archive.open(f"{name}.npy", "w", force_zip64=True) as member:
        np.save(member, np.asarray(value), allow_pickle=False)


def write_grids(
    source: BinaryIO, output: Path, packets: list[CSIPacket], scs: int,
) -> tuple[int, int, int]:
    """Save common SC/slot axes and independent path/symbol grids to .npz."""

    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    source_stat = os.fstat(source.fileno())
    groups: dict[tuple[int, int, int, int], list[CSIPacket]] = defaultdict(list)
    for packet in packets:
        groups[packet.key].append(packet)
    keys = sorted(groups)
    min_sc = min(packet.rb_start * 12 for packet in packets)
    max_sc = max(packet.rb_start * 12 + packet.count for packet in packets)
    first_slot = min(packet.slot for packet in packets)
    last_slot = max(packet.slot for packet in packets)
    shape = (max_sc - min_sc, last_slot - first_slot + 1)
    needed = shape[0] * shape[1] * (IQ_DTYPE.itemsize + np.dtype(bool).itemsize)
    if needed > MAX_GRID_BYTES:
        raise ValueError(
            f"dense grid + validity mask needs {needed / 1024**3:.2f} GiB per stream "
            f"(limit {MAX_GRID_BYTES / 1024**3:.0f} GiB); use a shorter capture"
        )
    slots = np.arange(first_slot, last_slot + 1, dtype=np.int64)
    modulus = 1024 * 10 * (scs // 15)
    # Publish the result only after every stream succeeds. A hard link fails
    # rather than overwriting an output created by another process meanwhile.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp") as tmp:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            metadata = {
                "format_version": 1,
                "scs_khz": scs,
                "slots_per_frame": 10 * (scs // 15),
                "slot_modulus": modulus,
                "slot_duration_s": 0.001 / (scs // 15),
                "sc_indices": np.arange(min_sc, max_sc, dtype=np.int64),
                "slots": slots,
                "raw_slots": slots % modulus,
                "time_s": (slots - first_slot) * (0.001 / (scs // 15)),
                "stream_keys": np.asarray(keys, dtype=np.uint16),
            }
            for name, value in metadata.items():
                _save_array(archive, name, value)
            for i, key in enumerate(keys):
                ordered = sorted(groups[key], key=lambda p: (p.slot, p.record_index))
                grid = np.full(shape, complex(np.nan, np.nan), dtype=IQ_DTYPE)
                valid = np.zeros(shape, dtype=bool)
                for packet in ordered:
                    start = packet.rb_start * 12 - min_sc
                    stop = start + packet.count
                    column = packet.slot - first_slot
                    if valid[start:stop, column].any():
                        raise ValueError(
                            f"record {packet.record_index}: overlapping CSI for {key} "
                            f"at extended slot {packet.slot}; refusing to overwrite "
                            "measurements (duplicate PUSCH or ambiguous timeline)"
                        )
                    source.seek(packet.payload_offset)
                    iq = np.frombuffer(read_exact(source, packet.count * IQ_DTYPE.itemsize), dtype=IQ_DTYPE)
                    grid[start:stop, column] = iq
                    valid[start:stop, column] = True
                prefix = f"stream_{i:04d}"
                _save_array(archive, f"{prefix}_csi", grid)
                _save_array(archive, f"{prefix}_valid", valid)
                _save_array(archive, f"{prefix}_record_indices", np.array(
                    [p.record_index for p in ordered], dtype=np.uint64,
                ))
                _save_array(archive, f"{prefix}_packet_slots", np.array(
                    [p.slot for p in ordered], dtype=np.int64,
                ))
                _save_array(archive, f"{prefix}_rb_start", np.array(
                    [p.rb_start for p in ordered], dtype=np.uint16,
                ))
                _save_array(archive, f"{prefix}_count", np.array(
                    [p.count for p in ordered], dtype=np.uint16,
                ))
                del grid, valid
        tmp.flush()
        current_stat = os.fstat(source.fileno())
        if (source_stat.st_size, source_stat.st_mtime_ns) != (current_stat.st_size, current_stat.st_mtime_ns):
            raise ValueError("capture changed during reconstruction; stop the recorder first")
        os.link(tmp.name, output)
    return len(keys), shape[0], shape[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="closed .bin file from isac_packet_capture.py")
    parser.add_argument("--scs", type=int, choices=VALID_SCS, default=15,
                        help="SCS in kHz; must match packet headers (default: %(default)s)")
    args = parser.parse_args()
    output = args.file.with_name(f"{args.file.stem}_csi_grid.npz")
    print(
        "Note: assumes one radio timeline, forward gaps and reordering < 5.12 s; "
        "whole missed cycles and gNB restarts cannot be inferred from slot counters.",
        file=sys.stderr,
    )
    try:
        with args.file.open("rb") as source:
            before = os.fstat(source.fileno())
            packets, eq_count = index_capture(source, args.scs)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("capture changed while reading; stop the recorder first")
            streams, nsc, nslots = write_grids(source, output, packets, args.scs)
        print(f"Read {len(packets)} CSI frames; skipped {eq_count} EQ frames.")
        print(f"Saved {output}: {streams} streams, each {nsc} SC x {nslots} slots.")
        print("Missing entries are complex NaN with valid=False; no interpolation was applied.")
    except (OSError, ValueError, MemoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
